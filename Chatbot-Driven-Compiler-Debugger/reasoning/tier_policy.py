"""Stage 5: Tier assignment — evidence → certainty ceiling.

The LLM proposes a tier; this module computes the ceiling from evidence
and takes the minimum.  Keys on *evidence kinds*, not on code patterns,
so it is epistemic policy, not detection logic.
"""

from __future__ import annotations

from reasoning.models import Candidate, FactBundle, Tier, TIER_SEVERITY_ORDER


# ── Per-tier hedging phrases ────────────────────────────────────────────────

HEDGING = {
    Tier.DEFINITE_ERROR: "This is a definite error confirmed by the compiler.",
    Tier.DEFINITE_BUG:   "This is a confirmed bug supported by compiler diagnostics and analysis.",
    Tier.LIKELY_BUG:     "This is likely a bug based on available evidence, though there may be an unusual valid use.",
    Tier.POSSIBLE_ISSUE: "This might be an issue. Review the evidence and decide whether it applies to your intent.",
    Tier.CODE_SMELL:     "This is a maintainability concern, not necessarily a bug.",
    Tier.OPTIMIZATION:   "This is an optional optimization suggestion.",
    Tier.STYLE:          "This is a stylistic suggestion for readability.",
}


def _tier_from_string(s: str) -> Tier:
    """Convert a string to a Tier, with fallback."""
    s = s.lower().strip().replace(" ", "_").replace("-", "_")
    for t in Tier:
        if t.value == s:
            return t
    return Tier.POSSIBLE_ISSUE


def compute_tier_ceiling(candidate: Candidate, bundle: FactBundle) -> Tier:
    """Compute the maximum tier a candidate may reach based on its evidence.

    Rules (from the plan §16):
    - compiler error → definite_error
    - compiler definite warning (e.g. "is used uninitialized") or
      probe-confirmed → definite_bug
    - compiler "may be" warning or probe confirmed + refuter holds → likely_bug
    - LLM claim with valid citation, refuter holds/uncertain → possible_issue
    - depends_on_intent capped at likely_bug
    - downgraded by gate → one tier lower
    - refuted → dropped (handled by caller)
    """
    has_compiler_error = False
    has_compiler_definite_warning = False
    has_compiler_maybe_warning = False
    has_probe_confirmed = False

    for fid in candidate.fact_ids:
        for fact in bundle.facts:
            if fact.id != fid:
                continue
            if fact.source == "compiler":
                if fact.kind == "error":
                    has_compiler_error = True
                elif fact.kind == "warning":
                    msg = fact.message.lower()
                    if "may be" in msg or "might be" in msg:
                        has_compiler_maybe_warning = True
                    else:
                        has_compiler_definite_warning = True
            elif fact.source == "dataflow" and fact.kind == "may_read_unassigned":
                has_probe_confirmed = True

    # Determine ceiling
    if has_compiler_error:
        ceiling = Tier.DEFINITE_ERROR
    elif has_compiler_definite_warning or has_probe_confirmed:
        ceiling = Tier.DEFINITE_BUG
    elif has_compiler_maybe_warning:
        ceiling = Tier.LIKELY_BUG
    else:
        ceiling = Tier.POSSIBLE_ISSUE

    # Intent dependency caps at likely_bug
    if candidate.depends_on_intent:
        if TIER_SEVERITY_ORDER.get(ceiling, 99) < TIER_SEVERITY_ORDER.get(Tier.LIKELY_BUG, 99):
            ceiling = Tier.LIKELY_BUG

    # Gate downgrade: one tier lower
    if candidate.gate_status == "downgrade":
        ceiling_idx = TIER_SEVERITY_ORDER.get(ceiling, 3)
        tiers_by_idx = {v: k for k, v in TIER_SEVERITY_ORDER.items()}
        ceiling = tiers_by_idx.get(ceiling_idx + 1, Tier.POSSIBLE_ISSUE)

    # Refutation: uncertain → one tier lower
    if candidate.refute_verdict == "uncertain":
        ceiling_idx = TIER_SEVERITY_ORDER.get(ceiling, 3)
        tiers_by_idx = {v: k for k, v in TIER_SEVERITY_ORDER.items()}
        ceiling = tiers_by_idx.get(ceiling_idx + 1, Tier.POSSIBLE_ISSUE)

    return ceiling


def assign_tier(candidate: Candidate, bundle: FactBundle) -> Tier:
    """Assign the final tier = min(LLM proposal, evidence ceiling)."""
    proposed = _tier_from_string(candidate.proposed_tier)
    ceiling = compute_tier_ceiling(candidate, bundle)

    # Take the less severe (higher index) of proposed and ceiling
    proposed_idx = TIER_SEVERITY_ORDER.get(proposed, 3)
    ceiling_idx = TIER_SEVERITY_ORDER.get(ceiling, 3)

    final_idx = max(proposed_idx, ceiling_idx)
    tiers_by_idx = {v: k for k, v in TIER_SEVERITY_ORDER.items()}
    return tiers_by_idx.get(final_idx, Tier.POSSIBLE_ISSUE)
