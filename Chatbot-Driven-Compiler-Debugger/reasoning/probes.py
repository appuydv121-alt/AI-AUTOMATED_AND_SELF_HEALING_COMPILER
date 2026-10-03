"""General claim probes.

Probes are a small library of general property checkers over the AST /
dataflow.  They are invoked **only when the LLM makes a claim of that kind**
to confirm or contradict it.  They never generate findings by themselves.

A probe returns ``"confirmed"``, ``"contradicted"``, or ``"not_checkable"``.
"""

from __future__ import annotations

from typing import Any

from reasoning.models import FactBundle


def probe_read_before_write(bundle: FactBundle, variable: str, claimed_line: int) -> str:
    """Check whether ``variable`` is read before being written on any path to ``claimed_line``."""
    events = bundle.dataflow_events.get(variable, [])
    if not events:
        return "not_checkable"

    # Find declaration
    decls = [e for e in events if e["kind"] == "declare"]
    if not decls:
        return "not_checkable"

    has_init = any(e.get("has_initializer", False) for e in decls)
    if has_init:
        return "contradicted"

    decl_line = decls[0]["line"]
    # Look for any write before the claimed read
    for e in events:
        if e["line"] <= decl_line:
            continue
        if e["line"] > claimed_line:
            break
        if e["kind"] == "write":
            return "contradicted"  # written before the claimed read line
        if e["kind"] in ("read", "read_write") and e["line"] == claimed_line:
            return "confirmed"

    # If no write found before claimed_line and there is a read
    reads_at = [e for e in events if e["kind"] in ("read", "read_write") and e["line"] == claimed_line]
    if reads_at:
        return "confirmed"

    return "not_checkable"


def probe_variable_exists(bundle: FactBundle, variable: str) -> str:
    """Check whether ``variable`` exists in the AST declarations."""
    for decl in bundle.declarations:
        if decl.get("name") == variable:
            return "confirmed"
    return "contradicted"


def probe_variable_has_initializer(bundle: FactBundle, variable: str) -> str:
    """Check whether ``variable`` has an initializer at declaration."""
    for decl in bundle.declarations:
        if decl.get("name") == variable:
            if decl.get("has_initializer", False):
                return "confirmed"
            return "contradicted"
    return "not_checkable"


def probe_line_exists(bundle: FactBundle, line: int) -> str:
    """Check whether line number is within the source range."""
    if 1 <= line <= len(bundle.source_lines):
        return "confirmed"
    return "contradicted"


def probe_snippet_present(bundle: FactBundle, snippet: str, line: int) -> str:
    """Check whether ``snippet`` is literally present at the claimed line (whitespace-normalized)."""
    if line < 1 or line > len(bundle.source_lines):
        return "contradicted"

    source_line = bundle.source_lines[line - 1]
    # Normalize whitespace for comparison
    norm_source = " ".join(source_line.split())
    norm_snippet = " ".join(snippet.split())

    if norm_snippet in norm_source:
        return "confirmed"

    # Try surrounding lines (off-by-one tolerance)
    for offset in [-1, 1]:
        check_line = line + offset
        if 1 <= check_line <= len(bundle.source_lines):
            alt_source = " ".join(bundle.source_lines[check_line - 1].split())
            if norm_snippet in alt_source:
                return "confirmed"

    return "contradicted"


def probe_fact_exists(bundle: FactBundle, fact_id: str) -> str:
    """Check whether a cited fact ID exists in the bundle."""
    for fact in bundle.facts:
        if fact.id == fact_id:
            return "confirmed"
    return "contradicted"


def run_probe(bundle: FactBundle, probe_type: str, **kwargs) -> str:
    """Dispatch to the appropriate probe by type string.

    Returns ``"confirmed"``, ``"contradicted"``, or ``"not_checkable"``.
    """
    dispatchers = {
        "read_before_write": lambda: probe_read_before_write(
            bundle, kwargs.get("variable", ""), kwargs.get("line", 0)
        ),
        "variable_exists": lambda: probe_variable_exists(
            bundle, kwargs.get("variable", "")
        ),
        "variable_has_initializer": lambda: probe_variable_has_initializer(
            bundle, kwargs.get("variable", "")
        ),
        "line_exists": lambda: probe_line_exists(
            bundle, kwargs.get("line", 0)
        ),
        "snippet_present": lambda: probe_snippet_present(
            bundle, kwargs.get("snippet", ""), kwargs.get("line", 0)
        ),
        "fact_exists": lambda: probe_fact_exists(
            bundle, kwargs.get("fact_id", "")
        ),
    }

    fn = dispatchers.get(probe_type)
    if fn is None:
        return "not_checkable"
    return fn()
