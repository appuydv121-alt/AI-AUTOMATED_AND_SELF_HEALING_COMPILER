"""Unit tests for orchestrator.py."""
import pytest
from unittest.mock import patch
from orchestrator import OrchestratorStateMachine, OrchestratorResult


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
