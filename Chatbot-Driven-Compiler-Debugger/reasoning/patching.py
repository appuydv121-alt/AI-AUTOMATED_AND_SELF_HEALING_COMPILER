"""Patch application and scope checking (V0/V1).

Applies structured edits (line-range replacements) to source code and
verifies that changes stay within declared target scope.
"""

from __future__ import annotations

import difflib

from reasoning.models import PatchEdit, VerificationReport


def apply_edits(source_lines: list[str], edits: list[PatchEdit]) -> tuple[list[str], str]:
    """Apply edits to source lines and return (patched_lines, unified_diff).

    Edits are applied in reverse line order to avoid index shifts.
    Returns the original source lines unchanged if any edit is invalid.
    """
    patched = list(source_lines)

    # Sort edits by start_line descending to avoid index shifting
    sorted_edits = sorted(edits, key=lambda e: e.start_line, reverse=True)

    for edit in sorted_edits:
        # Validate range
        if edit.start_line < 1 or edit.end_line > len(patched):
            return source_lines, ""
        if edit.start_line > edit.end_line:
            return source_lines, ""

        # Apply (1-indexed to 0-indexed)
        replacement_lines = edit.replacement.splitlines() if edit.replacement else []
        patched[edit.start_line - 1 : edit.end_line] = replacement_lines

    # Generate unified diff
    diff = difflib.unified_diff(
        source_lines,
        patched,
        fromfile="original",
        tofile="patched",
        lineterm="",
    )
    diff_text = "\n".join(diff)

    return patched, diff_text


def check_v0_applies(source_lines: list[str], edits: list[PatchEdit]) -> tuple[str, str]:
    """V0: Check whether the patch applies cleanly.

    Returns ``("passed"|"failed", detail)``.
    """
    if not edits:
        return "passed", "No edits to apply"

    for edit in edits:
        if edit.start_line < 1:
            return "failed", f"Edit start_line {edit.start_line} is < 1"
        if edit.end_line > len(source_lines):
            return "failed", f"Edit end_line {edit.end_line} exceeds source length ({len(source_lines)})"
        if edit.start_line > edit.end_line:
            return "failed", f"Edit start_line {edit.start_line} > end_line {edit.end_line}"

    # Check for overlapping edits
    sorted_edits = sorted(edits, key=lambda e: e.start_line)
    for i in range(len(sorted_edits) - 1):
        if sorted_edits[i].end_line >= sorted_edits[i + 1].start_line:
            return "failed", "Overlapping edits detected"

    # Try to apply
    patched, _ = apply_edits(source_lines, edits)
    if patched == source_lines and edits:
        return "failed", "Edits did not change the source"

    return "passed", "Patch applies cleanly"


def check_v1_scope(
    edits: list[PatchEdit],
    target_lines: list[int],
    slack: int = 3,
) -> tuple[str, str]:
    """V1: Check that changed lines stay within the declared target range (± slack).

    The slack allows for a necessary ``#include`` or nearby declaration.
    """
    if not edits:
        return "passed", "No edits"
    if not target_lines:
        return "passed", "No target scope declared"

    target_min = min(target_lines) - slack
    target_max = max(target_lines) + slack

    for edit in edits:
        if edit.start_line < target_min or edit.end_line > target_max:
            return "failed", (
                f"Edit at lines {edit.start_line}-{edit.end_line} is outside "
                f"target scope {min(target_lines)}-{max(target_lines)} (±{slack})"
            )

    return "passed", "All edits within target scope"
