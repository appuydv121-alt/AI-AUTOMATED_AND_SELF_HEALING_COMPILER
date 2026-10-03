import unittest
import os
import tempfile

from semantic_analyzer import (
    analyze_semantics,
    apply_safe_fixes,
    load_dictionary,
    normalize,
    resolve_category,
)


class TestSemanticDictionary(unittest.TestCase):

    def test_dictionary_has_broad_seed_and_valid_compounds(self):
        dictionary = load_dictionary()
        names = {name for category in dictionary.raw["categories"].values() for name in category["names"]}
        self.assertGreaterEqual(len(names), 500)
        self.assertTrue(all(entry["category"] in dictionary.raw["categories"] for entry in dictionary.raw["compounds"].values()))
        names_by_category = {}
        for category, data in dictionary.raw["categories"].items():
            for name in data["names"]:
                names_by_category.setdefault(name, set()).add(category)
        conflicts = [
            name for name, categories in names_by_category.items()
            if len(categories) > 1 and name not in dictionary.raw["overrides"]
        ]
        self.assertEqual(conflicts, [])

    def test_normalizes_common_styles_and_abbreviations(self):
        self.assertEqual(normalize("firstName"), ("first", "name"))
        self.assertEqual(normalize("USER_ID"), ("user", "id"))
        self.assertEqual(normalize("student_age"), ("student", "age"))

    def test_compound_keys_normalize_to_the_dictionary_index(self):
        dictionary = load_dictionary()
        abbreviations = tuple(sorted(dictionary.raw.get("abbreviations", {}).items()))
        for compound in dictionary.compound_index:
            with self.subTest(compound=compound):
                self.assertEqual("".join(normalize(compound, abbreviations)), compound)

    def test_missing_dictionary_uses_builtin_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_path = os.path.join(directory, "missing.json")
            fallback = load_dictionary(missing_path)
        self.assertTrue(fallback.fallback)
        report = analyze_semantics("int main() { int name = 1; }", dictionary=fallback)
        self.assertTrue(report.diagnostics)

    def test_compounds_and_boolean_prefixes_resolve(self):
        self.assertEqual(resolve_category("brandName")[0], "TEXT")
        self.assertEqual(resolve_category("studentCount")[0], "COUNT")
        self.assertEqual(resolve_category("isStudent")[0], "BOOLEAN")


