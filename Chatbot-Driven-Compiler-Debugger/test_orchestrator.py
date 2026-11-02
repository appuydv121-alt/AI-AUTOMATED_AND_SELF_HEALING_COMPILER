"""Unit tests for orchestrator.py."""
import pytest
from types import SimpleNamespace
from unittest.mock import patch

from compiler_service import RunResult
import orchestrator
from orchestrator import (
    OrchestratorResult,
    OrchestratorStateMachine,
    _compare_test_case,
    _extract_code_block,
    _quality_does_not_regress,
)
from test_generator_agent import TestCase as GeneratedTestCase


@pytest.mark.parametrize(
    ("response", "language", "expected"),
    [
        ("Here is the fix:\n```cpp\nint main() {}\n```", "cpp", "int main() {}"),
        ("```c++\nint main() {}\n```", "c++", "int main() {}"),
        ("```python\nprint('ok')\n```", "python", "print('ok')"),
        ("Sure, here is the code:\nint main() {}", "cpp", "int main() {}"),
    ],
)
def test_extract_code_block(response, language, expected):
    assert _extract_code_block(response, language) == expected


def test_extract_code_block_returns_empty_for_blank_response():
    assert _extract_code_block("  \n", "cpp") == ""


def test_equivalence_gate_rejects_candidate_if_reference_times_out():
    reference = RunResult(-1, "partial", "timed out", exit_code=-1, timed_out=True)
    candidate = RunResult(0, "optimized output", "", exit_code=0)

    passed, reason = _compare_test_case(reference, candidate, "large workload")

    assert not passed
    assert "reference program timed out" in reason.lower()


def test_equivalence_gate_rejects_candidate_if_reference_fails():
    reference = RunResult(2, "", "crashed", exit_code=1)
    candidate = RunResult(0, "", "", exit_code=0)

    passed, reason = _compare_test_case(reference, candidate, "edge case")

    assert not passed
    assert "did not complete successfully" in reason


def test_equivalence_gate_requires_full_exact_output():
    reference = RunResult(0, "answer\n", "", exit_code=0)
    candidate = RunResult(0, "answer", "", exit_code=0)

    passed, reason = _compare_test_case(reference, candidate, "formatting")

    assert not passed
    assert "output discrepancy" in reason.lower()


def test_equivalence_gate_rejects_truncated_output():
    reference = RunResult(0, "same preview", "", exit_code=0, output_limited=True)
    candidate = RunResult(0, "same preview", "", exit_code=0)

    passed, reason = _compare_test_case(reference, candidate, "large output")

    assert not passed
    assert "capture limit" in reason.lower()


def test_quality_gate_does_not_trade_off_security_for_efficiency():
    candidate = {"security_score": 100.0, "efficiency_score": 70.0}
    reference = {"security_score": 90.0, "efficiency_score": 80.0}

    assert not _quality_does_not_regress(candidate, reference)


def test_deterministic_hardening_does_not_add_cosmetic_io_changes():
    code = """#include <iostream>
using namespace std;
int main() {
    int sum = 0;
    for (int i = 0; i < 100; ++i) {
        sum += i;
    }
    cout << sum << endl;
    return 0;
}"""

    hardened = OrchestratorStateMachine(code)._apply_deterministic_hardening(code)

    assert hardened == code


@pytest.mark.parametrize(
    ("optimizer_stage", "expected_source"),
    [
        ("cached", "cached response; no model inference was run"),
        ("requesting", "live model request"),
    ],
)
def test_orchestrator_does_not_accept_candidate_when_reference_times_out(optimizer_stage, expected_source):
    original = "int main() { return 0; }"
    optimized = "int main() { return 1; }"
    quality = {"security_score": 50.0, "efficiency_score": 50.0, "deductions": []}
    runs = iter([
        RunResult(0, "candidate output", "", exit_code=0),
        RunResult(-1, "", "timed out", exit_code=-1, timed_out=True),
        RunResult(0, "candidate output", "", exit_code=0),
    ])
    llm_response = f"```cpp\n{optimized}\n```", "test-provider", "test-model"

    def optimizer_response(task, *args, **kwargs):
        if task == "optimal_code":
            kwargs["progress_callback"]({"stage": optimizer_stage})
        return llm_response

    with (
        patch.object(orchestrator, "collect_empirical_evidence", return_value={"tool_findings": [], "profile_metrics": {}}),
        patch.object(orchestrator, "compute_code_quality_metrics", return_value=quality),
        patch.object(orchestrator, "retrieve_similar_experiences", return_value=[]),
        patch.object(orchestrator, "run_security_audit", return_value=[]),
        patch.object(
            orchestrator,
            "run_efficiency_profile",
            return_value=SimpleNamespace(est_big_o="O(1)", hotspots=[]),
        ),
        patch.object(orchestrator, "generate_test_matrix", return_value=[GeneratedTestCase("large", "", "large workload")]),
        patch.object(orchestrator._llm, "generate_task", side_effect=optimizer_response),
        patch.object(orchestrator, "run_security_guardrail", return_value=(True, "")),
        patch.object(orchestrator, "run_sanitizer_analysis", return_value=[]),
        patch.object(orchestrator, "compile_and_run", side_effect=lambda *args, **kwargs: next(runs)),
        patch.object(
            orchestrator,
            "measure_execution_profile",
            return_value=SimpleNamespace(median_time_ms=0.0, peak_memory_kb=0.0),
        ),
    ):
        result = OrchestratorStateMachine(original, max_cycles=1).run()

    assert not result.success
    assert result.optimized_code == original
    assert result.verification_status == "Needs Review"
    assert result.tests_evaluated == 1
    assert result.tests_passed == 0
    assert "equivalence was not established" in " ".join(result.changes_summary).lower()
    optimizer_trace = [
        entry for entry in result.agent_trace
        if entry["agent"] == "OptimizerAgent"
    ]
    assert any(expected_source in entry["details"] for entry in optimizer_trace)
    assert any("provider/model: test-provider (test-model)" in entry["details"] for entry in optimizer_trace)


def test_orchestrator_initialization_and_transitions():
    code = """#include <iostream>
int main() {
    std::cout << "Hello" << std::endl;
    return 0;
}"""
    def mock_generate_task(task, *args, **kwargs):
        if task == "security_audit":
            return '{"findings": []}', "mock_prov", "mock_mdl"
        elif task == "efficiency_profile":
            return '{"hotspots": [], "est_big_o": "O(1)"}', "mock_prov", "mock_mdl"
        elif task == "test_generation":
            return '{"test_inputs": [{"name": "empty", "stdin": "", "rationale": "test"}]}', "mock_prov", "mock_mdl"
        elif task == "optimal_code":
            return (
                "```cpp\n#include <iostream>\nint main() {\n    std::cout << \"Hello\\n\";\n    return 0;\n}\n```",
                "mock_prov",
                "mock_mdl"
            )
        return "{}", "mock_prov", "mock_mdl"

    with patch("llm_provider.generate_task", side_effect=mock_generate_task):
        orch = OrchestratorStateMachine(code=code, language="cpp", max_cycles=1, time_budget_s=15.0)
        res = orch.run()
        assert isinstance(res, OrchestratorResult)
        assert len(res.state_transitions) >= 3
        states = [t["state_to"] for t in res.state_transitions]
        assert "COLLECT_TOOLS" in states
        assert "RETRIEVE_MEMORY" in states
