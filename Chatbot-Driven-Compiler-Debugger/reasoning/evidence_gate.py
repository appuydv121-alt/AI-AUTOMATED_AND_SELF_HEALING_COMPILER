"""Stage 3: Evidence gate — mechanical checks on LLM candidates.

Every candidate must pass these deterministic checks before it can
proceed to refutation.  Failed checks cause rejection or downgrade.
"""

from __future__ import annotations

from reasoning.models import Candidate, FactBundle
from reasoning.probes import run_probe


def check_candidate(candidate: Candidate, bundle: FactBundle) -> Candidate:
    """Run mechanical evidence checks on a single candidate.

    Sets ``candidate.gate_status`` to ``"pass"``, ``"reject"``, or
    ``"downgrade"`` and ``candidate.gate_reason`` with an explanation.
    """
    reasons: list[str] = []

    # 1. All cited lines must exist
    for line in candidate.lines:
        result = run_probe(bundle, "line_exists", line=line)
        if result == "contradicted":
            candidate.gate_status = "reject"
            candidate.gate_reason = f"Cited line {line} does not exist in source ({len(bundle.source_lines)} lines total)"
            return candidate

    # 2. Snippet must be present at the claimed line
    if candidate.snippet and candidate.lines:
        primary_line = candidate.lines[0]
        result = run_probe(bundle, "snippet_present", snippet=candidate.snippet, line=primary_line)
        if result == "contradicted":
            candidate.gate_status = "reject"
            candidate.gate_reason = f"Snippet not found at line {primary_line}: '{candidate.snippet[:60]}...'"
            return candidate

    # 3. All cited fact IDs must exist
    for fid in candidate.fact_ids:
        result = run_probe(bundle, "fact_exists", fact_id=fid)
        if result == "contradicted":
            reasons.append(f"Cited fact {fid} does not exist in bundle")

    if reasons:
        candidate.gate_status = "downgrade"
        candidate.gate_reason = "; ".join(reasons)
        return candidate

    # 4. Check if claim involves a variable — verify it exists
    claim_lower = candidate.claim.lower()
    for decl in bundle.declarations:
        var_name = decl.get("name", "")
        if var_name and var_name in candidate.claim:
            # Found a variable mentioned in the claim — verify it
            result = run_probe(bundle, "variable_exists", variable=var_name)
            if result == "contradicted":
                candidate.gate_status = "reject"
                candidate.gate_reason = f"Variable '{var_name}' mentioned in claim does not exist in AST"
                return candidate

    # 5. Run specific probes based on claim kind
    probe_result = _run_claim_specific_probes(candidate, bundle)
    if probe_result == "contradicted":
        candidate.gate_status = "reject"
        candidate.gate_reason = "Claim contradicted by deterministic analysis"
        return candidate

    candidate.gate_status = "pass"
    candidate.gate_reason = ""
    return candidate


def _run_claim_specific_probes(candidate: Candidate, bundle: FactBundle) -> str:
    """Run probes specific to the kind of claim being made."""
    kind_lower = candidate.kind.lower()

    # For uninitialized-read claims, check the dataflow
    if any(kw in kind_lower for kw in ("uninitial", "unassigned", "read before")):
        # Try to identify the variable from the claim
        for decl in bundle.declarations:
            var_name = decl.get("name", "")
            if var_name and var_name in candidate.claim:
                line = candidate.lines[0] if candidate.lines else 0
                result = run_probe(
                    bundle, "read_before_write",
                    variable=var_name, line=line,
                )
                if result == "contradicted":
                    return "contradicted"
                if result == "confirmed":
                    return "confirmed"

    # For "variable not initialized" claims, check initializer status
    if "not initialized" in kind_lower or "no initializer" in kind_lower:
        for decl in bundle.declarations:
            var_name = decl.get("name", "")
            if var_name and var_name in candidate.claim:
                result = run_probe(bundle, "variable_has_initializer", variable=var_name)
                if result == "confirmed":  # has initializer = claim is wrong
                    return "contradicted"

    return "not_checkable"


def run_evidence_gate(
    candidates: list[Candidate],
    bundle: FactBundle,
) -> list[Candidate]:
    """Run the evidence gate on all candidates, returning the full list
    with ``gate_status`` set on each."""
    return [check_candidate(c, bundle) for c in candidates]
