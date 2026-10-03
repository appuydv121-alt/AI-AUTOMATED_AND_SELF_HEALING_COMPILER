"""Pipeline orchestrator — Stages 0 through 9.

``run_reasoning()`` is the single public entry point called by ``app.py``.
It coordinates all stages with per-stage time budgets and graceful
degradation: if any stage fails, the pipeline continues with whatever
evidence it has collected.
"""

from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from reasoning.adapters import findings_to_diagnostics
from reasoning.config import (
    DEFAULT_PROFILE,
    GLOBAL_BUDGET,
    PROFILE_STAGES,
    STAGE_TIMEOUT,
)
from reasoning.evidence_gate import run_evidence_gate
from reasoning.facts import collect as collect_facts
from reasoning.llm_client import LLMClient, get_client, set_provider
from reasoning.models import (
    Candidate,
    Fact,
    Finding,
    IntentModel,
    PatchEdit,
    ReasoningResult,
    StageResult,
    Tier,
    VariableRole,
)
from reasoning.optimize import run_optimization_analysis
from reasoning.patching import apply_edits
from reasoning.prompts import (
    comprehend_prompt,
    explain_prompt,
    fix_prompt,
    hypothesize_prompt,
)
from reasoning.refuter import run_refutation, refute_candidate
from reasoning.schemas import (
    COMPREHEND_SCHEMA,
    EXPLAIN_SCHEMA,
    FIX_SCHEMA,
    HYPOTHESIZE_SCHEMA,
)
from reasoning.tier_policy import HEDGING, assign_tier
from reasoning.verify import verify_patch

# ── Pipeline-level result cache (session_state is not available here) ───────
# Keyed by SHA-256 of (source, run_result, profile, provider, gemini_model, ollama_model).
# Cleared when cache grows beyond 32 entries to prevent memory bloat.
_PIPELINE_CACHE: dict[str, ReasoningResult] = {}
_PIPELINE_CACHE_MAX = 32


