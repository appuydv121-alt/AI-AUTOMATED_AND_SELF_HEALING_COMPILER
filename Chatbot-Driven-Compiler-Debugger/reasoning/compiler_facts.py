"""Compiler-warning fact collector.

Runs a *second* non-executing compile with rich warning flags and parses
GCC's structured JSON diagnostic output (falling back to text regex for
older compilers).  Keeps ``note:`` children that the existing parser drops.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from typing import Any

from reasoning.config import WARNINGS_FLAGS, WARNINGS_JSON_FORMAT
from reasoning.models import Fact


# ── Text fallback regex (same as diagnostic_engine._GCC_DIAG_RE) ────────────

_GCC_DIAG_RE = re.compile(
    r"^(?P<file>.+):(?P<line>\d+):(?P<col>\d+):\s*"
    r"(?P<severity>error|warning|note):\s*(?P<msg>.*)$"
)

_OPTION_RE = re.compile(r"\[(-W[^\]]+)\]\s*$")


def _parse_text_diagnostics(stderr: str, source_path: str) -> list[Fact]:
    """Parse traditional GCC text output into Facts, grouping notes with their parent."""
    facts: list[Fact] = []
    counter = 0
    for raw_line in stderr.splitlines():
        m = _GCC_DIAG_RE.match(raw_line.strip())
        if not m:
            continue
        severity = m.group("severity")
        msg = m.group("msg")
        line = int(m.group("line"))
        option_m = _OPTION_RE.search(msg)
        option = option_m.group(1) if option_m else ""

        if severity == "note" and facts:
            # Attach as child of the previous warning/error
            facts[-1].children.append(Fact(
                id="", source="compiler", kind="note",
                line=line, message=msg, option=option,
            ))
        else:
            counter += 1
            facts.append(Fact(
                id=f"F{counter}",
                source="compiler",
                kind=severity,
                line=line,
                message=msg,
                option=option,
            ))
    return facts


def _parse_json_diagnostics(stderr: str, source_path: str) -> list[Fact]:
    """Parse GCC ``-fdiagnostics-format=json`` output into Facts."""
    # GCC emits a JSON array on stderr
    try:
        entries = json.loads(stderr.strip())
    except (json.JSONDecodeError, ValueError):
        return _parse_text_diagnostics(stderr, source_path)

    if not isinstance(entries, list):
        return _parse_text_diagnostics(stderr, source_path)

    facts: list[Fact] = []
    for i, entry in enumerate(entries, 1):
        kind = entry.get("kind", "warning")
        msg = entry.get("message", "")
        option = entry.get("option", "")
        line = _extract_line(entry)

        children: list[Fact] = []
        for child in entry.get("children", []):
            children.append(Fact(
                id="", source="compiler", kind=child.get("kind", "note"),
                line=_extract_line(child),
                message=child.get("message", ""),
                option=child.get("option", ""),
            ))

        facts.append(Fact(
            id=f"F{i}",
            source="compiler",
            kind=kind,
            line=line,
            message=msg,
            option=option,
            children=children,
            extra={"locations": entry.get("locations", [])},
        ))
    return facts


def _extract_line(entry: dict[str, Any]) -> int | None:
    """Pull the primary line number from a GCC JSON diagnostic entry."""
    locations = entry.get("locations", [])
    if locations:
        caret = locations[0].get("caret", {})
        line = caret.get("line")
        if line is not None:
            return int(line)
    return None


# ── Public API ──────────────────────────────────────────────────────────────

def collect_compiler_facts(
    source: str,
    *,
    work_dir: str | None = None,
    timeout: int = 10,
) -> list[Fact]:
    """Run ``g++`` with rich warning flags and return structured Facts.

    This does NOT produce an executable — it compiles to ``/dev/null`` (NUL
    on Windows).  The only purpose is diagnostic extraction.
    """
    if work_dir is None:
        work_dir = os.path.dirname(os.path.abspath(__file__))

    source_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".cpp", prefix="_warn_",
            dir=work_dir, delete=False,
        ) as src:
            src.write(source)
            source_path = src.name

        null_target = "NUL" if os.name == "nt" else "/dev/null"
        cmd = ["g++"] + WARNINGS_FLAGS + ["-c", source_path, "-o", null_target]
        if WARNINGS_JSON_FORMAT:
            cmd.insert(1, "-fdiagnostics-format=json")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        stderr = result.stderr
        if not stderr or not stderr.strip():
            return []

        if WARNINGS_JSON_FORMAT:
            return _parse_json_diagnostics(stderr, source_path)
        return _parse_text_diagnostics(stderr, source_path)

    except subprocess.TimeoutExpired:
        return []
    except FileNotFoundError:
        return []
    except Exception:
        return []
    finally:
        if source_path and os.path.exists(source_path):
            try:
                os.unlink(source_path)
            except OSError:
                pass
