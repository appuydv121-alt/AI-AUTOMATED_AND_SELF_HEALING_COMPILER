import pytest
from complexity_engine import analyze_algorithmic_complexity


def test_user_snippet_sort_inside_loop():
    code = """#include <iostream>
#include <vector>
#include <algorithm>
using namespace std;

int main() {
    vector<int> arr = {8, 3, 5, 1, 9, 2};

    for (int i = 0; i < arr.size(); i++) {
        sort(arr.begin(), arr.end());
        cout << arr[0] << endl;
    }
    return 0;
}
"""
    res = analyze_algorithmic_complexity(code, language="cpp")
    assert res.time_complexity == "O(N² log N)"
    assert res.space_complexity in ("O(N)", "O(log N)")
    assert len(res.hotspots) > 0
    assert "sort" in res.hotspots[0]["reason"].lower()


def test_linear_loop():
    code = """int main() {
    for (int i = 0; i < n; i++) {
        x += i;
    }
}"""
    res = analyze_algorithmic_complexity(code, language="cpp")
    assert res.time_complexity == "O(N)"
    assert res.space_complexity == "O(1)"


def test_nested_loops():
    code = """int main() {
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            sum += a[i][j];
        }
    }
}"""
    res = analyze_algorithmic_complexity(code, language="cpp")
    assert res.time_complexity == "O(N²)"


def test_sort_outside_loop():
    code = """int main() {
    sort(arr.begin(), arr.end());
    for (int i = 0; i < arr.size(); i++) {
        cout << arr[i] << " ";
    }
}"""
    res = analyze_algorithmic_complexity(code, language="cpp")
    assert res.time_complexity == "O(N log N)"


def test_recursive_fibonacci():
    code = """int fib(int n) {
    if (n <= 1) return n;
    return fib(n - 1) + fib(n - 2);
}"""
    res = analyze_algorithmic_complexity(code, language="cpp")
    assert res.time_complexity == "O(2^N)"
    assert res.space_complexity == "O(N)"

