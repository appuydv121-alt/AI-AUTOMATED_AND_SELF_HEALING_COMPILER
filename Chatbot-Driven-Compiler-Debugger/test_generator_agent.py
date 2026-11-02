"""test_generator_agent.py — Edge-Case & Equivalence Test Matrix Synthesizer.

Responsibilities:
  - Generates representative sample inputs from the code's apparent input format.
  - Generates 6-10 diverse edge-case inputs (empty input, zero, negative numbers, boundary values, whitespace).
  - Supplies the SandboxJudge with samples for behavioral comparison; generated cases are not a proof of equivalence.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import llm_provider as _llm
from agent_guard import build_guarded_prompt, validate_agent_json

TEST_GEN_SCHEMA = {
    "type": "object",
    "required": ["test_inputs"],
    "properties": {
        "test_inputs": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "stdin", "rationale"],
                "properties": {
                    "name": {"type": "string"},
                    "stdin": {"type": "string"},
                    "rationale": {"type": "string"},
                },
            },
        },
    },
}


@dataclass
class TestCase:
    name: str
    stdin: str
    rationale: str
    expected_edge: str = ""

    def __post_init__(self):
        if not self.expected_edge and self.rationale:
            self.expected_edge = self.rationale
        elif not self.rationale and self.expected_edge:
            self.rationale = self.expected_edge


def generate_test_matrix(
    code: str,
    baseline_stdin: str = "",
    language: str = "cpp",
    provider_name: str | None = None,
    ollama_model: str | None = None,
    count: int = 6,
) -> list[TestCase]:
    """Synthesize a battery of edge-case test inputs tailored to the code."""
    cases: list[TestCase] = []

    # Include user's baseline input first if provided
    if baseline_stdin and baseline_stdin.strip():
        cases.append(TestCase(
            name="Baseline User Input",
            stdin=baseline_stdin,
            rationale="User-supplied test input from the stdin console.",
        ))

    task_instructions = (
        f"Generate {count} distinct, edge-case stdin inputs to test the provided {language.upper()} code.\n"
        "Target edge cases: empty input, single values (0, 1, -1), large numbers (e.g. 10000), "
        "multi-word strings with spaces, multiple lines, and boundary values.\n"
        "Return strictly typed JSON matching TEST_GEN_SCHEMA."
    )

    prompt = build_guarded_prompt(
        role="TestGenerator",
        task_instructions=task_instructions,
        code=code,
        evidence={"baseline_stdin": baseline_stdin},
        schema=TEST_GEN_SCHEMA,
        language=language,
    )

    for attempt in range(2):
        try:
            raw_res, ap, am = _llm.generate_task(
                "test_generation",
                prompt,
                schema=TEST_GEN_SCHEMA,
                provider_name=provider_name,
                ollama_model=ollama_model,
                language=language,
            )
            raw_str = raw_res if isinstance(raw_res, str) else json.dumps(raw_res)
            is_valid, parsed, err = validate_agent_json(raw_str, schema=TEST_GEN_SCHEMA)
            if is_valid and isinstance(parsed, dict):
                for item in parsed.get("test_inputs", []):
                    cases.append(TestCase(
                        name=str(item.get("name", f"EdgeCase-{len(cases)+1}")),
                        stdin=str(item.get("stdin", "")),
                        rationale=str(item.get("rationale", "")),
                    ))
                break
        except Exception:
            pass

    # Robust deterministic fallback if LLM is offline or generated < 3 cases
    if len(cases) < 3:
        import re
        is_array_reader = bool(re.search(r"cin\s*>>\s*[a-zA-Z_]\w*\[", code) or re.search(r"for[^{}]*cin\s*>>", code))
        if is_array_reader:
            fallbacks = [
                TestCase("Array Reverse Order", "5\n5 4 3 2 1\n", "Reverse sorted array"),
                TestCase("Array With Duplicates", "5\n2 8 2 1 8\n", "Array with duplicate values"),
                TestCase("Array Already Sorted", "5\n1 2 3 4 5\n", "Pre-sorted array"),
                TestCase("Single Element Array", "1\n99\n", "Single item array"),
                TestCase("Zero Size Array", "0\n", "Zero-length array boundary"),
            ]
        else:
            fallbacks = [
                TestCase("Zero Boundary", "0", "Tests behavior at zero boundary"),
                TestCase("Single Positive", "1", "Tests unit positive input"),
                TestCase("Small Value", "5", "Standard small input"),
                TestCase("Negative Boundary", "-1", "Tests negative input handling"),
                TestCase("Multi-word String", "Arpit Yadav 2026", "Tests whitespace handling"),
                TestCase("Empty / EOL", "", "Tests EOF behavior"),
            ]
        for fb in fallbacks:
            if not any(c.stdin.strip() == fb.stdin.strip() for c in cases):
                cases.append(fb)

    return cases[: max(count, len(cases))]
