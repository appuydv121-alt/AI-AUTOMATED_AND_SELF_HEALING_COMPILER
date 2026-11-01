"""Unit tests for efficiency_agent.py."""
import pytest
from unittest.mock import patch
from efficiency_agent import run_efficiency_profile


def test_efficiency_profile_with_mocked_llm():
    code = "int main() { for(int i=0; i<n; i++) for(int j=0; j<n; j++) sum++; }"
    mock_llm_response = (
        '{"est_big_o": "O(N^2)", "hotspots": [{"line": 1, "complexity": "O(N^2)", '
        '"reason": "Quadratic nested loop", "suggested_change": "Use prefix sums or hash map"}]}',
        "mock_provider",
        "mock_model"
    )

    with patch("efficiency_agent._llm.generate_task", return_value=mock_llm_response):
        res = run_efficiency_profile(code, {"profile_metrics": {"median_time_ms": 12.5}}, language="cpp")
        assert res.est_big_o == "O(N^2)"
        assert len(res.hotspots) == 1
        assert res.hotspots[0].complexity == "O(N^2)"


def test_efficiency_profile_fallback():
    code = "#include <iostream>\nint main() { std::cin >> x; std::cout << x; }"
    with patch("efficiency_agent._llm.generate_task", side_effect=RuntimeError("LLM Offline")):
        res = run_efficiency_profile(code, {}, language="cpp")
        assert len(res.hotspots) >= 1
        assert "ios_base" in res.hotspots[0].suggested_change

