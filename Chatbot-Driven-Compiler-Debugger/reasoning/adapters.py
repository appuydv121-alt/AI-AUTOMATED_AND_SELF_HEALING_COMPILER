"""Adapters — convert internal ``Finding`` objects to the existing
diagnostic dict format and chat-message text used by ``app.py``.
"""

from __future__ import annotations

from reasoning.models import Finding, ReasoningResult, Tier


# ── Tier → existing UI category mapping ────────────────────────────────────

_TIER_TO_CATEGORY = {
    Tier.DEFINITE_ERROR: "ERROR",
    Tier.DEFINITE_BUG:   "BUG",
    Tier.LIKELY_BUG:     "LIKELY_BUG",
    Tier.POSSIBLE_ISSUE: "POTENTIAL_ISSUE",
    Tier.CODE_SMELL:     "SMELL",
    Tier.OPTIMIZATION:   "OPTIMIZATION",
    Tier.STYLE:          "SUGGESTION",
}


def finding_to_diagnostic(finding: Finding) -> dict:
    """Convert a Finding to the existing ``make_diagnostic`` dict shape."""
    category = _TIER_TO_CATEGORY.get(finding.tier, "POTENTIAL_ISSUE")
    line = finding.lines[0] if finding.lines else None

    # Build details for the UI
    why_parts = []
    if finding.reasoning:
        why_parts.append(finding.reasoning)
    if finding.evidence:
        evidence_text = "; ".join(
            f"{e.get('source', '')}: {e.get('text', '')}" for e in finding.evidence
        )
        why_parts.append(f"Evidence: {evidence_text}")

    fix_parts = []
    if finding.proposed_patch:
        edits = finding.proposed_patch.get("edits", [])
        if edits:
            fix_parts.append(finding.proposed_patch.get("rationale", ""))
            for edit in edits:
                fix_parts.append(
                    f"Line {edit.get('start_line', '?')}: {edit.get('replacement', '').strip()}"
                )
    if finding.verification:
        fix_parts.append(f"Verification: {finding.verification.summary}")

    diag = {
        "category": category,
        "title": finding.title or finding.kind,
        "message": finding.explanation,
        "line": line,
        "source": "reasoning",
        "why": " ".join(why_parts) if why_parts else None,
        "fix": " | ".join(fix_parts) if fix_parts else None,
        # Extra keys for dedup/merge
        "fact_ids": finding.fact_ids,
        "supersedes": finding.supersedes,
        "confidence": finding.confidence,
        "tier": finding.tier.value,
        "finding_id": finding.id,
    }
    return diag


def findings_to_diagnostics(findings: list[Finding]) -> list[dict]:
    """Convert all findings to diagnostic dicts."""
    return [finding_to_diagnostic(f) for f in findings]


def findings_to_chat(result: ReasoningResult) -> str:
    """Format findings as a chat message for the AI Tutor panel."""
    if not result.findings:
        if result.stages_failed:
            return (
                "**AI Analysis** (partial — some stages encountered issues)\n\n"
                "No issues were found by the completed stages."
            )
        return "**AI Analysis:** No issues found. The code appears correct based on compiler diagnostics and reasoning analysis."

    lines = ["**AI Analysis Results:**\n"]

    for f in result.findings:
        tier_label = f.tier.value.replace("_", " ").title()
        icon = {
            Tier.DEFINITE_ERROR: "❌",
            Tier.DEFINITE_BUG: "🐛",
            Tier.LIKELY_BUG: "⚠️",
            Tier.POSSIBLE_ISSUE: "❓",
            Tier.CODE_SMELL: "👃",
            Tier.OPTIMIZATION: "⚡",
            Tier.STYLE: "💡",
        }.get(f.tier, "•")

        line_ref = f" — Line {f.lines[0]}" if f.lines else ""
        lines.append(f"{icon} **{tier_label}**{line_ref}")
        lines.append(f"{f.explanation}")

        if f.proposed_patch and f.verification and f.verification.all_passed:
            edits = f.proposed_patch.get("edits", [])
            if edits:
                lines.append(f"\n*Suggested fix:* {f.proposed_patch.get('rationale', '')}")
                lines.append(f"*Verification:* {f.verification.summary}")
        elif f.why_this_may_be_fine:
            lines.append(f"*Note:* {f.why_this_may_be_fine}")

        lines.append("")

    if result.partial:
        lines.append("*Analysis was incomplete due to time budget.*")

    profile = result.profile.title()
    elapsed = f"{result.elapsed_seconds:.1f}s"
    lines.append(f"*Profile: {profile} | Elapsed: {elapsed}*")

    return "\n".join(lines)


def findings_to_patch_code(result: ReasoningResult, source: str) -> str | None:
    """Extract the first verified patch as corrected source code for the expander."""
    from reasoning.patching import apply_edits
    from reasoning.models import PatchEdit

    source_lines = source.splitlines()

    for f in result.findings:
        if not f.proposed_patch:
            continue
        if not f.verification or not f.verification.all_passed:
            continue

        edits_raw = f.proposed_patch.get("edits", [])
        edits = [
            PatchEdit(
                start_line=e.get("start_line", 0),
                end_line=e.get("end_line", 0),
                replacement=e.get("replacement", ""),
            )
            for e in edits_raw
        ]
        patched, _ = apply_edits(source_lines, edits)
        if patched != source_lines:
            return "\n".join(patched)

    return None
