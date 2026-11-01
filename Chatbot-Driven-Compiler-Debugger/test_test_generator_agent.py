"""Unit tests for test_generator_agent.py."""
import pytest
from unittest.mock import patch
from test_generator_agent import generate_test_matrix, TestCase


def test_generate_test_matrix_with_mocked_llm():
    code = "int main() { int n; cin >> n; }"
    mock_llm_response = (
        '{"test_inputs": [{"name": "Zero", "stdin": "0", "rationale": "Zero test"}, '
        '{"name": "Negative", "stdin": "-5", "rationale": "Negative value"}]}',
        "mock_provider",
        "mock_model"
    )

    with patch("test_generator_agent._llm.generate_task", return_value=mock_llm_response):
        matrix = generate_test_matrix(code, baseline_stdin="10", language="cpp")
        assert len(matrix) >= 2
        assert any(c.name == "Baseline User Input" for c in matrix)
        assert any(c.stdin == "0" for c in matrix)


def test_generate_test_matrix_fallback():
    code = "int main() { return 0; }"
    with patch("test_generator_agent._llm.generate_task", side_effect=RuntimeError("Offline")):
        matrix = generate_test_matrix(code, baseline_stdin="", language="cpp")
        assert len(matrix) >= 5
        stdin_values = [c.stdin for c in matrix]
        assert "0" in stdin_values
        assert "1" in stdin_values

