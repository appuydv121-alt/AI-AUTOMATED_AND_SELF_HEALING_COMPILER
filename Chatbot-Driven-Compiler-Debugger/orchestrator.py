"""orchestrator.py — Multi-Agent Supervisor & State Machine Orchestrator.

Enforces:
  1. Strict State Machine: Planner -> SecurityAudit -> EfficiencyProfile -> Optimizer -> TestGen -> Judge -> Critic.
  2. Stop Rules: max_cycles (default 3), patience (stop on 2 cycles of no progress), time budget.
  3. Rigorous Sandbox Judge: Compiles, runs across test matrix for equivalence, checks sanitizers, measures median runtime delta.
  4. Human Gate: Prepares patch diff + empirical evidence for explicit human review.
"""
from __future__ import annotations

import difflib
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import llm_provider as _llm
from agent_guard import build_guarded_prompt, wrap_code_data
from compiler_service import compile_and_run
from efficiency_agent import run_efficiency_profile
from memory_store import retrieve_similar_experiences
from secure_scan import compute_code_quality_metrics, run_security_guardrail
from security_agent import run_security_audit
from test_generator_agent import generate_test_matrix
from tool_runner import collect_empirical_evidence, measure_execution_profile, run_sanitizer_analysis


@dataclass
class TransitionLog:
    state_from: str
    state_to: str
    cycle: int
    reason: str
    elapsed_ms: float = 0.0


@dataclass
class OrchestratorResult:
    success: bool
    original_code: str
    optimized_code: str
    language: str
    diff_unified: str
    verification_status: str  # e.g., "Verified & Improved", "Rejected", "No Progress"
    changes_summary: list[str] = field(default_factory=list)
    state_transitions: list[dict[str, Any]] = field(default_factory=list)
    agent_trace: list[dict[str, Any]] = field(default_factory=list)
    evidence_report: dict[str, Any] = field(default_factory=dict)
    metrics_before: dict[str, Any] = field(default_factory=dict)
    metrics_after: dict[str, Any] = field(default_factory=dict)
    tests_evaluated: int = 0
    tests_passed: int = 0
    cycles_taken: int = 1
    total_time_s: float = 0.0
    active_provider: str = ""
    active_model: str = ""


def _extract_code_block(raw: str, language: str = "cpp") -> str:
    """Extract the most complete code block from an LLM response."""
    if not raw or not raw.strip():
        return ""
    text = raw.strip()

    lang_tags = [
        language.lower(),
        "cpp",
        "c++",
        "c",
        "python",
        "py",
        "javascript",
        "js",
        "typescript",
        "ts",
        "sql",
    ]
    language_pattern = "|".join(re.escape(tag) for tag in lang_tags)
    pattern = rf"```(?:{language_pattern})?\s*([\s\S]*?)```"
    matches = re.findall(pattern, text, re.IGNORECASE)
    if matches:
        longest = max(matches, key=len).strip()
        if longest:
            return longest

    generic_matches = re.findall(r"```\s*([\s\S]*?)```", text)
    if generic_matches:
        longest = max(generic_matches, key=len).strip()
        if longest:
            return longest

    lines = text.splitlines()
    code_lines = [
        line
        for line in lines
        if not line.strip().startswith(("Here is", "Here's", "Sure,", "Note:", "I have"))
    ]
    return "\n".join(code_lines).strip()


