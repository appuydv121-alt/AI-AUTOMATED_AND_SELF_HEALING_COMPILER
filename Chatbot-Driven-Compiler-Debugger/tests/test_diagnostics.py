import os
import shutil
import tempfile
import unittest

import diagnostic_engine as de
from secure_scan import run_security_guardrail, scan_all_security_issues

HAS_GPP = shutil.which("g++") is not None


class TestLocalStaticChecks(unittest.TestCase):

    def test_valid_code_no_issues(self):
        code = (
            "#include <iostream>\n"
            "using namespace std;\n"
            "int main() {\n"
            '    cout << "hello" << endl;\n'
            "    return 0;\n"
            "}\n"
        )
        self.assertEqual(de.run_local_static_checks(code), [])

    def test_empty_code_no_crash(self):
        self.assertEqual(de.run_local_static_checks(""), [])
        self.assertEqual(de.run_local_static_checks("   \n\n  "), [])

    def test_system_call_flagged_as_security(self):
        diagnostics = de.run_local_static_checks('int main() { system("pause"); return 0; }')
        self.assertTrue(any(d["category"] == "SECURITY_CRITICAL" for d in diagnostics))

    def test_rand_flagged_as_security_warning_not_error(self):
        diagnostics = de.run_local_static_checks("int main() { int x = rand(); return 0; }")
        self.assertTrue(any(d["category"] == "SECURITY_WARNING" for d in diagnostics))
        self.assertFalse(any(d["category"] == "ERROR" for d in diagnostics))

    def test_security_scanner_retains_first_match_gate(self):
        safe, warning = run_security_guardrail("system( rand()")
        self.assertFalse(safe)
        self.assertIn("system()", warning)
        self.assertEqual(len(scan_all_security_issues("system( rand()")), 2)

    def test_performance_header_is_diagnostic_not_security_block(self):
        code = "#include <bits/stdc++.h>\nint main() { return 0; }"
        safe, _ = run_security_guardrail(code)
        self.assertTrue(safe)
        self.assertTrue(any("PERFORMANCE:" in item["message"] for item in scan_all_security_issues(code)))

    def test_security_diagnostic_has_source_line(self):
        diagnostics = de.run_local_static_checks("int main() {\n  rand();\n}")
        self.assertEqual(diagnostics[0]["line"], 2)

    def test_valid_keyword_usage_no_false_positive(self):
        self.assertEqual(de.run_local_static_checks("int main() { int class_count = 5; return 0; }"), [])

    def test_malformed_incomplete_code_does_not_crash(self):
        diagnostics = de.run_local_static_checks('int main() { if (x > 0) { cout << "a"')
        self.assertTrue(any(d["category"] == "POTENTIAL_ISSUE" for d in diagnostics))

    def test_unterminated_string_is_potential_issue(self):
        diagnostics = de.run_local_static_checks('int main() { cout << "hello; return 0; }')
        self.assertTrue(any("Unterminated" in d["title"] or "Unclosed" in d["title"] for d in diagnostics))
        self.assertFalse(any(d["category"] == "ERROR" for d in diagnostics))

    def test_division_by_zero_is_not_flagged_precompile(self):
        code = "int main() { int total = 500; int count = 0; int avg = total / count; return avg; }"
        self.assertEqual(de.run_local_static_checks(code), [])

    def test_semantic_check_is_additive_and_can_be_disabled(self):
        code = "int main() { int name = 10; }"
        enabled = de.run_local_static_checks(code)
        disabled = de.run_local_static_checks(code, semantic_enabled=False)
        self.assertTrue(any(item["source"] == "semantic" for item in enabled))
        self.assertFalse(any(item["source"] == "semantic" for item in disabled))

    def test_semantic_minimum_confidence_is_applied(self):
        code = 'int main() { std::string age = "20"; }'
        low = de.run_local_static_checks(code, semantic_min_confidence=0.30)
        high = de.run_local_static_checks(code, semantic_min_confidence=0.75)
        self.assertTrue(any(item["source"] == "semantic" for item in low))
        self.assertFalse(any(item["source"] == "semantic" for item in high))


@unittest.skipUnless(HAS_GPP, "g++ not available on PATH in this environment")
class TestCompilerSyntaxCheck(unittest.TestCase):

    def setUp(self):
        self.work_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.work_dir, ignore_errors=True)

    def test_valid_code_produces_no_compiler_diagnostics(self):
        diagnostics, _ = de.run_compiler_syntax_check("int main() { return 0; }", self.work_dir)
        self.assertEqual(diagnostics, [])

    def test_missing_semicolon_is_confirmed_error(self):
        code = "int main() {\n  int total_marks = 95\n  return total_marks;\n}"
        diagnostics, _ = de.run_compiler_syntax_check(code, self.work_dir)
        self.assertTrue(any(d["category"] == "ERROR" for d in diagnostics))

    def test_undefined_variable_is_confirmed_error(self):
        diagnostics, _ = de.run_compiler_syntax_check(
            "int main() { return undefined_variable_xyz; }", self.work_dir
        )
        self.assertTrue(any(d["category"] == "ERROR" for d in diagnostics))

    def test_syntax_check_temp_file_is_cleaned_up_and_isolated(self):
        de.run_compiler_syntax_check("int main() { return 0; }", self.work_dir)
        self.assertFalse(os.path.exists(os.path.join(self.work_dir, "temp_source.cpp")))
        self.assertFalse(os.path.exists(os.path.join(self.work_dir, "temp_program")))
        self.assertEqual(os.listdir(self.work_dir), [])

    def test_large_file_completes_within_timeout(self):
        lines = ["#include <iostream>", "int main() {"]
        lines += [f'  std::cout << "{index}" << std::endl;' for index in range(5000)]
        lines += ["  return 0;", "}"]
        diagnostics, _ = de.run_compiler_syntax_check("\n".join(lines), self.work_dir)
        self.assertEqual(diagnostics, [])


