from test_generator_agent import generate_test_matrix
from compiler_service import compile_and_run

code = """#include <iostream>
#include <vector>
using namespace std;

int main() {
    int n;
    cin >> n;

    vector<int> arr(n);

    for (int i = 0; i < n; i++)
        cin >> arr[i];

    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n - 1; j++) {
            if (arr[j] > arr[j + 1]) {
                swap(arr[j], arr[j + 1]);
            }
        }
    }

    for (int x : arr)
        cout << x << " ";

    return 0;
}"""

tests = generate_test_matrix(code, baseline_stdin="5\n4 2 5 1 3\n")
for t in tests:
    print("Test:", t.name, "Stdin:", repr(t.stdin))
    res = compile_and_run(code, stdin_input=t.stdin, language="cpp")
    print("  Status:", res[0], "Stdout:", repr(res[1]), "Stderr:", repr(res[2]))

