"""Tests for the reasoning pipeline — deterministic components only.

These tests do NOT require Ollama or a network connection.
LLM stages are tested with mock responses.
"""

import json
import os
import sys
import unittest

# Ensure the project root (Chatbot-Driven-Compiler-Debugger) is on the path
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from reasoning.models import (
    Candidate, Fact, FactBundle, Finding, PatchEdit,
    Tier, VerificationReport,
)
from reasoning.tier_policy import assign_tier, compute_tier_ceiling, HEDGING
from reasoning.evidence_gate import check_candidate, run_evidence_gate
from reasoning.patching import apply_edits, check_v0_applies, check_v1_scope
from reasoning.verify import verify_patch
from reasoning.probes import (
    probe_line_exists, probe_snippet_present, probe_fact_exists,
    probe_variable_exists, probe_read_before_write,
)
from reasoning.adapters import finding_to_diagnostic, findings_to_chat
from reasoning.compiler_facts import _parse_json_diagnostics, _parse_text_diagnostics


# ── Helpers ────────────────────────────────────────────────────────────

def _make_bundle(source="int main() { return 0; }", facts=None, declarations=None, dataflow_events=None):
    lines = source.splitlines()
    return FactBundle(
        facts=facts or [],
        source_lines=lines,
        declarations=declarations or [],
        dataflow_events=dataflow_events or {},
    )


# ── Compiler facts parsing ─────────────────────────────────────────────

class TestCompilerFactsParsing(unittest.TestCase):

    def test_json_diagnostic_with_note_children(self):
        gcc_json = json.dumps([{
            "kind": "warning",
            "message": "'sum' is used uninitialized",
            "option": "-Wuninitialized",
            "children": [{"kind": "note", "message": "'sum' was declared here",
                          "locations": [{"caret": {"file": "t.cpp", "line": 12, "column": 9}}]}],
            "locations": [{"caret": {"file": "t.cpp", "line": 17, "column": 13}}],
        }])
        facts = _parse_json_diagnostics(gcc_json, "t.cpp")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].kind, "warning")
        self.assertEqual(facts[0].option, "-Wuninitialized")
        self.assertEqual(facts[0].line, 17)
        self.assertEqual(len(facts[0].children), 1)
        self.assertEqual(facts[0].children[0].kind, "note")
        self.assertEqual(facts[0].children[0].line, 12)

    def test_text_diagnostic_groups_notes(self):
        stderr = (
            "t.cpp:17:13: warning: 'sum' is used uninitialized [-Wuninitialized]\n"
            "t.cpp:12:9: note: 'sum' was declared here\n"
        )
        facts = _parse_text_diagnostics(stderr, "t.cpp")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].line, 17)
        self.assertEqual(len(facts[0].children), 1)
        self.assertEqual(facts[0].children[0].line, 12)

    def test_empty_stderr(self):
        self.assertEqual(_parse_text_diagnostics("", "t.cpp"), [])
        self.assertEqual(_parse_json_diagnostics("[]", "t.cpp"), [])


# ── Tier policy ────────────────────────────────────────────────────────

class TestTierPolicy(unittest.TestCase):

    def test_compiler_warning_caps_at_definite_bug(self):
        bundle = _make_bundle(facts=[
            Fact(id="F1", source="compiler", kind="warning", line=17,
                 message="'sum' is used uninitialized", option="-Wuninitialized"),
        ])
        c = Candidate(claim="sum uninitialized", kind="uninitialized",
                       lines=[17], snippet="sum += arr[i];",
                       fact_ids=["F1"], proposed_tier="definite_bug")
        c.gate_status = "pass"
        c.refute_verdict = "holds"
        tier = assign_tier(c, bundle)
        self.assertEqual(tier, Tier.DEFINITE_BUG)

    def test_intent_dependent_capped_at_likely_bug(self):
        bundle = _make_bundle(facts=[
            Fact(id="F1", source="compiler", kind="warning", line=17,
                 message="'sum' is used uninitialized", option="-Wuninitialized"),
        ])
        c = Candidate(claim="sum should be 0", kind="uninitialized",
                       lines=[17], snippet="sum += arr[i];",
                       fact_ids=["F1"], proposed_tier="definite_bug",
                       depends_on_intent=True)
        c.gate_status = "pass"
        c.refute_verdict = "holds"
        tier = assign_tier(c, bundle)
        self.assertEqual(tier, Tier.LIKELY_BUG)

    def test_no_evidence_caps_at_possible_issue(self):
        bundle = _make_bundle()
        c = Candidate(claim="possible bug", kind="logic", lines=[5],
                       snippet="x = 1;", proposed_tier="definite_bug")
        c.gate_status = "pass"
        c.refute_verdict = "holds"
        tier = assign_tier(c, bundle)
        self.assertEqual(tier, Tier.POSSIBLE_ISSUE)

    def test_uncertain_refutation_downgrades(self):
        bundle = _make_bundle(facts=[
            Fact(id="F1", source="compiler", kind="warning", line=17,
                 message="'m' may be used uninitialized", option="-Wmaybe-uninitialized"),
        ])
        c = Candidate(claim="m uninitialized", kind="uninitialized",
                       lines=[17], snippet="m = x;",
                       fact_ids=["F1"], proposed_tier="likely_bug")
        c.gate_status = "pass"
        c.refute_verdict = "uncertain"
        tier = assign_tier(c, bundle)
        # 'may be' warning → likely_bug ceiling, uncertain refute → possible_issue
        self.assertEqual(tier, Tier.POSSIBLE_ISSUE)

    def test_all_tiers_have_hedging(self):
        for tier in Tier:
            self.assertIn(tier, HEDGING)