class TestSemanticAnalysis(unittest.TestCase):

    def test_clear_name_type_mismatch_is_warning_and_high_confidence(self):
        report = analyze_semantics("int main() { int name = 10; }")
        self.assertEqual(len(report.diagnostics), 1)
        diagnostic = report.diagnostics[0]
        self.assertEqual(diagnostic.expected_category, "TEXT")
        self.assertEqual(diagnostic.confidence_label, "HIGH")
        self.assertEqual(diagnostic.severity, "warning")
        self.assertIsNotNone(diagnostic.fix)

    def test_correct_and_ambiguous_declarations_stay_quiet(self):
        source = '''
int age = 20;
std::string name = "Ada";
bool isActive = true;
double price = 99.99;
int studentCount = 100;
int value = 10;
'''
        self.assertEqual(analyze_semantics(source).diagnostics, [])

    def test_numeric_text_in_age_is_suggestion_only(self):
        report = analyze_semantics('std::string age = "20";')
        self.assertEqual(len(report.diagnostics), 1)
        self.assertEqual(report.diagnostics[0].confidence_label, "MEDIUM")
        self.assertIsNone(report.diagnostics[0].fix)

    def test_boolean_integer_literal_can_be_fixed_conservatively(self):
        source = "int main() { int isActive = 1; }"
        report = analyze_semantics(source)
        corrected, applied = apply_safe_fixes(source, report)
        self.assertEqual(corrected, "int main() { bool isActive = true; }")
        self.assertEqual(len(applied), 1)

    def test_boolean_literal_outside_zero_or_one_is_reported_without_fix(self):
        report = analyze_semantics("bool isStudent = 10;")
        self.assertEqual(len(report.diagnostics), 1)
        self.assertEqual(report.diagnostics[0].expected_category, "BOOLEAN")
        self.assertIsNone(report.diagnostics[0].fix)

    def test_text_literal_and_include_added_without_reformatting(self):
        source = '#include <iostream>\nint main() { int name = 10; }\n'
        report = analyze_semantics(source)
        corrected, applied = apply_safe_fixes(source, report)
        self.assertEqual(corrected, '#include <iostream>\n#include <string>\nint main() { std::string name = "10"; }\n')
        self.assertEqual(len(applied), 1)

    def test_correction_preserves_crlf_line_endings(self):
        source = '#include <iostream>\r\nint main() { int name = 10; }\r\n'
        report = analyze_semantics(source)
        corrected, _ = apply_safe_fixes(source, report)
        self.assertIn('#include <string>\r\n', corrected)
        self.assertNotIn('\n', corrected.replace('\r\n', ''))

    def test_multideclarator_never_gets_automatic_fix(self):
        report = analyze_semantics("int name = 10, age = 20;")
        self.assertTrue(report.diagnostics)
        self.assertTrue(all(item.fix is None for item in report.diagnostics))

    def test_member_parameter_and_loop_variables_never_get_automatic_fix(self):
        source = '''
struct Person { int firstName = 10; };
int main(int firstName) {
    for (int firstName = 10; firstName < 20; ++firstName) {}
}
'''
        report = analyze_semantics(source)
        self.assertGreaterEqual(len(report.diagnostics), 2)
        self.assertTrue(all(item.fix is None for item in report.diagnostics))

    def test_unproven_initializer_and_collection_type_are_suggestion_only(self):
        source = '''
int main() {
    int name = getName();
    std::vector<int> nameList;
    int firstName = 10;
    firstName = getValue();
}
'''
        report = analyze_semantics(source)
        self.assertGreaterEqual(len(report.diagnostics), 2)
        self.assertTrue(all(item.fix is None for item in report.diagnostics))

    def test_later_reference_after_another_declaration_blocks_fix(self):
        source = "int main() { int firstName = 10; int other = 0; return firstName + other; }"
        report = analyze_semantics(source)
        first_name = next(item for item in report.diagnostics if item.variable == "firstName")
        self.assertIsNone(first_name.fix)

    def test_names_inside_comments_and_literals_are_ignored(self):
        source = '// int name = 10;\nconst char* text = "int age = 4;";\n'
        report = analyze_semantics(source)
        self.assertEqual(report.diagnostics, [])

    def test_known_correct_name_type_pairs_stay_silent(self):
        declarations = [
            "int age = 20;", 'std::string name = "Ada";', "double height = 180.5;",
            "double weight = 70.5;", "int size = 10;", 'std::string brandName = "Nike";',
            "bool isActive = true;", "double price = 99.99;", "int studentCount = 100;",
            "int code = 404;", 'std::string code = "READY";', 'std::string id = "007";',
            "int value = 100;", 'std::string value = "hello";', "int age = calculateAge();",
            'std::string zipCode = "90210";', 'std::string phone = "555";',
            "long timestamp = 1700000000;", "int rating = 5;", "double score = 9.5;",
            "int studentAge = 19;", "int employeeCount = 4;", "double productPrice = 2.5;",
            "bool hasPermission = false;", "std::string userEmail = \"a@b.test\";",
            "std::string phoneNumber = \"5551234\";", "std::string birthDate = \"2000-01-01\";",
            "int statusCode = 200;", "std::vector<int> scores;",
        ]
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                source = f"int main() {{ {declaration} }}"
                self.assertEqual(analyze_semantics(source).diagnostics, [])

    def test_unknown_user_types_are_skipped(self):
        report = analyze_semantics("Person name = makePerson();")
        self.assertEqual(report.diagnostics, [])

    def test_thousand_declarations_stay_within_analysis_budget(self):
        declarations = "\n".join(f"int name{index} = {index};" for index in range(1000))
        source = f"int main() {{\n{declarations}\n}}"
        report = analyze_semantics(source)
        self.assertEqual(report.stats["variables_seen"], 1000)
        self.assertLess(report.stats["elapsed_ms"], 250)


if __name__ == "__main__":
    unittest.main()