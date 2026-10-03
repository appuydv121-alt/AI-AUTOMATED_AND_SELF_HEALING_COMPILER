import unittest

from cpp_declarations import extract_declarations, tokenize


class TestCppTokenizer(unittest.TestCase):

    def test_ignores_comments_and_string_contents(self):
        source = '''// int fake = 4;
const char* text = "int also_fake = 3;";
/* bool nope = false; */
int real = 2;
'''
        declarations = extract_declarations(source)
        self.assertEqual([declaration.name for declaration in declarations], ["text", "real"])

    def test_retains_source_spans_and_line_numbers(self):
        source = "int main() {\n  double price = 4.5;\n}\n"
        declaration = extract_declarations(source)[0]
        self.assertEqual(declaration.name, "price")
        self.assertEqual(declaration.type_family, "FLOAT")
        self.assertEqual(declaration.line, 2)
        self.assertEqual(source[slice(*declaration.type_span)], "double")
        self.assertEqual(source[slice(*declaration.init_span)], "4.5")

    def test_recognizes_qualified_and_template_types(self):
        declarations = extract_declarations(
            "std::string name = \"Ada\"; std::vector<int> scores; std::vector<std::string> names;"
        )
        self.assertEqual([item.name for item in declarations], ["name", "scores", "names"])
        self.assertEqual([item.type_family for item in declarations], ["TEXT", "COLLECTION", "COLLECTION"])

    def test_recognizes_fixed_size_arrays_without_making_them_fixable(self):
        declarations = extract_declarations('int main() { char name[20]; int scores[5]; }')
        self.assertEqual([item.name for item in declarations], ["name", "scores"])
        self.assertEqual([item.type_family for item in declarations], ["TEXT", "COLLECTION"])
        self.assertTrue(all(item.is_array for item in declarations))

    def test_tokenizer_skips_directives_and_preserves_raw_literals(self):
        tokens = tokenize('#include <string>\nconst char* raw = R"tag(int fake;)tag";')
        self.assertFalse(any(token.text == "include" for token in tokens))
        self.assertTrue(any(token.kind == "string" and "int fake;" in token.text for token in tokens))

    def test_distinguishes_member_parameter_loop_and_local_scopes(self):
        source = '''
struct Person { int age = 20; };
int main(int name) {
    for (int age = 0; age < 2; ++age) {}
    int label = 1;
}
'''
        declarations = extract_declarations(source)
        age_scopes = [item.scope for item in declarations if item.name == "age"]
        by_name = {item.name: item for item in declarations if item.name != "age"}
        self.assertEqual(age_scopes, ["member", "loop"])
        self.assertEqual(by_name["name"].scope, "param")
        self.assertEqual(by_name["label"].scope, "local")



if __name__ == "__main__":
    unittest.main()