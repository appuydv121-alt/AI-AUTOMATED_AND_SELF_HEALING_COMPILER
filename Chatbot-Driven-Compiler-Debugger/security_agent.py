"""security_agent.py — Evidence-Based Security Audit Agent.

Responsibilities:
  - Consumes empirical tool outputs and source code.
  - Discovers logic vulnerabilities, buffer safety issues, and insecure primitives.
  - Requires all reported issues to be supported by tool evidence or explicit code lines.
  - Outputs strictly typed JSON matching SECURITY_SCHEMA.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

import llm_provider as _llm
from agent_guard import build_guarded_prompt, validate_agent_json

SECURITY_SCHEMA = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "severity", "line", "cwe", "evidence", "minimal_fix"],
                "properties": {
                    "id": {"type": "string"},
                    "severity": {"type": "string", "enum": ["CRITICAL", "WARNING", "INFO"]},
                    "line": {"type": ["integer", "null"]},
                    "cwe": {"type": "string"},
                    "evidence": {"type": "string"},
                    "minimal_fix": {"type": "string"},
                },
            },
        },
    },
}


@dataclass
class SecurityFinding:
    id: str
    severity: str
    line: int | None
    cwe: str
    evidence: str
    minimal_fix: str


def run_security_audit(
    code: str,
    evidence: dict[str, Any],
    language: str = "cpp",
    provider_name: str | None = None,
    ollama_model: str | None = None,
) -> list[SecurityFinding]:
    """Audit code for security vulnerabilities using tool evidence as proof."""
    task_instructions = (
        "Analyze the provided code and empirical tool findings for real security vulnerabilities.\n"
        "Look for: buffer overflows, uninitialized memory reads, integer overflow/underflow, "
        "dangerous library functions (gets, strcpy, system), injection, or resource leaks.\n"
        "RULE: You must ONLY report findings that are backed by tool findings or a concrete source code line.\n"
        "Assign the most accurate Common Weakness Enumeration (CWE) identifier (e.g. CWE-119, CWE-457, CWE-78)."
    )

    prompt = build_guarded_prompt(
        role="SecurityAudit",
        task_instructions=task_instructions,
        code=code,
        evidence=evidence,
        schema=SECURITY_SCHEMA,
        language=language,
    )

    findings: list[SecurityFinding] = []

    # Retry up to 2 times on invalid JSON schema
    for attempt in range(2):
        try:
            raw_res, ap, am = _llm.generate_task(
                "security_audit",
                prompt,
                schema=SECURITY_SCHEMA,
                provider_name=provider_name,
                ollama_model=ollama_model,
                language=language,
            )
            raw_str = raw_res if isinstance(raw_res, str) else json.dumps(raw_res)
            is_valid, parsed, err = validate_agent_json(raw_str, schema=SECURITY_SCHEMA)
            if is_valid and isinstance(parsed, dict):
                for item in parsed.get("findings", []):
                    findings.append(SecurityFinding(
                        id=str(item.get("id", f"SEC-{len(findings)+1}")),
                        severity=str(item.get("severity", "WARNING")).upper(),
                        line=item.get("line"),
                        cwe=str(item.get("cwe", "CWE-Unknown")),
                        evidence=str(item.get("evidence", "")),
                        minimal_fix=str(item.get("minimal_fix", "")),
                    ))
                break
        except Exception:
            pass

    # If LLM unavailable or returned empty, ground directly in empirical tool findings
    if not findings and evidence.get("tool_findings"):
        for idx, tf in enumerate(evidence["tool_findings"]):
            if tf.get("category") in ("security", "sanitizer", "memory"):
                findings.append(SecurityFinding(
                    id=f"TOOL-SEC-{idx+1}",
                    severity=tf.get("severity", "WARNING"),
                    line=tf.get("line"),
                    cwe="CWE-457" if "uninitialized" in tf.get("message", "").lower() else "CWE-General",
                    evidence=f"{tf.get('tool_name')}: {tf.get('message')}",
                    minimal_fix="Initialize variable or replace unsafe operation",
                ))

    return findings