def _pipeline_cache_key(
    source: str,
    run_result: dict | None,
    profile: str,
    provider_name: str,
    gemini_model: str | None,
    ollama_model: str | None,
) -> str:
    payload = json.dumps(
        {
            "source": source,
            "run_result": run_result or {},
            "profile": profile,
            "provider": provider_name,
            "gemini_model": gemini_model or "",
            "ollama_model": ollama_model or "",
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def run_reasoning(
    source: str,
    run_result: dict[str, Any] | None = None,
    *,
    profile: str = "",
    work_dir: str | None = None,
    provider_name: str = "",
    gemini_model: str | None = None,
    ollama_model: str | None = None,
) -> ReasoningResult:
    """Run the full Fact → Hypothesis → Verification pipeline.

    Parameters
    ----------
    source : str
        The C++ source code.
    run_result : dict, optional
        Result from ``compile_and_run`` with keys ``return_code``,
        ``stdout``, ``stderr``.
    profile : str
        One of ``"quick"``, ``"standard"``, ``"thorough"``.
        Defaults to ``config.DEFAULT_PROFILE``.  Automatically
        downgraded to ``"quick"`` when the program compiled cleanly
        (return_code == 0) and no stderr is present.
    work_dir : str, optional
        Working directory for temp files.
    provider_name : str
        ``"gemini"`` or ``"ollama"``.  Forwarded to
        ``llm_client.set_provider()`` so the correct backend is used.
    gemini_model : str, optional
        Gemini model name selected in the sidebar.
    ollama_model : str, optional
        Ollama model name selected in the sidebar.

    Returns
    -------
    ReasoningResult
        Contains findings, fact_bundle, timing, and stage status.
    """
    # ── Provider injection ─────────────────────────────────────────────
    if provider_name:
        set_provider(provider_name, gemini_model=gemini_model, ollama_model=ollama_model)

    # ── Auto-quick profile on clean runs ───────────────────────────────
    rr = run_result or {}
    is_clean_run = rr.get("return_code", 1) == 0 and not (rr.get("stderr") or "").strip()
    if not profile:
        profile = "quick" if is_clean_run else DEFAULT_PROFILE
    if profile not in PROFILE_STAGES:
        profile = DEFAULT_PROFILE

    # ── Result cache ───────────────────────────────────────────────────
    cache_key = _pipeline_cache_key(
        source, run_result, profile,
        provider_name or "gemini",
        gemini_model, ollama_model,
    )
    if cache_key in _PIPELINE_CACHE:
        return _PIPELINE_CACHE[cache_key]

    active_stages = PROFILE_STAGES[profile]
    start_time = time.monotonic()
    result = ReasoningResult(profile=profile)
    stages_run: list[str] = []
    stages_failed: list[str] = []

    def budget_remaining() -> float:
        return max(0.0, GLOBAL_BUDGET - (time.monotonic() - start_time))

    # ── Stage 0: Collect facts (always runs, deterministic) ─────────
    try:
        bundle = collect_facts(source, compile_result=run_result, work_dir=work_dir)
        result.fact_bundle = bundle
        stages_run.append("facts")
    except Exception:
        stages_failed.append("facts")
        result.stages_run = stages_run
        result.stages_failed = stages_failed
        result.elapsed_seconds = time.monotonic() - start_time
        return result

    # Check if we should proceed with LLM stages
    client = get_client()
    available, err = client.is_model_available()
    if not available:
        # Return facts-only result (compiler warnings become findings)
        result.findings = _compiler_facts_to_findings(bundle)
        result.stages_run = stages_run
        result.elapsed_seconds = time.monotonic() - start_time
        return result

    # ── Stage 1: Comprehend ─────────────────────────────────────────
    intent_model = None
    if "comprehend" in active_stages and budget_remaining() > 5:
        try:
            intent_model = _run_comprehend(source, bundle, client)
            result.intent_model = intent_model
            stages_run.append("comprehend")
        except Exception:
            stages_failed.append("comprehend")

    # ── Stage 2: Hypothesize ────────────────────────────────────────
    candidates: list[Candidate] = []
    if "hypothesize" in active_stages and budget_remaining() > 5:
        try:
            candidates = _run_hypothesize(source, bundle, intent_model, client)
            stages_run.append("hypothesize")
        except Exception:
            stages_failed.append("hypothesize")

    # ── Stage 3: Evidence gate ──────────────────────────────────────
    if "evidence_gate" in active_stages and candidates:
        try:
            candidates = run_evidence_gate(candidates, bundle)
            stages_run.append("evidence_gate")
        except Exception:
            stages_failed.append("evidence_gate")

    surviving = [c for c in candidates if c.gate_status != "reject"]

    # ── Stage 4: Refute (parallelized per-candidate) ────────────────
    if "refute" in active_stages and surviving and budget_remaining() > 5:
        try:
            full_code = "\n".join(bundle.source_lines)

            def _get_code_slice(candidate: Candidate) -> str:
                if candidate.lines:
                    start = max(0, min(candidate.lines) - 5)
                    end   = min(len(bundle.source_lines), max(candidate.lines) + 5)
                    return "\n".join(
                        f"{i+1}: {line}"
                        for i, line in enumerate(bundle.source_lines[start:end], start)
                    )
                return full_code

            with ThreadPoolExecutor(max_workers=min(4, len(surviving))) as pool:
                fut_map = {
                    pool.submit(refute_candidate, c, _get_code_slice(c), client=client): c
                    for c in surviving if c.gate_status == "pass"
                }
                for fut in as_completed(fut_map, timeout=STAGE_TIMEOUT.get("refute", 10) + 2):
                    try:
                        fut.result()
                    except Exception:
                        pass

            stages_run.append("refute")
        except Exception:
            stages_failed.append("refute")

    surviving = [c for c in surviving if c.refute_verdict != "refuted"]

    # ── Stage 5: Tier assignment ────────────────────────────────────
    findings: list[Finding] = []
    if "tier" in active_stages:
        for c in surviving:
            tier = assign_tier(c, bundle)
            finding = _candidate_to_finding(c, tier, bundle)
            findings.append(finding)
        stages_run.append("tier")

    # ── Stage 6+7: Fix + Verify (parallelized per-finding) ──────────
    fixable = [
        f for f in findings
        if f.tier in (Tier.DEFINITE_ERROR, Tier.DEFINITE_BUG, Tier.LIKELY_BUG)
    ]
    if "fix" in active_stages and fixable and budget_remaining() > 5:
        try:
            def _fix_and_verify(finding: Finding) -> None:
                _run_fix(finding, bundle, intent_model, client)
                if "verify" in active_stages and finding.proposed_patch:
                    _run_verify(finding, bundle)

            with ThreadPoolExecutor(max_workers=min(4, len(fixable))) as pool:
                futs = [pool.submit(_fix_and_verify, f) for f in fixable]
                for fut in as_completed(futs, timeout=STAGE_TIMEOUT.get("fix", 12) + 5):
                    try:
                        fut.result()
                    except Exception:
                        pass

            stages_run.append("fix")
            if any(f.proposed_patch for f in fixable):
                stages_run.append("verify")
        except Exception:
            stages_failed.append("fix")
    elif "verify" in active_stages:
        for finding in findings:
            if finding.proposed_patch:
                try:
                    _run_verify(finding, bundle)
                    if "verify" not in stages_run:
                        stages_run.append("verify")
                except Exception:
                    if "verify" not in stages_failed:
                        stages_failed.append("verify")

    # ── Stage 8: Optimize ───────────────────────────────────────────
    has_blocking = any(f.tier in (Tier.DEFINITE_ERROR,) for f in findings)
    if "optimize" in active_stages and not has_blocking and budget_remaining() > 5:
        try:
            opt_findings = run_optimization_analysis(
                bundle.source_lines, bundle.functions, intent_model, client=client,
            )
            findings.extend(opt_findings)
            stages_run.append("optimize")
        except Exception:
            stages_failed.append("optimize")

    # ── Stage 9: Explain (parallelized per-finding) ─────────────────
    if "explain" in active_stages and budget_remaining() > 3:
        try:
            with ThreadPoolExecutor(max_workers=min(4, max(1, len(findings)))) as pool:
                futs = [pool.submit(_run_explain, f, client) for f in findings]
                for fut in as_completed(futs, timeout=STAGE_TIMEOUT.get("explain", 8) + 2):
                    try:
                        fut.result()
                    except Exception:
                        pass
            if "explain" not in stages_run:
                stages_run.append("explain")
        except Exception:
            if "explain" not in stages_failed:
                stages_failed.append("explain")

    # Add compiler-only findings that aren't superseded
    compiler_findings = _compiler_facts_to_findings(bundle)
    superseded_ids: set[str] = set()
    for f in findings:
        superseded_ids.update(f.supersedes)
    for cf in compiler_findings:
        if not any(fid in superseded_ids for fid in cf.fact_ids):
            existing_fact_ids: set[str] = set()
            for f in findings:
                existing_fact_ids.update(f.fact_ids)
            if not any(fid in existing_fact_ids for fid in cf.fact_ids):
                findings.append(cf)

    result.findings = findings
    result.stages_run = stages_run
    result.stages_failed = stages_failed
    result.elapsed_seconds = time.monotonic() - start_time
    result.partial = budget_remaining() <= 0

    # ── Store in cache ─────────────────────────────────────────────
    if len(_PIPELINE_CACHE) >= _PIPELINE_CACHE_MAX:
        to_drop = list(_PIPELINE_CACHE.keys())[: _PIPELINE_CACHE_MAX // 2]
        for k in to_drop:
            del _PIPELINE_CACHE[k]
    _PIPELINE_CACHE[cache_key] = result

    return result



# ── Stage implementations ───────────────────────────────────────────────────

def _run_comprehend(source: str, bundle, client: LLMClient) -> IntentModel | None:
    facts_text = bundle.fact_text_block()
    prompt = comprehend_prompt(source, facts_text)

    parsed, error = client.generate_json(
        prompt, COMPREHEND_SCHEMA,
        timeout=STAGE_TIMEOUT.get("comprehend", 30),
    )
    if error or parsed is None:
        return None

    roles = []
    for vr in parsed.get("variable_roles", []):
        roles.append(VariableRole(
            name=vr.get("name", ""),
            role=vr.get("role", ""),
            confidence=float(vr.get("confidence", 0)),
            evidence_lines=vr.get("evidence_lines", []),
        ))

    return IntentModel(
        purpose=parsed.get("purpose", ""),
        variable_roles=roles,
        unknowns=parsed.get("unknowns", []),
    )


def _run_hypothesize(
    source: str, bundle, intent_model: IntentModel | None, client: LLMClient,
) -> list[Candidate]:
    facts_text = bundle.fact_text_block()
    intent_json = json.dumps({
        "purpose": intent_model.purpose if intent_model else "",
        "variable_roles": [
            {"name": vr.name, "role": vr.role, "confidence": vr.confidence}
            for vr in (intent_model.variable_roles if intent_model else [])
        ],
        "unknowns": intent_model.unknowns if intent_model else [],
    })

    prompt = hypothesize_prompt(source, facts_text, intent_json)
    parsed, error = client.generate_json(
        prompt, HYPOTHESIZE_SCHEMA,
        timeout=STAGE_TIMEOUT.get("hypothesize", 30),
    )
    if error or parsed is None:
        return []

    candidates = []
    for raw in parsed.get("candidates", []):
        candidates.append(Candidate(
            claim=raw.get("claim", ""),
            kind=raw.get("kind", ""),
            lines=raw.get("lines", []),
            snippet=raw.get("snippet", ""),
            fact_ids=raw.get("fact_ids", []),
            depends_on_intent=raw.get("depends_on_intent", False),
            proposed_tier=raw.get("proposed_tier", "possible_issue"),
            why_this_may_be_fine=raw.get("why_this_may_be_fine", ""),
            falsifier=raw.get("falsifier", ""),
        ))

    return candidates


def _run_fix(finding: Finding, bundle, intent_model, client: LLMClient):
    """Stage 6: Generate minimal patch edits for a finding."""
    if not finding.lines:
        return

    # Build code slice around the finding
    start = max(0, min(finding.lines) - 5)
    end = min(len(bundle.source_lines), max(finding.lines) + 5)
    code_slice = "\n".join(
        f"{i+1}: {line}" for i, line in enumerate(bundle.source_lines[start:end], start)
    )

    candidate_json = json.dumps({
        "claim": finding.explanation,
        "kind": finding.kind,
        "lines": finding.lines,
        "tier": finding.tier.value,
    })
    intent_json = json.dumps({
        "purpose": intent_model.purpose if intent_model else "",
        "variable_roles": [
            {"name": vr.name, "role": vr.role}
            for vr in (intent_model.variable_roles if intent_model else [])
        ],
    })

    prompt = fix_prompt(code_slice, candidate_json, intent_json)
    parsed, error = client.generate_json(
        prompt, FIX_SCHEMA,
        timeout=STAGE_TIMEOUT.get("fix", 30),
    )
    if error or parsed is None:
        return

    if not parsed.get("can_fix", False):
        return

    edits_raw = parsed.get("edits", [])
    if not edits_raw:
        return

    finding.proposed_patch = {
        "edits": edits_raw,
        "rationale": parsed.get("rationale", ""),
        "alternatives": parsed.get("alternatives", ""),
    }


def _run_verify(finding: Finding, bundle):
    """Stage 7: Run verification ladder on the finding's patch."""
    if not finding.proposed_patch:
        return

    edits_raw = finding.proposed_patch.get("edits", [])
    edits = [
        PatchEdit(
            start_line=e.get("start_line", 0),
            end_line=e.get("end_line", 0),
            replacement=e.get("replacement", ""),
        )
        for e in edits_raw
    ]

    report = verify_patch(bundle.source_lines, edits, finding.lines)
    finding.verification = report


def _run_explain(finding: Finding, client: LLMClient):
    """Stage 9: Generate user-facing prose."""
    tier = finding.tier
    hedging = HEDGING.get(tier, HEDGING[Tier.POSSIBLE_ISSUE])

    finding_json = json.dumps({
        "tier": tier.value,
        "kind": finding.kind,
        "lines": finding.lines,
        "claim": finding.explanation,
        "evidence": finding.evidence,
        "fact_ids": finding.fact_ids,
    })

    prompt = explain_prompt(finding_json, tier.value, hedging)
    parsed, error = client.generate_json(
        prompt, None,  # Use plain JSON for explain since it's simpler
        timeout=STAGE_TIMEOUT.get("explain", 15),
    )
    if error or parsed is None:
        return

    if parsed.get("title"):
        finding.title = parsed["title"]
    if parsed.get("explanation"):
        finding.explanation = parsed["explanation"]
    if parsed.get("reasoning"):
        finding.reasoning = parsed.get("reasoning", "")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _compiler_facts_to_findings(bundle) -> list[Finding]:
    """Convert compiler-warning facts to Findings for direct display."""
    findings = []
    for fact in bundle.facts:
        if fact.source != "compiler":
            continue
        if fact.kind == "error":
            tier = Tier.DEFINITE_ERROR
        elif fact.kind == "warning":
            msg_lower = fact.message.lower()
            if "may be" in msg_lower or "might be" in msg_lower:
                tier = Tier.LIKELY_BUG
            else:
                tier = Tier.DEFINITE_BUG
        else:
            continue

        evidence = [{"fact_id": fact.id, "source": "compiler", "text": fact.message}]
        for child in fact.children:
            evidence.append({
                "fact_id": child.id or fact.id,
                "source": "compiler",
                "text": child.message,
            })

        title = f"Compiler {fact.kind}"
        if fact.option:
            title += f" ({fact.option})"

        explanation = fact.message
        if fact.children:
            explanation += " — " + "; ".join(c.message for c in fact.children)

        findings.append(Finding(
            tier=tier,
            kind=fact.option or fact.kind,
            lines=[fact.line] if fact.line else [],
            title=title,
            explanation=explanation,
            evidence=evidence,
            source="compiler",
            fact_ids=[fact.id],
        ))

    return findings


def _candidate_to_finding(candidate: Candidate, tier: Tier, bundle) -> Finding:
    """Convert a surviving Candidate to a Finding."""
    evidence = []
    for fid in candidate.fact_ids:
        for fact in bundle.facts:
            if fact.id == fid:
                evidence.append({
                    "fact_id": fid,
                    "source": fact.source,
                    "text": fact.message,
                })

    return Finding(
        tier=tier,
        kind=candidate.kind,
        lines=candidate.lines,
        title=candidate.claim[:80],
        explanation=candidate.claim,
        reasoning=candidate.why_this_may_be_fine,
        depends_on_intent=candidate.depends_on_intent,
        evidence=evidence,
        why_this_may_be_fine=candidate.why_this_may_be_fine,
        source="reasoning",
        fact_ids=candidate.fact_ids,
        supersedes=candidate.fact_ids,  # reasoning findings supersede their source facts
    )
