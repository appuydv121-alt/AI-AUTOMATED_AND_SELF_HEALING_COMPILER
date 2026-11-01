"""Unit tests for security_agent.py."""
import pytest
from unittest.mock import patch
from security_agent import run_security_audit, SecurityFinding


def test_security_audit_with_mocked_llm():
    code = "int main() { char buf[10]; gets(buf); return 0; }"
    mock_llm_response = (
        '{"findings": [{"id": "SEC-1", "severity": "CRITICAL", "line": 1, '
        '"cwe": "CWE-120", "evidence": "gets(buf) is vulnerable to buffer overflow", '
        '"minimal_fix": "Use fgets(buf, sizeof(buf), stdin);"}]}',
        "mock_provider",
        "mock_model"
    )

    with patch("security_agent._llm.generate_task", return_value=mock_llm_response):
        findings = run_security_audit(code, {"tool_findings": []}, language="cpp")
        assert len(findings) == 1
        assert findings[0].severity == "CRITICAL"
        assert findings[0].cwe == "CWE-120"
        assert "gets" in findings[0].evidence


def test_security_audit_fallback_to_tool_findings():
    code = "int main() { int uninit; return uninit; }"
    mock_evidence = {
        "tool_findings": [
            {
                "tool_name": "ast-engine",
                "category": "memory",
                "severity": "CRITICAL",
                "line": 1,
                "message": "Uninitialized variable 'uninit'",
                "evidence": "Line 1: uninit read before assignment",
            }
        ]
    }
    with patch("security_agent._llm.generate_task", side_effect=RuntimeError("LLM Offline")):
        findings = run_security_audit(code, mock_evidence, language="cpp")
        assert len(findings) == 1
        assert findings[0].severity == "CRITICAL"
        assert "uninitialized" in findings[0].evidence.lower()