class TestCompilerDiagnosticParsing(unittest.TestCase):

    def test_parses_unix_filename(self):
        diagnostics = de.parse_compiler_diagnostics("temp_source.cpp:7:4: error: expected ';'")
        self.assertEqual(diagnostics[0]["line"], 7)
        self.assertEqual(diagnostics[0]["category"], "ERROR")

    def test_parses_windows_drive_letter_filename(self):
        diagnostics = de.parse_compiler_diagnostics(
            r"C:\\work\\_diag_source.cpp:12:8: error: expected ';'"
        )
        self.assertEqual(diagnostics[0]["line"], 12)
        self.assertEqual(diagnostics[0]["message"], "expected ';'")


class TestDebounceLogic(unittest.TestCase):

    def test_new_or_changed_code_waits_for_quiet_window(self):
        state = {"diag_last_code": "int main(){}", "diag_last_time": 1000}
        self.assertFalse(de.should_run_debounced_check(state, "int main(){ }", now=1004))
        self.assertFalse(de.should_run_debounced_check(state, "int main(){}", now=1001))

    def test_quiet_buffer_runs_once(self):
        state = {"diag_last_code": "int main(){}", "diag_last_time": 1000}
        check_time = 1000 + de.SYNTAX_CHECK_DEBOUNCE_SECONDS + 0.1
        self.assertTrue(de.should_run_debounced_check(state, "int main(){}", now=check_time))
        state["diag_syntax_checked_code"] = "int main(){}"
        self.assertFalse(de.should_run_debounced_check(state, "int main(){}", now=check_time + 1))


class TestMergeAndRender(unittest.TestCase):

    def test_merge_deduplicates_identical_line_and_message(self):
        first = de.make_diagnostic("ERROR", "t", "same message", line=5, source="compiler")
        second = de.make_diagnostic("WARNING", "t2", "same message", line=5, source="local")
        self.assertEqual(len(de.merge_diagnostics([first], [second])), 1)

    def test_compiler_error_suppresses_semantic_warning_on_same_line(self):
        semantic = de.make_diagnostic("WARNING", "semantic", "possible mismatch", line=5, source="semantic")
        compiler = de.make_diagnostic("ERROR", "compiler", "syntax error", line=5, source="compiler")
        other_semantic = de.make_diagnostic("WARNING", "semantic", "other mismatch", line=6, source="semantic")
        merged = de.merge_diagnostics([semantic, other_semantic], [compiler])
        self.assertNotIn(semantic, merged)
        self.assertIn(other_semantic, merged)
        self.assertIn(compiler, merged)

    def test_merge_sorts_errors_before_suggestions(self):
        error = de.make_diagnostic("ERROR", "e", "err msg", line=2)
        suggestion = de.make_diagnostic("SUGGESTION", "s", "sug msg", line=1)
        self.assertEqual(de.merge_diagnostics([suggestion], [error])[0]["category"], "ERROR")

    def test_render_empty_state(self):
        self.assertIn("No immediate issues detected", de.render_diagnostics_markdown([]))

    def test_render_nonempty_diagnostics(self):
        diagnostics = de.run_local_static_checks('int main(){ system("pause"); cout << "a')
        self.assertGreater(len(de.render_diagnostics_markdown(diagnostics)), 0)

    def test_meaningful_identifiers_are_not_flagged(self):
        code = (
            'int main() { std::string name = "arpit"; '
            'int name1 = 1, age1 = 20, height1 = 170, weight1 = 65; return 0; }'
        )
        diagnostics = de.run_local_static_checks(code)
        self.assertFalse(any(d["category"] == "SUGGESTION" for d in diagnostics))
        self.assertFalse(any(d["category"] == "ERROR" for d in diagnostics))

    def test_numbered_generic_identifier_gets_suggestion_not_error(self):
        # H3 removal: generic identifiers like value1 are no longer flagged to prevent false positives
        diagnostics = de.run_local_static_checks("int main() { int value1 = 10; return value1; }")
        self.assertFalse(any(d["category"] == "SUGGESTION" for d in diagnostics))
        self.assertFalse(any(d["category"] == "ERROR" for d in diagnostics))

    def test_sibling_scope_variables_do_not_warn(self):
        # Sibling scopes (H4 fix): sequential for-loops with the same counter name should not warn
        code = "int main() { for (int i = 0; i < 3; i++) {} for (int i = 0; i < 5; i++) {} return 0; }"
        diagnostics = de.run_local_static_checks(code)
        self.assertFalse(any(d["category"] == "WARNING" and "shadow" in d["title"].lower() for d in diagnostics))

    def test_shadowed_variable_gets_warning(self):
        code = "int main() { int value = 10; { int value = 20; } return 0; }"
        diagnostics = de.run_local_static_checks(code)
        self.assertTrue(any(d["category"] == "WARNING" and "shadow" in d["title"].lower() for d in diagnostics))

    def test_duplicate_declaration_gets_warning(self):
        code = "int main() { int count = 1; int count = 2; return 0; }"
        diagnostics = de.run_local_static_checks(code)
        self.assertTrue(any(d["category"] == "WARNING" and "duplicate" in d["title"].lower() for d in diagnostics))


if __name__ == "__main__":
    unittest.main()