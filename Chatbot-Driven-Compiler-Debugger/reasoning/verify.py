"""Stage 7: Verification ladder (V0–V2, structural only).

Per user decision: no compilation or execution of patched programs.
Checks are purely structural: patch applies, scope, security scan.
"""

from __future__ import annotations

import sys
import os

from reasoning.models import PatchEdit, VerificationReport
from reasoning.patching import apply_edits, check_v0_applies, check_v1_scope


def verify_patch(
    source_lines: list[str],
    edits: list[PatchEdit],
    target_lines: list[int],
) -> VerificationReport:
    """Run the verification ladder on a proposed patch.

    Currently implements V0 (applies), V1 (scope), V2 (security).
    """
    report = VerificationReport()

    if not edits:
        return report

    # V0: Patch applies cleanly
    status, detail = check_v0_applies(source_lines, edits)
    report.v0_applies = status
    report.v0_detail = detail

    if status == "failed":
        return report

    # V1: Scope check
    status, detail = check_v1_scope(edits, target_lines)
    report.v1_scope = status
    report.v1_detail = detail

    # V2: Security scan (compare patched against original baseline)
    try:
        patched_lines, _ = apply_edits(source_lines, edits)
        patched_code = "\n".join(patched_lines)
        original_code = "\n".join(source_lines)

        # Import the existing security scanner
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from secure_scan import scan_all_security_issues

        original_issues = {
            issue["pattern"] for issue in scan_all_security_issues(original_code)
        }
        patched_issues = scan_all_security_issues(patched_code)
        new_issues = [
            issue for issue in patched_issues
            if issue["pattern"] not in original_issues
        ]

        if new_issues:
            report.v2_security = "failed"
            report.v2_detail = f"Patch introduces {len(new_issues)} new security concern(s)"
        else:
            report.v2_security = "passed"
            report.v2_detail = "No new security concerns"

    except Exception as exc:
        report.v2_security = "skipped"
        report.v2_detail = f"Security scan unavailable: {exc}"

    return report
