"""Unit tests for tool_runner.py."""
import os
import shutil
import subprocess

import pytest
from tool_runner import (
    run_static_toolchain,
    run_sanitizer_analysis,
    measure_execution_profile,
    collect_empirical_evidence,
)
from unittest.mock import patch


def _gpp_ready():
    return bool(shutil.which("g++") or shutil.which("clang++"))


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


@pytest.mark.skipif(not _gpp_ready(), reason="g++ compiler is not available")
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


def test_sanitizer_analysis_uses_the_container_runner():
    from compiler_service import RunResult

    with patch(
        "compiler_service.compile_and_run",
        return_value=RunResult(0, "", "", exit_code=0),
    ) as run_code:
        assert run_sanitizer_analysis("int main() { return 0; }") == []

    assert run_code.call_args.kwargs["sanitizers"] is True