def _compare_test_case(reference_run, candidate_run, test_name: str) -> tuple[bool, str]:
    if getattr(reference_run, "timed_out", False) or reference_run[0] == -1:
        return False, f"Reference program timed out on '{test_name}'; equivalence cannot be verified."
    if reference_run[0] != 0:
        return False, (
            f"Reference program did not complete successfully on '{test_name}' "
            f"(status {reference_run[0]}); equivalence cannot be verified."
        )
    if getattr(reference_run, "output_limited", False):
        return False, f"Reference output exceeded the capture limit on '{test_name}'; equivalence cannot be verified."
    if getattr(candidate_run, "timed_out", False) or candidate_run[0] != 0:
        return False, (
            f"Candidate did not complete successfully on '{test_name}' "
            f"(status {candidate_run[0]}): {candidate_run[2].strip()}"
        )
    if getattr(candidate_run, "output_limited", False):
        return False, f"Candidate output exceeded the capture limit on '{test_name}'; equivalence cannot be verified."
    if reference_run[1] != candidate_run[1]:
        return False, (
            f"Output discrepancy on test '{test_name}':\n"
            f"Expected: {reference_run[1]!r}\nGot: {candidate_run[1]!r}"
        )
    return True, ""


def _quality_does_not_regress(candidate: dict[str, Any], reference: dict[str, Any]) -> bool:
    return (
        candidate.get("security_score", 0.0) >= reference.get("security_score", 0.0)
        and candidate.get("efficiency_score", 0.0) >= reference.get("efficiency_score", 0.0)
    )


