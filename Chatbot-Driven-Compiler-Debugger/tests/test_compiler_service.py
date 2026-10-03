import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from compiler_service import compile_and_run, RunResult


class TestCompilerServiceStdin(unittest.TestCase):
    def test_run_result_tuple_compatibility(self):
        result = RunResult(0, "output text", "", exit_code=0, execution_time=0.05)
        # Verify 3-tuple unpacking
        status, stdout, stderr = result
        self.assertEqual(status, 0)
        self.assertEqual(stdout, "output text")
        self.assertEqual(stderr, "")
        # Verify rich attributes
        self.assertEqual(result.exit_code, 0)
        self.assertAlmostEqual(result.execution_time, 0.05)
        self.assertFalse(result.timed_out)

    def test_two_sum_sample_with_stdin(self):
        code = """#include <iostream>
#include <vector>
using namespace std;

int main() {
    int n;
    if (!(cin >> n)) return 0;
    vector<int> nums(n);
    for (int i = 0; i < n; i++) {
        cin >> nums[i];
    }
    int target = 7;
    for (int i = 0; i < n; i++) {
        for (int j = i + 1; j < n; j++) {
            if (nums[i] + nums[j] == target) {
                cout << "Pair: " << nums[i] << " + " << nums[j] << " = " << target << endl;
                return 0;
            }
        }
    }
    cout << "No pair found" << endl;
    return 0;
}
"""
        stdin_input = "5\n1 3 4 6 2\n"
        res = compile_and_run(code, stdin_input=stdin_input)
        self.assertEqual(res.status_code, 0)
        self.assertEqual(res.exit_code, 0)
        self.assertIn("Pair: 1 + 6 = 7", res.stdout)
        self.assertGreaterEqual(res.execution_time, 0.0)

    def test_stdin_input_update_produces_new_output(self):
        code = """#include <iostream>
using namespace std;

int main() {
    int a, b;
    if (cin >> a >> b) {
        cout << (a * b) << endl;
    }
    return 0;
}
"""
        res1 = compile_and_run(code, stdin_input="6 7\n")
        self.assertEqual(res1.status_code, 0)
        self.assertIn("42", res1.stdout)

        res2 = compile_and_run(code, stdin_input="9 9\n")
        self.assertEqual(res2.status_code, 0)
        self.assertIn("81", res2.stdout)

    def test_timeout_on_infinite_loop(self):
        code = """int main() {
    while (true) {}
    return 0;
}"""
        res = compile_and_run(code, timeout_run=2)
        self.assertEqual(res.status_code, -1)
        self.assertTrue(res.timed_out)
        self.assertIn("Execution timed out. Your program may be waiting for more input or stuck in an infinite loop.", res.stderr)

    def test_non_zero_exit_code(self):
        code = """int main() {
    return 42;
}"""
        res = compile_and_run(code)
        self.assertEqual(res.status_code, 2)
        self.assertEqual(res.exit_code, 42)
        self.assertIn("exit code 42", res.stderr)

    def test_very_large_input_truncated(self):
        code = """#include <iostream>
#include <string>
using namespace std;

int main() {
    string s;
    cin >> s;
    cout << s.length() << endl;
    return 0;
}"""
        # 150KB input
        large_input = "A" * (150 * 1024)
        res = compile_and_run(code, stdin_input=large_input)
        self.assertEqual(res.status_code, 0)
        # Should be capped at 100KB (102400)
        length_read = int(res.stdout.strip())
        self.assertEqual(length_read, 100 * 1024)


if __name__ == "__main__":
    unittest.main()
