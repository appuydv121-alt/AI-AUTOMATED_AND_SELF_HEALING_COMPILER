from compiler_service import compile_and_run

code = """#include <iostream>
#include <vector>
using namespace std;
int main() {
    int n;
    cin >> n;
    vector<int> arr(n);
    for (int i = 0; i < n; i++) cin >> arr[i];
    for (int x : arr) cout << x << " ";
    return 0;
}
"""

res = compile_and_run(code, stdin_input="5\n10 20 30 40 50\n", language="cpp")
print("Status:", res[0])
print("Stdout:", res[1])