class OrchestratorStateMachine:
    """Supervisor state machine managing multi-agent code optimization and hardening."""

    def __init__(
        self,
        code: str,
        language: str = "cpp",
        baseline_stdin: str = "",
        max_cycles: int = 3,
        patience: int = 2,
        time_budget_s: float = 120.0,
        provider_name: str | None = None,
        ollama_model: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.code = code
        self.language = language.lower()
        self.baseline_stdin = baseline_stdin
        self.max_cycles = max_cycles
        self.patience = patience
        self.time_budget_s = time_budget_s
        self.provider_name = provider_name
        self.ollama_model = ollama_model
        self.progress_callback = progress_callback

        self.start_time = time.monotonic()
        self.current_state = "INIT"
        self.transitions: list[TransitionLog] = []
        self.trace: list[dict[str, Any]] = []

        self.best_candidate = code
        self.no_progress_count = 0

    def _transition(self, next_state: str, cycle: int, reason: str):
        t_el = (time.monotonic() - self.start_time) * 1000.0
        log_entry = TransitionLog(
            state_from=self.current_state,
            state_to=next_state,
            cycle=cycle,
            reason=reason,
            elapsed_ms=round(t_el, 2),
        )
        self.transitions.append(log_entry)
        self.current_state = next_state
        if self.progress_callback:
            try:
                self.progress_callback({"stage": "state_transition", **asdict(log_entry)})
            except Exception:
                pass

    def _log_trace(self, agent: str, action: str, status: str, details: str, elapsed_ms: float = 0.0):
        entry = {
            "agent": agent,
            "action": action,
            "status": status,
            "details": details,
            "elapsed_ms": round(elapsed_ms, 2),
        }
        self.trace.append(entry)
        if self.progress_callback:
            try:
                self.progress_callback({"stage": "agent_trace", **entry})
            except Exception:
                pass

    def _apply_deterministic_hardening(self, src: str) -> str:
        """Deterministic algorithmic optimization & hardening for maximum efficiency and security."""
        if self.language in ("cpp", "c++", "c"):
            out = src

            # 1. Replace Bubble Sort / Selection Sort O(N^2) loops with std::sort (O(N log N))
            loop_start_pat = re.compile(r"\bfor\s*\(", re.MULTILINE)
            for m in list(loop_start_pat.finditer(out)):
                paren_start = m.end() - 1
                paren_count = 1
                j = paren_start + 1
                while j < len(out) and paren_count > 0:
                    if out[j] == '(':
                        paren_count += 1
                    elif out[j] == ')':
                        paren_count -= 1
                    j += 1
                if paren_count != 0:
                    continue
                brace_start = out.find('{', j)
                if brace_start == -1 or brace_start > j + 15:
                    continue
                brace_count = 1
                k = brace_start + 1
                while k < len(out) and brace_count > 0:
                    if out[k] == '{':
                        brace_count += 1
                    elif out[k] == '}':
                        brace_count -= 1
                    k += 1
                if brace_count == 0:
                    loop_full = out[m.start():k]
                    # Check if this loop performs adjacent element or nested swaps
                    swap_m = re.search(r"\bswap\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\[[^\]]+\]\s*,\s*\1\[[^\]]+\]\s*\)", loop_full)
                    if swap_m and ("for " in loop_full[len(m.group(0)):] or "while " in loop_full[len(m.group(0)):]):
                        vec_name = swap_m.group(1)
                        is_vec = bool(re.search(rf"\bvector<[^>]+>\s+{vec_name}\b", out))
                        if is_vec:
                            rep = f"sort({vec_name}.begin(), {vec_name}.end());"
                        else:
                            rep = f"sort({vec_name}, {vec_name} + n);"
                        out = out[:m.start()] + rep + out[k:]
                        break

            # 2. Hoist redundant sort inside loop outside
            for m in list(loop_start_pat.finditer(out)):
                paren_start = m.end() - 1
                paren_count = 1
                j = paren_start + 1
                while j < len(out) and paren_count > 0:
                    if out[j] == '(':
                        paren_count += 1
                    elif out[j] == ')':
                        paren_count -= 1
                    j += 1
                if paren_count != 0:
                    continue
                brace_start = out.find('{', j)
                if brace_start == -1 or brace_start > j + 15:
                    continue
                brace_count = 1
                k = brace_start + 1
                while k < len(out) and brace_count > 0:
                    if out[k] == '{':
                        brace_count += 1
                    elif out[k] == '}':
                        brace_count -= 1
                    k += 1
                if brace_count == 0:
                    loop_full = out[m.start():k]
                    sort_m = re.search(r"(\s*(?:sort|std::sort)\s*\([^;]*\);)", loop_full)
                    if sort_m:
                        sort_stmt = sort_m.group(1).strip()
                        new_loop = loop_full[:sort_m.start()] + loop_full[sort_m.end():]
                        out = out[:m.start()] + sort_stmt + "\n    " + new_loop + out[k:]
                        break

            # 3. Ensure <algorithm> is included if sort is present
            if "sort(" in out and "<algorithm>" not in out:
                if "#include <iostream>" in out:
                    out = re.sub(r"(#include\s*<iostream>)", r"\1\n#include <algorithm>", out, count=1)
                else:
                    out = "#include <algorithm>\n" + out

            return out
        return src

    def run(self) -> OrchestratorResult:
        # 1. INIT & EMPIRICAL EVIDENCE COLLECTION
        t0 = time.monotonic()
        self._transition("COLLECT_TOOLS", cycle=0, reason="Gathering tool findings & baseline profile")
        
        baseline_evidence = collect_empirical_evidence(self.code, language=self.language, stdin_input=self.baseline_stdin)
        base_quality = compute_code_quality_metrics(self.code, language=self.language)
        base_sec = base_quality.get("security_score", 0.0)
        base_eff = base_quality.get("efficiency_score", 0.0)

        self._log_trace(
            "ToolRunner",
            "Collected empirical tool findings and baseline profile",
            "info",
            f"Static/Sanitizer Flaws: {baseline_evidence.get('total_flaws_found', 0)}, "
            f"Baseline Runtime: {baseline_evidence.get('profile_metrics', {}).get('median_time_ms', 0)} ms. "
            f"Security: {base_sec:.0f}%, Efficiency: {base_eff:.0f}%.",
            elapsed_ms=(time.monotonic() - t0) * 1000.0,
        )

        # 2. RETRIEVE PAST EXPERIENCE FROM MEMORY
        t_mem = time.monotonic()
        self._transition("RETRIEVE_MEMORY", cycle=0, reason="Querying historical fixes from MemoryStore")
        flaw_names = [f.get("message", "") for f in baseline_evidence.get("tool_findings", [])]
        similar_cases = retrieve_similar_experiences(self.language, flaw_names, limit=2)
        memory_notes = ""
        if similar_cases:
            memory_notes = "Similar Successful Historical Fixes:\n" + "\n".join(
                f"- Flaw: {sc.get('flaw_summary')} | Gain: +{sc.get('runtime_gain_pct', 0)}%"
                for sc in similar_cases
            )
            self._log_trace("MemoryStore", "Retrieved relevant historical fixes", "success", memory_notes, elapsed_ms=(time.monotonic() - t_mem) * 1000.0)
        else:
            self._log_trace("MemoryStore", "No historical experience matched", "info", "Starting exploration without prior history.", elapsed_ms=(time.monotonic() - t_mem) * 1000.0)

        # 3. MULTI-AGENT REFINEMENT CYCLES
        candidate = self.code
        critic_guidance = ""
        tests_evaluated = 0
        tests_passed = 0
        best_tests_evaluated = 0
        best_tests_passed = 0
        best_candidate_metrics = base_quality
        best_candidate_verified = False
        active_prov = ""
        active_mdl = ""
        mismatch_error = ""

        for cycle in range(1, self.max_cycles + 1):
            cycle_start = time.monotonic()

            # Budget check
            if time.monotonic() - self.start_time > self.time_budget_s:
                self._transition("ABORTED", cycle=cycle, reason="Time budget exhausted")
                break

            # 3a. Security Audit Agent
            t_sec = time.monotonic()
            self._transition("SECURITY_AUDIT", cycle=cycle, reason="Auditing security vulnerabilities")
            sec_findings = run_security_audit(
                candidate,
                baseline_evidence,
                language=self.language,
                provider_name=self.provider_name,
                ollama_model=self.ollama_model,
            )
            self._log_trace(
                "SecurityAuditAgent",
                f"Security audit completed (Cycle {cycle})",
                "warning" if sec_findings else "success",
                f"Identified {len(sec_findings)} security/memory finding(s)." + ("\n" + "\n".join(f"• [{f.cwe}] {f.evidence}" for f in sec_findings[:3]) if sec_findings else ""),
                elapsed_ms=(time.monotonic() - t_sec) * 1000.0,
            )

            # 3b. Efficiency Profiler Agent
            t_eff = time.monotonic()
            self._transition("EFFICIENCY_PROFILE", cycle=cycle, reason="Profiling algorithmic complexity")
            eff_result = run_efficiency_profile(
                candidate,
                baseline_evidence,
                language=self.language,
                provider_name=self.provider_name,
                ollama_model=self.ollama_model,
            )
            profile_metrics = baseline_evidence.get("profile_metrics", {})
            self._log_trace(
                "EfficiencyProfilerAgent",
                f"Efficiency profile completed (Cycle {cycle})",
                "info",
                f"Estimated Big-O: {eff_result.est_big_o}. Hotspots identified: {len(eff_result.hotspots)}.",
                elapsed_ms=(time.monotonic() - t_eff) * 1000.0,
            )

            # 3c. Optimizer Agent (Synthesis)
            t_opt = time.monotonic()
            self._transition("OPTIMIZE", cycle=cycle, reason="Synthesizing hardened & minimal code patch")
            
            optimizer_prompt = (
                f"You are the OPTIMIZER agent. Improve the algorithmic and measured runtime efficiency of this "
                f"{self.language.upper()} program while preserving exact functional behavior. Make only changes "
                "supported by the supplied code or profiling evidence; do not make cosmetic rewrites.\n\n"
                f"SECURITY FINDINGS TO FIX:\n"
                + ("\n".join(f"- {f.cwe}: {f.evidence}. Minimal fix: {f.minimal_fix}" for f in sec_findings) if sec_findings else "(None)")
                + f"\n\nEFFICIENCY HOTSPOTS TO FIX:\n"
                + ("\n".join(f"- {h.complexity}: {h.reason}. Suggested change: {h.suggested_change}" for h in eff_result.hotspots) if eff_result.hotspots else "(None)")
                + f"\n\nPROFILE EVIDENCE:\n"
                + f"- Median runtime: {profile_metrics.get('median_time_ms', 'unavailable')} ms\n"
                + f"- Runs: {profile_metrics.get('runs_count', 'unavailable')}\n"
                + f"- Peak memory: {profile_metrics.get('peak_memory_kb', 'unavailable')} KB\n"
                + f"- Estimated time complexity: {eff_result.est_big_o}\n"
                + (f"\n\nCRITIC REFINEMENT GUIDANCE:\n{critic_guidance}" if critic_guidance else "")
                + (f"\n\n{memory_notes}" if memory_notes else "")
                + f"\n\nRULES:\n"
                f"1. Return ONLY the complete corrected program inside a single ```{self.language} code block.\n"
                f"2. Preserve observable behavior, input/output format, ordering, and all required outputs.\n"
                f"3. Check for loop-invariant work and closed-form reductions of fixed-bound arithmetic loops; "
                f"prefer proven reductions in algorithmic complexity or measured work over style changes. "
                f"Do not add fast-I/O boilerplate or replace std::endl unless the profile shows I/O is a "
                f"bottleneck and changing flush behavior is safe.\n"
                f"4. Do not remove outputs, narrow loop domains, or claim a speedup without evidence. If no safe, "
                f"meaningful optimization is supported, return the original program unchanged.\n\n"
                f"SOURCE CODE:\n{wrap_code_data(candidate, language=self.language)}"
            )

            optimizer_request = {
                "cache_hit": False,
                "request_started": False,
                "tokens": None,
            }

            def track_optimizer_request(event: dict[str, Any]) -> None:
                if event.get("stage") == "cached":
                    optimizer_request["cache_hit"] = True
                elif event.get("stage") == "requesting":
                    optimizer_request["request_started"] = True
                elif event.get("stage") == "done":
                    optimizer_request["tokens"] = event.get("tokens")

            candidate_origin = "LLM response"
            try:
                raw_opt, ap, am = _llm.generate_task(
                    "optimal_code",
                    optimizer_prompt,
                    provider_name=self.provider_name,
                    ollama_model=self.ollama_model,
                    language=self.language,
                    progress_callback=track_optimizer_request,
                )
                active_prov = ap
                active_mdl = am
                candidate_code = _extract_code_block(str(raw_opt), language=self.language)
                if optimizer_request["cache_hit"]:
                    response_source = "cached response; no model inference was run"
                elif optimizer_request["request_started"]:
                    response_source = "live model request"
                else:
                    response_source = "provider returned a response; inference source was not reported"
                token_count = optimizer_request["tokens"]
                token_note = f"; generated tokens: {token_count}" if token_count is not None else ""
                self._log_trace(
                    "OptimizerAgent",
                    f"Model response received (Cycle {cycle})",
                    "info",
                    f"Provider: {ap}; model: {am}; source: {response_source}{token_note}.",
                    elapsed_ms=(time.monotonic() - t_opt) * 1000.0,
                )
            except Exception as exc:
                self._log_trace("OptimizerAgent", f"Synthesis error (Cycle {cycle})", "error", str(exc), elapsed_ms=(time.monotonic() - t_opt) * 1000.0)
                candidate_code = self._apply_deterministic_hardening(candidate)
                ap = "deterministic_agent"
                am = "rule_based_hardener"
                candidate_origin = "deterministic hardening (model request failed)"

            if not candidate_code or not candidate_code.strip() or candidate_code.strip() == candidate.strip():
                unchanged_response = bool(candidate_code and candidate_code.strip() == candidate.strip())
                candidate_code = self._apply_deterministic_hardening(candidate)
                fallback_reason = "LLM returned unchanged code" if unchanged_response else "LLM returned no usable code"
                candidate_origin = f"deterministic hardening ({fallback_reason})"
            else:
                # Apply deterministic algorithmic hardening (O(N^2) sorting -> std::sort, hoist sort, fast I/O) on top of synthesis
                hardened_cand = self._apply_deterministic_hardening(candidate_code)
                if hardened_cand and hardened_cand.strip() != candidate_code.strip():
                    candidate_code = hardened_cand
                    candidate_origin = "LLM response plus deterministic hardening"

            candidate_changed = bool(candidate_code.strip()) and candidate_code.strip() != candidate.strip()
            candidate_status = "success" if candidate_changed else "warning"
            self._log_trace(
                "OptimizerAgent",
                f"Patch candidate {'generated' if candidate_changed else 'unchanged'} (Cycle {cycle})",
                candidate_status,
                f"Candidate source: {candidate_origin}; provider/model: {ap} ({am}); "
                f"source code changed: {'yes' if candidate_changed else 'no'}.",
                elapsed_ms=(time.monotonic() - t_opt) * 1000.0,
            )

            # 3d. Test Generator Agent (Edge-Case Test Matrix)
            t_testgen = time.monotonic()
            self._transition("TEST_GENERATION", cycle=cycle, reason="Generating edge-case input matrix")
            test_matrix = generate_test_matrix(
                self.code,
                baseline_stdin=self.baseline_stdin,
                language=self.language,
                provider_name=self.provider_name,
                ollama_model=self.ollama_model,
                count=5,
            )
            self._log_trace("TestGeneratorAgent", f"Generated test matrix (Cycle {cycle})", "info", f"Produced {len(test_matrix)} edge-case test vectors.", elapsed_ms=(time.monotonic() - t_testgen) * 1000.0)

            # 3e. Sandbox Judge & Equivalence Verification Gate
            t_judge = time.monotonic()
            self._transition("JUDGE", cycle=cycle, reason="Evaluating behavioral equivalence & sanitizers in sandbox")

            # Guardrail check
            safe, sec_msg = run_security_guardrail(candidate_code)
            if not safe:
                critic_guidance = f"SECURITY GUARDRAIL REJECTION: {sec_msg}"
                self._log_trace("SandboxJudge", f"Candidate rejected by security guardrail (Cycle {cycle})", "error", sec_msg, elapsed_ms=(time.monotonic() - t_judge) * 1000.0)
                continue

            if self.language in ("cpp", "c++", "c"):
                sanitizer_stdin = self.baseline_stdin
                if not sanitizer_stdin and test_matrix:
                    sanitizer_stdin = test_matrix[0].stdin
                sanitizer_findings = run_sanitizer_analysis(
                    candidate_code,
                    stdin_input=sanitizer_stdin,
                )
                critical_findings = [
                    finding for finding in sanitizer_findings
                    if finding.severity.upper() == "CRITICAL"
                ]
                if critical_findings:
                    sanitizer_message = "\n".join(
                        finding.message for finding in critical_findings[:3]
                    )
                    critic_guidance = f"Candidate sanitizer rejection:\n{sanitizer_message}"
                    self._log_trace(
                        "SandboxJudge",
                        f"Candidate rejected by sanitizer (Cycle {cycle})",
                        "error",
                        sanitizer_message,
                        elapsed_ms=(time.monotonic() - t_judge) * 1000.0,
                    )
                    continue

            # Run candidate across test matrix to test equivalence against original
            passed_tests = 0
            mismatch_error = ""
            tests_evaluated = 0
            reference_times: list[float] = []
            candidate_times: list[float] = []

            # First verify candidate compiles
            first_test_stdin = test_matrix[0].stdin if test_matrix else self.baseline_stdin
            probe_run = compile_and_run(candidate_code, stdin_input=first_test_stdin, language=self.language)
            if probe_run[0] != 0:
                mismatch_error = (
                    f"Candidate did not complete the verification probe (status {probe_run[0]}):\n"
                    f"{probe_run[2].strip()}"
                )
            elif getattr(probe_run, "output_limited", False):
                mismatch_error = "Candidate probe output exceeded the capture limit; equivalence cannot be verified."
            elif not test_matrix:
                mismatch_error = "No test cases were generated; equivalence cannot be verified."

            if not mismatch_error:
                for tcase in test_matrix:
                    orig_run = compile_and_run(self.code, stdin_input=tcase.stdin, language=self.language)
                    cand_run = compile_and_run(candidate_code, stdin_input=tcase.stdin, language=self.language)
                    tests_evaluated += 1
                    passed, comparison_error = _compare_test_case(orig_run, cand_run, tcase.name)
                    if not passed:
                        mismatch_error = comparison_error
                        break
                    passed_tests += 1
                    reference_times.append(getattr(orig_run, "execution_time", 0.0))
                    candidate_times.append(getattr(cand_run, "execution_time", 0.0))
            tests_passed = passed_tests

            if mismatch_error:
                # 3f. Critic Feedback Loop
                self._transition("CRITIC", cycle=cycle, reason="Critic reflecting on test discrepancy/failure")
                critic_guidance = f"Sandbox verification failed on test matrix:\n{mismatch_error}\nPlease resolve this discrepancy."
                self._log_trace("CriticAgent", f"Rejection feedback formulated (Cycle {cycle})", "warning", critic_guidance, elapsed_ms=(time.monotonic() - t_judge) * 1000.0)
                continue

            # 3g. Score Comparison & Empirical Validation
            cand_quality = compute_code_quality_metrics(candidate_code, language=self.language)
            cand_sec = cand_quality.get("security_score", 0.0)
            cand_eff = cand_quality.get("efficiency_score", 0.0)
            quality_improved = (
                _quality_does_not_regress(cand_quality, best_candidate_metrics)
                and (
                    cand_sec > best_candidate_metrics.get("security_score", 0.0)
                    or cand_eff > best_candidate_metrics.get("efficiency_score", 0.0)
                )
            )
            baseline_runtime = statistics.median(reference_times)
            candidate_runtime = statistics.median(candidate_times)
            runtime_tolerance = max(0.25, baseline_runtime * 0.10)
            runtime_not_regressed = candidate_runtime <= baseline_runtime + runtime_tolerance
            runtime_improved = candidate_runtime < baseline_runtime - runtime_tolerance
            score_improved = runtime_not_regressed and (quality_improved or runtime_improved)

            if score_improved:
                self.best_candidate = candidate_code
                best_candidate_metrics = cand_quality
                best_candidate_verified = True
                best_tests_evaluated = tests_evaluated
                best_tests_passed = passed_tests
                self.no_progress_count = 0
                self._log_trace(
                    "SandboxJudge",
                    f"Candidate accepted and verified (Cycle {cycle})",
                    "success",
                    f"Passed all {passed_tests}/{tests_evaluated} generated cases. "
                    f"Security: {cand_sec:.0f}% (was {base_sec:.0f}%), "
                    f"Efficiency: {cand_eff:.0f}% (was {base_eff:.0f}%). "
                    f"Median runtime: {candidate_runtime:.2f} ms (reference {baseline_runtime:.2f} ms).",
                    elapsed_ms=(time.monotonic() - t_judge) * 1000.0,
                )

                # Check if optimal
                if cand_sec >= 100.0 and cand_eff >= 100.0:
                    self._transition(
                        "COMPLETED",
                        cycle=cycle,
                        reason="Both heuristic quality scores reached 100%; behavior checks passed",
                    )
                    break
            else:
                self.no_progress_count += 1
                reason = (
                    "Candidate regressed measured runtime"
                    if not runtime_not_regressed
                    else "Candidate did not improve quality scores or measured runtime"
                )
                self._log_trace(
                    "SandboxJudge",
                    f"Candidate not accepted (Cycle {cycle})",
                    "warning",
                    f"{reason}. Security: {cand_sec:.0f}% (reference {best_candidate_metrics.get('security_score', 0.0):.0f}%), "
                    f"efficiency: {cand_eff:.0f}% (reference {best_candidate_metrics.get('efficiency_score', 0.0):.0f}%), "
                    f"median runtime: {candidate_runtime:.2f} ms (reference {baseline_runtime:.2f} ms).",
                    elapsed_ms=(time.monotonic() - t_judge) * 1000.0,
                )
                if self.no_progress_count >= self.patience:
                    self._transition("COMPLETED", cycle=cycle, reason=f"Patience limit reached ({self.patience} cycles without score gain)")
                    break

        # Compute final profile metrics on best candidate
        cand_profile = measure_execution_profile(self.best_candidate, stdin_input=self.baseline_stdin, language=self.language, runs=3)
        metrics_after = {
            **best_candidate_metrics,
            "median_runtime_ms": cand_profile.median_time_ms,
            "peak_memory_kb": cand_profile.peak_memory_kb,
        }
        metrics_before = {
            **base_quality,
            "median_runtime_ms": baseline_evidence.get("profile_metrics", {}).get("median_time_ms", 0.0),
            "peak_memory_kb": baseline_evidence.get("profile_metrics", {}).get("peak_memory_kb", 0.0),
        }

        # Unified diff
        diff_lines = list(difflib.unified_diff(
            self.code.splitlines(keepends=True),
            self.best_candidate.splitlines(keepends=True),
            fromfile="original",
            tofile="optimized",
        ))
        diff_str = "".join(diff_lines)

        verified = best_candidate_verified and self.best_candidate != self.code
        status_label = "Verified & Improved" if verified else "Needs Review"
        reported_tests_evaluated = best_tests_evaluated if best_candidate_verified else tests_evaluated
        reported_tests_passed = best_tests_passed if best_candidate_verified else tests_passed

        # Compute summary of verified changes
        changes_list: list[str] = []
        sec_b = metrics_before.get("security_score", 0.0)
        sec_a = metrics_after.get("security_score", 0.0)
        eff_b = metrics_before.get("efficiency_score", 0.0)
        eff_a = metrics_after.get("efficiency_score", 0.0)

        if sec_a > sec_b:
            changes_list.append(f"Security Rating hardened from {sec_b:.0f}% to {sec_a:.0f}%.")
        if eff_a > eff_b:
            changes_list.append(f"Efficiency Rating improved from {eff_b:.0f}% to {eff_a:.0f}%.")
        if best_candidate_verified:
            changes_list.append(
                f"Matched the reference output on {reported_tests_passed}/{reported_tests_evaluated} generated cases."
            )
        elif reported_tests_evaluated > 0:
            changes_list.append(
                f"Functional equivalence was not established ({reported_tests_passed}/{reported_tests_evaluated} cases passed); "
                "the proposed patch was not accepted."
            )
        changes_list.append(f"Empirical Sandbox Status: {status_label}")

        return OrchestratorResult(
            success=verified,
            original_code=self.code,
            optimized_code=self.best_candidate,
            language=self.language,
            diff_unified=diff_str,
            verification_status=status_label,
            changes_summary=changes_list,
            state_transitions=[asdict(t) for t in self.transitions],
            agent_trace=self.trace,
            evidence_report=baseline_evidence,
            metrics_before=metrics_before,
            metrics_after=metrics_after,
            tests_evaluated=reported_tests_evaluated,
            tests_passed=reported_tests_passed,
            cycles_taken=cycle if 'cycle' in locals() else 1,
            total_time_s=round(time.monotonic() - self.start_time, 2),
            active_provider=active_prov or "local",
            active_model=active_mdl or "static-engine",
        )
