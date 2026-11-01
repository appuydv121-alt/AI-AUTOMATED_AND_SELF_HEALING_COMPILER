from orchestrator import OrchestratorStateMachine

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

orch = OrchestratorStateMachine(
    code=code,
    language="cpp",
    baseline_stdin="5\n4 2 5 1 3\n",
    max_cycles=1,
)
res = orch.run()
print("=== VERIFICATION STATUS ===")
print(res.verification_status)
print("=== OPTIMIZED CODE ===")
print(res.optimized_code)
print("=== METRICS BEFORE ===")
print("Efficiency:", res.metrics_before.get("efficiency_score"))
print("=== METRICS AFTER ===")
print("Efficiency:", res.metrics_after.get("efficiency_score"))
print("=== TESTS ===")
print(f"Passed {res.tests_passed}/{res.tests_evaluated}")
assert "sort(arr.begin(), arr.end());" in res.optimized_code
assert "swap" not in res.optimized_code
print("ALL ASSERTIONS PASSED!")

