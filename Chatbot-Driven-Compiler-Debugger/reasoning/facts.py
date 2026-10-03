"""Fact collector — Stage 0 of the pipeline.

Orchestrates all deterministic evidence sources:
  0b  compiler warnings (with notes, JSON)
  0c  syntax tree (functions, scopes, declarations, usages)
  0d  dataflow digest (per-variable events, may-read-unassigned)

Assembles them into a ``FactBundle`` with sequentially numbered facts.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from reasoning.compiler_facts import collect_compiler_facts
from reasoning.dataflow import build_dataflow, check_may_read_unassigned, dataflow_to_facts
from reasoning.models import Fact, FactBundle
from reasoning.syntax_tree import parse_source


def collect(
    source: str,
    *,
    compile_result: Optional[dict[str, Any]] = None,
    work_dir: str | None = None,
) -> FactBundle:
    """Build a complete ``FactBundle`` from source code.

    Parameters
    ----------
    source : str
        The C++ source code.
    compile_result : dict, optional
        Result from ``compile_and_run``, with keys ``return_code``,
        ``stdout``, ``stderr``.
    work_dir : str, optional
        Working directory for temp files.
    """
    if work_dir is None:
        work_dir = os.path.dirname(os.path.abspath(__file__))

    source_lines = source.splitlines()
    facts: list[Fact] = []

    # ── 0b: Compiler warnings with notes ──────────────────────────────
    compiler_facts = collect_compiler_facts(source, work_dir=work_dir)
    facts.extend(compiler_facts)

    # ── 0c: Syntax tree ───────────────────────────────────────────────
    ast_info = parse_source(source)
    frontend_used = ast_info.get("frontend_used", "none")

    # Convert AST declarations to facts
    fact_counter = len(facts)
    for decl in ast_info.get("declarations", []):
        fact_counter += 1
        init_status = "with initializer" if decl.get("has_initializer") else "no initializer"
        facts.append(Fact(
            id=f"F{fact_counter}",
            source="ast",
            kind="decl",
            line=decl.get("line"),
            message=f"decl {decl.get('name')} {decl.get('type', '')} ({init_status})",
            extra=decl,
        ))

    # ── 0d: Dataflow digest ───────────────────────────────────────────
    df_events = build_dataflow(ast_info, source_lines)
    unassigned = check_may_read_unassigned(df_events)
    df_facts = dataflow_to_facts(df_events, unassigned, fact_counter_start=fact_counter)
    facts.extend(df_facts)
    fact_counter += len(df_facts)

    # ── 0e: Semantic hints (H5 demoted to weak hint facts) ────────────
    try:
        from semantic_analyzer import analyze_semantics
        sem_report = analyze_semantics(source, min_confidence=0.50)
        for item in getattr(sem_report, "diagnostics", []):
            fact_counter += 1
            facts.append(Fact(
                id=f"F{fact_counter}",
                source="semantic_hint",
                kind="hint",
                line=getattr(item, "line_number", None),
                message=f"Name hint: {getattr(item, 'message', '')}",
                extra={"confidence": getattr(item, "confidence", 0.0)},
            ))
    except Exception:
        pass

    return FactBundle(
        facts=facts,
        source_lines=source_lines,
        functions=ast_info.get("functions", []),
        scopes=ast_info.get("scopes", []),
        declarations=ast_info.get("declarations", []),
        dataflow_events=df_events,
        frontend_used=frontend_used,
    )
