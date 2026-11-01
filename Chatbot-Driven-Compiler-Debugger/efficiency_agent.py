"""efficiency_agent.py — Performance & Algorithmic Profiler Agent.

Responsibilities:
  - Consumes runtime profile metrics (median execution latency, memory, runs) and AST deductions.
  - Identifies algorithmic complexity hotspots (e.g. O(N^2) loops, redundant allocations, unbuffered I/O).
  - Proposes minimal algorithmic or data-structural changes to reduce Big-O and execution time.
  - Emits strictly typed JSON matching EFFICIENCY_SCHEMA.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import llm_provider as _llm
from agent_guard import build_guarded_prompt, validate_agent_json

EFFICIENCY_SCHEMA = {
    "type": "object",
    "required": ["hotspots", "est_big_o"],
    "properties": {
        "hotspots": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["line", "complexity", "reason", "suggested_change"],
                "properties": {
                    "line": {"type": ["integer", "null"]},
                    "complexity": {"type": "string"},
                    "reason": {"type": "string"},
                    "suggested_change": {"type": "string"},
                },
            },
        },
        "est_big_o": {"type": "string"},
    },
}


@dataclass
class HotspotFinding:
    line: int | None
    complexity: str
    reason: str
    suggested_change: str


@dataclass
class EfficiencyProfileResult:
    hotspots: list[HotspotFinding]
    est_big_o: str


def run_efficiency_profile(
    code: str,
    evidence: dict[str, Any],
    language: str = "cpp",
    provider_name: str | None = None,
    gemini_model: str | None = None,
    ollama_model: str | None = None,
) -> EfficiencyProfileResult:
    """Analyze empirical execution profile and code structure for algorithmic efficiency gains."""
    profile_metrics = evidence.get("profile_metrics", {})
    task_instructions = (
        "Analyze the provided source code and empirical execution profile for performance bottlenecks.\n"
        f"Profile Metrics: Median Runtime = {profile_metrics.get('median_time_ms', 0)} ms across {profile_metrics.get('runs_count', 0)} runs.\n"
        "Look for: O(N^2) or higher nested loops, unbuffered I/O (cin/cout without sync_with_stdio), "
        "excessive buffer flushes (std::endl in loop), redundant string/vector copies, or unindexed queries.\n"
        "Estimate overall asymptotic time complexity (est_big_o) and propose targeted minimal improvements."
    )

    prompt = build_guarded_prompt(
        role="EfficiencyProfiler",
        task_instructions=task_instructions,
        code=code,
        evidence=evidence,
        schema=EFFICIENCY_SCHEMA,
        language=language,
    )

    from complexity_engine import analyze_algorithmic_complexity
    comp_report = analyze_algorithmic_complexity(code, language=language)

    hotspots: list[HotspotFinding] = []
    est_big_o = comp_report.time_complexity

    # Seed baseline findings from algorithmic analysis
    for h in comp_report.hotspots:
        hotspots.append(HotspotFinding(
            line=h.get("line"),
            complexity=str(h.get("complexity", est_big_o)),
            reason=str(h.get("reason", "")),
            suggested_change=str(h.get("suggested_change", "")),
        ))

    for attempt in range(2):
        try:
            raw_res, ap, am = _llm.generate_task(
                "efficiency_profile",
                prompt,
                schema=EFFICIENCY_SCHEMA,
                provider_name=provider_name,
                gemini_model=gemini_model,
                ollama_model=ollama_model,
                language=language,
            )
            raw_str = raw_res if isinstance(raw_res, str) else json.dumps(raw_res)
            is_valid, parsed, err = validate_agent_json(raw_str, schema=EFFICIENCY_SCHEMA)
            if is_valid and isinstance(parsed, dict):
                est_big_o = str(parsed.get("est_big_o", "O(N)"))
                for item in parsed.get("hotspots", []):
                    hotspots.append(HotspotFinding(
                        line=item.get("line"),
                        complexity=str(item.get("complexity", "O(N)")),
                        reason=str(item.get("reason", "")),
                        suggested_change=str(item.get("suggested_change", "")),
                    ))
                break
        except Exception:
            pass

    # Fallback to local heuristic if LLM unavailable
    if not hotspots and language.lower() in ("cpp", "c++"):
        if "ios_base::sync_with_stdio" not in code and ("cin" in code or "cout" in code):
            hotspots.append(HotspotFinding(
                line=None,
                complexity="I/O Overhead",
                reason="Unbuffered standard streams introduce heavy C/C++ sync overhead.",
                suggested_change="Add ios_base::sync_with_stdio(false); cin.tie(NULL);",
            ))

    return EfficiencyProfileResult(hotspots=hotspots, est_big_o=est_big_o)

