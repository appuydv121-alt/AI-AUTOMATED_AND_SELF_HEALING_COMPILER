#!/usr/bin/env python3
"""benchmark_llm.py — Time Gemini and Ollama on 3 sample C++ programs.

Usage:
    python benchmark_llm.py

Set GEMINI_API_KEY in .env or as an environment variable before running.
Results show latency (seconds) and whether the JSON parsed with all required fields.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Ensure project root is on path when run from outside the project dir.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Load .env so GEMINI_API_KEY is visible.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
except ImportError:
    pass

import llm_provider as _llm

# ── Sample programs ──────────────────────────────────────────────────────────
PROGRAMS: list[tuple[str, str]] = [
    (
        "Hello World",
        """\
#include <iostream>
using namespace std;
int main() {
    cout << "Hello, World!" << endl;
    return 0;
}""",
    ),
    (
        "twoSum (O(n²) naive)",
        """\
#include <iostream>
#include <vector>
using namespace std;
vector<int> twoSum(vector<int>& nums, int target) {
    for (int i = 0; i < nums.size(); i++)
        for (int j = i + 1; j < nums.size(); j++)
            if (nums[i] + nums[j] == target)
                return {i, j};
    return {};
}
int main() {
    vector<int> nums = {2, 7, 11, 15};
    auto r = twoSum(nums, 9);
    cout << r[0] << " " << r[1] << endl;
}""",
    ),
    (
        "Bubble Sort",
        """\
#include <iostream>
#include <vector>
using namespace std;
void bubbleSort(vector<int>& arr) {
    int n = arr.size();
    for (int i = 0; i < n - 1; i++)
        for (int j = 0; j < n - i - 1; j++)
            if (arr[j] > arr[j + 1]) swap(arr[j], arr[j + 1]);
}
int main() {
    vector<int> v = {64, 34, 25, 12, 22, 11, 90};
    bubbleSort(v);
    for (int x : v) cout << x << " ";
    cout << endl;
}""",
    ),
]

_REQUIRED_ANALYSIS_FIELDS = {
    "specification", "current_complexity", "alternative",
    "alternative_complexity", "tradeoffs", "recommendation",
}
_REQUIRED_SUGGESTION_FIELDS = {
    "line_start", "line_end", "severity", "issue", "reason", "original", "optimized",
}


def _fields_ok(result: object) -> tuple[bool, str]:
    """Return (ok, missing_fields_description)."""
    if not isinstance(result, dict):
        return False, "result is not a dict"
    analysis = result.get("analysis") or {}
    missing_a = _REQUIRED_ANALYSIS_FIELDS - set(analysis.keys())
    empty_a = {k for k in _REQUIRED_ANALYSIS_FIELDS if not str(analysis.get(k, "")).strip()}
    suggestions = result.get("suggestions", [])
    missing_s: set[str] = set()
    for s in suggestions:
        missing_s |= _REQUIRED_SUGGESTION_FIELDS - set(s.keys())
    issues = []
    if missing_a:
        issues.append(f"analysis missing: {sorted(missing_a)}")
    if empty_a:
        issues.append(f"analysis empty: {sorted(empty_a)}")
    if missing_s:
        issues.append(f"suggestions missing: {sorted(missing_s)}")
    return (not issues), ("; ".join(issues) or "OK")


def _benchmark_task(label: str, code: str, provider_name: str, model: str | None) -> dict:
    prompt = (
        "Review this C++ source. Return JSON with 'analysis' (specification, current_complexity,"
        " alternative, alternative_complexity, tradeoffs, recommendation) and 'suggestions' array"
        " (line_start, line_end, severity, issue, reason, original, optimized).\n\nCode:\n" + code
    )
    t0 = time.perf_counter()
    err = ""
    result = None
    try:
        result, active_prov, active_mdl = _llm.generate_task(
            "live_suggestions",
            prompt,
            provider_name=provider_name,
            model=model,
        )
    except Exception as exc:
        err = str(exc)
        active_prov = provider_name
        active_mdl = model or ""
    elapsed = time.perf_counter() - t0
    ok, field_note = _fields_ok(result) if result is not None else (False, "no result")
    return {
        "program": label,
        "requested_provider": provider_name,
        "active_provider": active_prov if not err else f"ERROR({provider_name})",
        "active_model": active_mdl,
        "latency_s": round(elapsed, 2),
        "json_ok": ok,
        "field_note": field_note,
        "error": err,
    }


def main() -> None:
    print("\n=== LLM Benchmark ===\n")
    print(f"GEMINI_API_KEY set: {bool(os.environ.get('GEMINI_API_KEY', '').strip())}")
    print(f"Gemini model:  {_llm._GEMINI_MODEL}")
    print(f"Live model:    {_llm._LIVE_MODEL}")
    print(f"Codegen model: {_llm._CODEGEN_MODEL}\n")

    configs: list[tuple[str, str | None]] = [
        ("gemini",  None),
        ("ollama",  _llm._LIVE_MODEL),
        ("ollama",  _llm._CODEGEN_MODEL),
    ]

    rows: list[dict] = []
    for label, code in PROGRAMS:
        print(f"  Program: {label}")
        for prov, mdl in configs:
            row = _benchmark_task(label, code, prov, mdl)
            rows.append(row)
            status = "✓" if row["json_ok"] else "✗"
            err_note = f"  ERROR: {row['error']}" if row["error"] else ""
            print(
                f"    [{status}] {row['active_provider']:8s} / {row['active_model']:25s} "
                f"{row['latency_s']:6.2f}s  fields: {row['field_note']}{err_note}"
            )
        print()

    # Summary table
    print("\n=== Summary Table ===")
    header = f"{'Program':<28} {'Provider':<10} {'Model':<25} {'Latency':>8} {'JSON OK':>8}"
    print(header)
    print("-" * len(header))
    for r in rows:
        prov_mdl = f"{r['active_provider']}/{r['active_model']}"
        status = "Yes" if r["json_ok"] else f"No ({r['field_note'][:30]})"
        print(f"{r['program']:<28} {r['active_provider']:<10} {r['active_model']:<25} {r['latency_s']:>7.2f}s {status:>8}")

    print("\nBenchmark complete.")


if __name__ == "__main__":
    main()