# ── Evidence gate ──────────────────────────────────────────────────────

class TestEvidenceGate(unittest.TestCase):

    def test_nonexistent_line_rejects(self):
        bundle = _make_bundle("int main() { return 0; }")
        c = Candidate(claim="bug", kind="logic", lines=[999], snippet="x")
        result = check_candidate(c, bundle)
        self.assertEqual(result.gate_status, "reject")

    def test_valid_candidate_passes(self):
        bundle = _make_bundle(
            "int main() {\n    int sum;\n    sum += 1;\n    return 0;\n}",
            facts=[Fact(id="F1", source="compiler", kind="warning", line=3,
                       message="uninitialized")],
        )
        c = Candidate(claim="sum uninitialized", kind="uninitialized",
                       lines=[3], snippet="sum += 1;", fact_ids=["F1"])
        result = check_candidate(c, bundle)
        self.assertEqual(result.gate_status, "pass")

    def test_missing_snippet_rejects(self):
        bundle = _make_bundle("int main() { return 0; }")
        c = Candidate(claim="bug", kind="logic", lines=[1],
                       snippet="this_does_not_exist_at_all_anywhere()")
        result = check_candidate(c, bundle)
        self.assertEqual(result.gate_status, "reject")

    def test_missing_fact_id_downgrades(self):
        bundle = _make_bundle(
            "int main() { return 0; }",
            facts=[Fact(id="F1", source="compiler", kind="warning", line=1, message="x")],
        )
        c = Candidate(claim="bug", kind="logic", lines=[1],
                       snippet="return 0", fact_ids=["F99"])
        result = check_candidate(c, bundle)
        self.assertEqual(result.gate_status, "downgrade")


# ── Probes ─────────────────────────────────────────────────────────────

class TestProbes(unittest.TestCase):

    def test_line_exists(self):
        bundle = _make_bundle("line1\nline2\nline3")
        self.assertEqual(probe_line_exists(bundle, 2), "confirmed")
        self.assertEqual(probe_line_exists(bundle, 5), "contradicted")

    def test_snippet_present(self):
        bundle = _make_bundle("int main() {\n    int sum;\n    return 0;\n}")
        self.assertEqual(probe_snippet_present(bundle, "int sum;", 2), "confirmed")
        self.assertEqual(probe_snippet_present(bundle, "int xyz;", 2), "contradicted")

    def test_fact_exists(self):
        bundle = _make_bundle(facts=[Fact(id="F1", source="c", kind="w", line=1, message="x")])
        self.assertEqual(probe_fact_exists(bundle, "F1"), "confirmed")
        self.assertEqual(probe_fact_exists(bundle, "F99"), "contradicted")

    def test_read_before_write_confirmed(self):
        bundle = _make_bundle(
            "int main() {\n    int x;\n    x += 1;\n}",
            declarations=[{"name": "x", "line": 2, "has_initializer": False}],
            dataflow_events={"x": [
                {"kind": "declare", "line": 2, "has_initializer": False},
                {"kind": "read_write", "line": 3},
            ]},
        )
        self.assertEqual(probe_read_before_write(bundle, "x", 3), "confirmed")

    def test_read_before_write_contradicted_by_init(self):
        bundle = _make_bundle(
            "int main() {\n    int x = 0;\n    x += 1;\n}",
            declarations=[{"name": "x", "line": 2, "has_initializer": True}],
            dataflow_events={"x": [
                {"kind": "declare", "line": 2, "has_initializer": True},
                {"kind": "read_write", "line": 3},
            ]},
        )
        self.assertEqual(probe_read_before_write(bundle, "x", 3), "contradicted")


