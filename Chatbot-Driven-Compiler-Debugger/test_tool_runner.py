"""Unit tests for tool_runner.py."""
import pytest
from tool_runner import (
    run_static_toolchain,
    measure_execution_profile,
    collect_empirical_evidence,
)


def test_run_static_toolchain_detects_uninitialized_and_div_zero():
    code = """#include <iostream>
int main() {
    int uninit_var;
    uninit_var += 5;
    int zero = 10 / 0;
    return 0;
}"""
    findings = run_static_toolchain(code, language="cpp")
    messages = [f.message for f in findings]
    assert any("uninitialized" in m.lower() for m in messages)
    assert any("division by zero" in m.lower() for m in messages)


def test_measure_execution_profile_clean_code():
    code = """#include <iostream>
int main() {
    std::cout << 42 << std::endl;
    return 0;
}"""
    profile = measure_execution_profile(code, language="cpp", runs=2)
    assert profile.exit_code == 0
    assert profile.runs_count == 2
    assert profile.median_time_ms > 0
    assert profile.timed_out is False


def test_collect_empirical_evidence():
    code = "int main() { return 0; }"
    evidence = collect_empirical_evidence(code, language="cpp")
    assert "tool_findings" in evidence
    assert "profile_metrics" in evidence
    assert isinstance(evidence["total_flaws_found"], int)

