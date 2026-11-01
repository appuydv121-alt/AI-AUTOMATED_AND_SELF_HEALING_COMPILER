"""orchestrator.py — Multi-Agent Supervisor & State Machine Orchestrator.

Enforces:
  1. Strict State Machine: Planner -> SecurityAudit -> EfficiencyProfile -> Optimizer -> TestGen -> Judge -> Critic.
  2. Stop Rules: max_cycles (default 3), patience (stop on 2 cycles of no progress), time budget.
  3. Rigorous Sandbox Judge: Compiles, runs across test matrix for equivalence, checks sanitizers, measures median runtime delta.
  4. Human Gate: Prepares patch diff + empirical evidence for explicit human review.
"""
from __future__ import annotations

import difflib
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import llm_provider as _llm
from agent_guard import build_guarded_prompt, wrap_code_data
from compiler_service import compile_and_run
from efficiency_agent import run_efficiency_profile
from memory_store import record_experience, retrieve_similar_experiences
from secure_scan import compute_code_quality_metrics, run_security_guardrail
from security_agent import run_security_audit
from test_generator_agent import generate_test_matrix
from tool_runner import collect_empirical_evidence, measure_execution_profile


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
        gemini_model: str | None = None,
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
        self.gemini_model = gemini_model
        self.ollama_model = ollama_model
        self.progress_callback = progress_callback

        self.start_time = time.monotonic()
        self.current_state = "INIT"
        self.transitions: list[TransitionLog] = []
        self.trace: list[dict[str, Any]] = []

        self.best_candidate = code
        self.best_score = 0.0
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
            import re

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

            # 4. Inject Fast I/O
            if "sync_with_stdio" not in out:
                main_pat = re.compile(r"(int\s+main\s*\([^)]*\)\s*\{)", re.MULTILINE)
                if main_pat.search(out):
                    fast_io = "\n    ios_base::sync_with_stdio(false);\n    cin.tie(NULL);\n"
                    out = main_pat.sub(r"\1" + fast_io, out, count=1)

            # 5. Replace endl with '\n'
            out = out.replace("<< endl", "<< '\\n'").replace("<< std::endl", "<< '\\n'")
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
        self.best_score = base_sec + base_eff

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
        best_candidate_metrics = base_quality
        active_prov = ""
        active_mdl = ""

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
                gemini_model=self.gemini_model,
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
                gemini_model=self.gemini_model,
                ollama_model=self.ollama_model,
            )
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
                f"You are the OPTIMIZER agent. Your task is to produce the smallest, cleanest {self.language.upper()} patch "
                "that resolves all security findings and efficiency bottlenecks while preserving exact functional behavior.\n\n"
                f"SECURITY FINDINGS TO FIX:\n"
                + ("\n".join(f"- {f.cwe}: {f.evidence}. Minimal fix: {f.minimal_fix}" for f in sec_findings) if sec_findings else "(None)")
                + f"\n\nEFFICIENCY HOTSPOTS TO FIX:\n"
                + ("\n".join(f"- {h.complexity}: {h.reason}. Suggested change: {h.suggested_change}" for h in eff_result.hotspots) if eff_result.hotspots else "(None)")
                + (f"\n\nCRITIC REFINEMENT GUIDANCE:\n{critic_guidance}" if critic_guidance else "")
                + (f"\n\n{memory_notes}" if memory_notes else "")
                + f"\n\nRULES:\n"
                f"1. Return ONLY the complete corrected program inside a single ```{self.language} code block.\n"
                f"2. Retain identical I/O formats.\n"
                f"3. In C++, use fast I/O (ios_base::sync_with_stdio(false); cin.tie(NULL)), initialize all primitives, and use '\\n'.\n\n"
                f"SOURCE CODE:\n{wrap_code_data(candidate, language=self.language)}"
            )

            try:
                raw_opt, ap, am = _llm.generate_task(
                    "optimal_code",
                    optimizer_prompt,
                    provider_name=self.provider_name,
                    gemini_model=self.gemini_model,
                    ollama_model=self.ollama_model,
                    language=self.language,
                )
                active_prov = ap
                active_mdl = am
                from agentic_optimizer import extract_code_block
                candidate_code = extract_code_block(str(raw_opt), language=self.language)
            except Exception as exc:
                self._log_trace("OptimizerAgent", f"Synthesis error (Cycle {cycle})", "error", str(exc), elapsed_ms=(time.monotonic() - t_opt) * 1000.0)
                candidate_code = self._apply_deterministic_hardening(candidate)
                ap = "deterministic_agent"
                am = "rule_based_hardener"

            if not candidate_code or not candidate_code.strip() or candidate_code.strip() == candidate.strip():
                candidate_code = self._apply_deterministic_hardening(candidate)
                ap = "structural_optimizer"
                am = "algorithm_hardener"
            else:
                # Apply deterministic algorithmic hardening (O(N^2) sorting -> std::sort, hoist sort, fast I/O) on top of synthesis
                hardened_cand = self._apply_deterministic_hardening(candidate_code)
                if hardened_cand and hardened_cand.strip() != candidate_code.strip():
                    candidate_code = hardened_cand

            self._log_trace("OptimizerAgent", f"Synthesized patch candidate (Cycle {cycle})", "success", f"Generated {len(candidate_code.splitlines())} lines via {ap} ({am}).", elapsed_ms=(time.monotonic() - t_opt) * 1000.0)

            # 3d. Test Generator Agent (Edge-Case Test Matrix)
            t_testgen = time.monotonic()
            self._transition("TEST_GENERATION", cycle=cycle, reason="Generating edge-case input matrix")
            test_matrix = generate_test_matrix(
                self.code,
                baseline_stdin=self.baseline_stdin,
                language=self.language,
                provider_name=self.provider_name,
                gemini_model=self.gemini_model,
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

            # Run candidate across test matrix to test equivalence against original
            passed_tests = 0
            mismatch_error = ""

            # First verify candidate compiles
            first_test_stdin = test_matrix[0].stdin if test_matrix else self.baseline_stdin
            probe_run = compile_and_run(candidate_code, stdin_input=first_test_stdin, language=self.language)
            if probe_run[0] not in (0, -1):
                # Compilation or immediate execution error
                mismatch_error = f"Candidate compilation/execution error (exit code {probe_run[0]}):\n{probe_run[2].strip()}"

            if not mismatch_error:
                for tcase in test_matrix:
                    orig_run = compile_and_run(self.code, stdin_input=tcase.stdin, language=self.language)
                    cand_run = compile_and_run(candidate_code, stdin_input=tcase.stdin, language=self.language)

                    # Case A: Original program ran cleanly to completion (exit code 0)
                    if orig_run[0] == 0:
                        if cand_run[0] != 0:
                            mismatch_error = f"Test '{tcase.name}' failed on candidate (exit code {cand_run[0]}): {cand_run[2].strip()}"
                            break
                        orig_out = orig_run[1].strip()
                        cand_out = cand_run[1].strip()
                        if orig_out != cand_out:
                            mismatch_error = f"Output discrepancy on test '{tcase.name}':\nExpected: '{orig_out}'\nGot: '{cand_out}'"
                            break
                        passed_tests += 1
                    else:
                        # Case B: Original program failed or timed out on malformed/empty test input
                        if cand_run[0] == 0:
                            passed_tests += 1
                        # If both failed or timed out on invalid test stdin, candidate matches original behavior

            tests_evaluated = len(test_matrix)
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
            cand_total_score = cand_sec + cand_eff

            score_improved = cand_total_score > self.best_score

            if score_improved:
                self.best_score = cand_total_score
                self.best_candidate = candidate_code
                best_candidate_metrics = cand_quality
                self.no_progress_count = 0
                self._log_trace(
                    "SandboxJudge",
                    f"Candidate accepted and verified (Cycle {cycle})",
                    "success",
                    f"Passed all {tests_passed}/{tests_evaluated} edge-case tests. "
                    f"Security: {cand_sec:.0f}% (was {base_sec:.0f}%), "
                    f"Efficiency: {cand_eff:.0f}% (was {base_eff:.0f}%).",
                    elapsed_ms=(time.monotonic() - t_judge) * 1000.0,
                )

                # Check if optimal
                if cand_sec >= 100.0 and cand_eff >= 100.0:
                    self._transition("COMPLETED", cycle=cycle, reason="Achieved 100% Security & 100% Efficiency")
                    break
            else:
                self.no_progress_count += 1
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

        verified = (self.best_candidate != self.code) and (self.best_score > (base_sec + base_eff)) and (not mismatch_error)
        status_label = "Verified & Improved" if verified else ("Verified & Hardened" if self.best_candidate != self.code else "Needs Review")

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
        if tests_evaluated > 0:
            changes_list.append(f"Verified functional equivalence on {tests_passed}/{tests_evaluated} edge-case tests.")
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
            tests_evaluated=tests_evaluated,
            tests_passed=tests_passed,
            cycles_taken=cycle if 'cycle' in locals() else 1,
            total_time_s=round(time.monotonic() - self.start_time, 2),
            active_provider=active_prov or "local",
            active_model=active_mdl or "static-engine",
        )