# ── Patching & Verification ───────────────────────────────────────────

class TestPatching(unittest.TestCase):

    def test_apply_single_edit(self):
        lines = ["line1", "line2", "line3"]
        edits = [PatchEdit(start_line=2, end_line=2, replacement="new_line2")]
        patched, diff = apply_edits(lines, edits)
        self.assertEqual(patched, ["line1", "new_line2", "line3"])
        self.assertIn("new_line2", diff)

    def test_out_of_range_edit_unchanged(self):
        lines = ["line1"]
        edits = [PatchEdit(start_line=5, end_line=5, replacement="x")]
        patched, diff = apply_edits(lines, edits)
        self.assertEqual(patched, lines)

    def test_v0_checks_overlap(self):
        lines = ["a", "b", "c", "d"]
        edits = [PatchEdit(1, 2, "x"), PatchEdit(2, 3, "y")]
        status, _ = check_v0_applies(lines, edits)
        self.assertEqual(status, "failed")

    def test_v1_scope_check(self):
        edits = [PatchEdit(5, 5, "fix")]
        status, _ = check_v1_scope(edits, [5, 6])
        self.assertEqual(status, "passed")
        status, _ = check_v1_scope(edits, [20, 25])
        self.assertEqual(status, "failed")


class TestVerification(unittest.TestCase):

    def test_full_v0_v1_v2_pass(self):
        lines = ["#include <iostream>", "int main() {", "    int x;", "    return 0;", "}"]
        edits = [PatchEdit(3, 3, "    int x = 0;")]
        report = verify_patch(lines, edits, target_lines=[3])
        self.assertEqual(report.v0_applies, "passed")
        self.assertEqual(report.v1_scope, "passed")
        self.assertIn(report.v2_security, ("passed", "skipped"))


# ── Adapters ───────────────────────────────────────────────────────────

class TestAdapters(unittest.TestCase):

    def test_finding_to_diagnostic_maps_tier(self):
        f = Finding(tier=Tier.DEFINITE_BUG, kind="uninitialized",
                    lines=[17], title="Bug", explanation="sum is uninitialized")
        d = finding_to_diagnostic(f)
        self.assertEqual(d["category"], "BUG")
        self.assertEqual(d["line"], 17)
        self.assertEqual(d["source"], "reasoning")

    def test_findings_to_chat_empty(self):
        from reasoning.models import ReasoningResult
        result = ReasoningResult()
        text = findings_to_chat(result)
        self.assertIn("No issues found", text)


# ── Sibling scope regression (H4 fix) ─────────────────────────────────

class TestShadowingFix(unittest.TestCase):

    def test_sibling_for_loops_no_false_positive(self):
        """Two sequential for(int i...) loops must NOT warn about shadowing."""
        import diagnostic_engine as diag
        code = """
int main() {
    for (int i = 0; i < 3; i++) {}
    for (int i = 0; i < 5; i++) {}
    return 0;
}
"""
        diagnostics = diag.check_variable_shadowing(code)
        shadowing_warnings = [d for d in diagnostics if "shadow" in d["title"].lower()
                              or "duplicate" in d["title"].lower()]
        self.assertEqual(len(shadowing_warnings), 0,
                         f"Sibling for-loop 'i' should not produce shadowing warnings, got: {shadowing_warnings}")

    def test_true_shadowing_still_warns(self):
        """Genuinely nested shadowing should still produce a warning."""
        import diagnostic_engine as diag
        code = """
int main() {
    int x = 10;
    {
        int x = 20;
    }
    return 0;
}
"""
        diagnostics = diag.check_variable_shadowing(code)
        shadow_warnings = [d for d in diagnostics if "shadow" in d["title"].lower()]
        self.assertGreater(len(shadow_warnings), 0,
                           "Nested shadowing of 'x' should produce a warning")


# ── Generic identifier removal (H3) ───────────────────────────────────

class TestGenericIdentifierRemoval(unittest.TestCase):

    def test_generic_names_no_longer_flagged(self):
        """H3 removal: single-letter variables and generic names should produce no diagnostics."""
        import diagnostic_engine as diag
        code = """
int main() {
    int a = 1;
    int b = 2;
    int result = a + b;
    return 0;
}
"""
        diagnostics = diag.check_identifier_quality(code)
        self.assertEqual(len(diagnostics), 0,
                         f"check_identifier_quality should return empty list, got: {diagnostics}")


if __name__ == "__main__":
    unittest.main()
