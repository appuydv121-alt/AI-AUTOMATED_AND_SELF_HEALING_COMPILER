"""Stage 8: Optimization reasoning.

Separate pass that only runs in "thorough" profile and never when
compile errors exist.  Forces a structured five-part analysis.
"""

from __future__ import annotations

import json

from reasoning.config import STAGE_TIMEOUT
from reasoning.llm_client import LLMClient, get_client
from reasoning.models import Finding, IntentModel, OptimizationAnalysis, StageResult, Tier
from reasoning.prompts import optimize_prompt
from reasoning.schemas import OPTIMIZE_SCHEMA


def run_optimization_analysis(
    source_lines: list[str],
    functions: list[dict],
    intent_model: IntentModel | None,
    *,
    client: LLMClient | None = None,
) -> list[Finding]:
    """Analyze functions for optimization opportunities.

    Returns findings only for functions where the LLM recommends
    "change" or "optional".
    """
    if client is None:
        client = get_client()

    findings: list[Finding] = []
    intent_json = json.dumps({
        "purpose": intent_model.purpose if intent_model else "",
        "variable_roles": [
            {"name": vr.name, "role": vr.role}
            for vr in (intent_model.variable_roles if intent_model else [])
        ],
    })

    for fn in functions:
        start = fn.get("start_line", 1) - 1
        end = fn.get("end_line", len(source_lines))
        code_slice = "\n".join(
            f"{i+1}: {line}" for i, line in enumerate(source_lines[start:end], start)
        )

        prompt = optimize_prompt(code_slice, intent_json)

        parsed, error = client.generate_json(
            prompt,
            OPTIMIZE_SCHEMA,
            timeout=STAGE_TIMEOUT.get("optimize", 30),
        )

        if error or parsed is None:
            continue

        recommendation = parsed.get("recommendation", "leave_as_is").lower().strip()
        if recommendation == "leave_as_is":
            continue

        analysis = OptimizationAnalysis(
            specification=parsed.get("specification", ""),
            current_complexity=parsed.get("current_complexity", ""),
            is_worth_changing=parsed.get("is_worth_changing", ""),
            alternative=parsed.get("alternative", ""),
            alternative_complexity=parsed.get("alternative_complexity", ""),
            tradeoffs=parsed.get("tradeoffs", []),
            recommendation=recommendation,
        )

        findings.append(Finding(
            tier=Tier.OPTIMIZATION,
            kind="optimization",
            lines=[fn.get("start_line", 1)],
            title=f"Optimization opportunity in {fn.get('name', 'function')}",
            explanation=analysis.alternative,
            reasoning=analysis.current_complexity,
            source="reasoning",
            optimization={
                "specification": analysis.specification,
                "current_complexity": analysis.current_complexity,
                "alternative": analysis.alternative,
                "alternative_complexity": analysis.alternative_complexity,
                "tradeoffs": analysis.tradeoffs,
                "recommendation": analysis.recommendation,
            },
        ))

    return findings
