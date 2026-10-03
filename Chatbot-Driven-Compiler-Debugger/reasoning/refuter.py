"""Stage 4: Adversarial refutation.

For each surviving candidate, asks the LLM to try to disprove the claim.
Uses a separate, adversarial prompt that forces the model to argue AGAINST
its own earlier finding.
"""

from __future__ import annotations

import json

from reasoning.config import STAGE_TIMEOUT
from reasoning.llm_client import LLMClient, get_client
from reasoning.models import Candidate, StageResult
from reasoning.prompts import refute_prompt
from reasoning.schemas import REFUTE_SCHEMA


def refute_candidate(
    candidate: Candidate,
    code_slice: str,
    *,
    client: LLMClient | None = None,
) -> Candidate:
    """Run the adversarial refutation pass on a single candidate.

    Sets ``candidate.refute_verdict`` to ``"holds"``, ``"refuted"``,
    or ``"uncertain"``, and ``candidate.refute_reason``.
    """
    if client is None:
        client = get_client()

    candidate_dict = {
        "claim": candidate.claim,
        "kind": candidate.kind,
        "lines": candidate.lines,
        "snippet": candidate.snippet,
        "proposed_tier": candidate.proposed_tier,
    }

    prompt = refute_prompt(code_slice, json.dumps(candidate_dict))

    parsed, error = client.generate_json(
        prompt,
        REFUTE_SCHEMA,
        timeout=STAGE_TIMEOUT.get("refute", 20),
    )

    if error or parsed is None:
        candidate.refute_verdict = "uncertain"
        candidate.refute_reason = error or "Refutation stage failed"
        return candidate

    verdict = parsed.get("verdict", "uncertain").lower().strip()
    if verdict not in ("holds", "refuted", "uncertain"):
        verdict = "uncertain"

    candidate.refute_verdict = verdict
    candidate.refute_reason = parsed.get("reason", "")
    return candidate


def run_refutation(
    candidates: list[Candidate],
    source_lines: list[str],
    *,
    client: LLMClient | None = None,
) -> list[Candidate]:
    """Run refutation on all gate-passing candidates."""
    if client is None:
        client = get_client()

    full_code = "\n".join(source_lines)

    for candidate in candidates:
        if candidate.gate_status != "pass":
            continue

        # Extract relevant code slice (a few lines around the claim)
        if candidate.lines:
            start = max(0, min(candidate.lines) - 5)
            end = min(len(source_lines), max(candidate.lines) + 5)
            code_slice = "\n".join(
                f"{i+1}: {line}" for i, line in enumerate(source_lines[start:end], start)
            )
        else:
            code_slice = full_code

        refute_candidate(candidate, code_slice, client=client)

    return candidates
