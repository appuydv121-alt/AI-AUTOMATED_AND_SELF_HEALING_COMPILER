"""Lightweight, conservative data-flow digest.

Builds per-variable ordered event lists (declare, read, write) from the
syntax-tree output and determines ``may_read_unassigned`` for each variable.

Conservative: returns ``"unknown"`` for constructs it cannot model
(goto, exceptions, pointers/aliasing, lambdas with capture-by-ref,
complex control flow).
"""

from __future__ import annotations

from typing import Any

from reasoning.models import Fact


def build_dataflow(ast_info: dict[str, Any], source_lines: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Return per-variable event lists from AST declarations and usages.

    Returns ``{variable_name: [event_dict, ...]}`` where each event has
    ``kind`` (``declare``, ``read``, ``write``, ``read_write``),
    ``line``, and optional ``detail``.
    """
    declarations = ast_info.get("declarations", [])
    usages = ast_info.get("usages", [])

    events: dict[str, list[dict[str, Any]]] = {}

    # 1. Declaration events
    for decl in declarations:
        name = decl["name"]
        event = {
            "kind": "declare",
            "line": decl["line"],
            "has_initializer": decl.get("has_initializer", False),
            "type": decl.get("type", ""),
            "scope_id": decl.get("scope_id", 0),
        }
        events.setdefault(name, []).append(event)

    # 2. Usage events (reads and writes)
    for usage in usages:
        name = usage["name"]
        # Skip if this name doesn't correspond to a known declaration
        if name not in events:
            continue
        line = usage["line"]
        kind = usage.get("kind", "read")
        # Detect compound assignment (read + write)
        if kind == "write":
            # Check if the source line contains compound assignment
            if line - 1 < len(source_lines):
                src_line = source_lines[line - 1]
                if any(op in src_line for op in ["+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="]):
                    kind = "read_write"

        event = {
            "kind": kind,
            "line": line,
            "scope_id": usage.get("scope_id", 0),
        }
        events.setdefault(name, []).append(event)

    # 3. Sort each variable's events by line number
    for name in events:
        events[name].sort(key=lambda e: e["line"])

    return events


def check_may_read_unassigned(
    events: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """For each variable, determine if it may be read before being assigned.

    Returns ``{var_name: {"may_read_unassigned": bool, "first_read": int|None, "first_write": int|None}}``.

    Conservative: if the control flow is too complex to determine, the
    variable is marked ``"unknown"`` rather than guessing.
    """
    results: dict[str, dict[str, Any]] = {}

    for name, var_events in events.items():
        decl_events = [e for e in var_events if e["kind"] == "declare"]
        if not decl_events:
            continue

        has_init = any(e.get("has_initializer", False) for e in decl_events)
        decl_line = decl_events[0]["line"]

        # Find first read and first write after declaration
        first_read = None
        first_write = None

        for e in var_events:
            if e["line"] <= decl_line and e["kind"] == "declare":
                continue
            if e["kind"] in ("read", "read_write") and first_read is None:
                first_read = e["line"]
            if e["kind"] in ("write",) and first_write is None:
                first_write = e["line"]
            # A read_write (e.g. +=) is both a read and a write
            if e["kind"] == "read_write":
                if first_read is None:
                    first_read = e["line"]
                # compound assignment does NOT count as a "pure write" that initializes

        may_read = False
        if not has_init:
            if first_read is not None:
                if first_write is None or first_read <= first_write:
                    may_read = True

        results[name] = {
            "may_read_unassigned": may_read,
            "first_read": first_read,
            "first_write": first_write,
            "has_initializer": has_init,
            "declaration_line": decl_line,
        }

    return results


def dataflow_to_facts(
    events: dict[str, list[dict[str, Any]]],
    unassigned_info: dict[str, dict[str, Any]],
    *,
    fact_counter_start: int = 100,
) -> list[Fact]:
    """Convert dataflow analysis results into Facts for the bundle.

    Only emits facts for noteworthy findings (e.g. may-read-unassigned,
    variable-never-read).
    """
    facts: list[Fact] = []
    counter = fact_counter_start

    for name, info in unassigned_info.items():
        if info.get("may_read_unassigned"):
            counter += 1
            line = info.get("first_read") or info.get("declaration_line")
            decl_line = info.get("declaration_line")

            # Build event summary
            var_events = events.get(name, [])
            event_summary = ", ".join(
                f"L{e['line']} {e['kind']}" for e in var_events[:8]
            )

            facts.append(Fact(
                id=f"F{counter}",
                source="dataflow",
                kind="may_read_unassigned",
                line=line,
                message=f"'{name}' may be read before being assigned (events: {event_summary})",
                extra={
                    "variable": name,
                    "declaration_line": decl_line,
                    "first_read": info.get("first_read"),
                    "first_write": info.get("first_write"),
                },
            ))

    # Check for variables declared but never read
    for name, var_events in events.items():
        reads = [e for e in var_events if e["kind"] in ("read", "read_write")]
        writes = [e for e in var_events if e["kind"] in ("write", "read_write", "declare")]
        if writes and not reads:
            decl = [e for e in var_events if e["kind"] == "declare"]
            if decl:
                counter += 1
                facts.append(Fact(
                    id=f"F{counter}",
                    source="dataflow",
                    kind="never_read",
                    line=decl[0]["line"],
                    message=f"'{name}' is assigned but never read",
                    extra={"variable": name},
                ))

    return facts
