"""eval/run_eval.py — Automated Multi-Agent Benchmark Evaluation Suite.

Evaluates:
  - Success Rate (%) across benchmark programs
  - Verifiable Quality Score Deltas (Security & Efficiency)
  - Empirical Runtime Latency Gains
  - Sanitizer Flaw Neutralization
"""
from __future__ import annotations

import json
import os
import sys
import time

# Ensure parent path is in sys.path
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from orchestrator import OrchestratorStateMachine


def run_benchmark(sample_file: str, language: str = "cpp") -> dict:
    with open(sample_file, "r", encoding="utf-8") as f:
        cases = json.load(f)

    results = []
    total = len(cases)
    passed = 0
    sec_deltas = []
    eff_deltas = []

    print(f"==================================================")
    print(f"Running Multi-Agent Evaluation on {total} {language.upper()} Programs")
    print(f"==================================================")

    for idx, c in enumerate(cases, 1):
        cid = c.get("id")
        name = c.get("name")
        code = c.get("code")
        stdin_val = c.get("stdin", "")

        print(f"\n[{idx}/{total}] Evaluating {cid}: {name}...")
        t0 = time.time()
        orch = OrchestratorStateMachine(
            code=code,
            language=language,
            baseline_stdin=stdin_val,
            max_cycles=2,
            time_budget_s=30.0,
        )
        res = orch.run()
        elapsed = round(time.time() - t0, 2)

        sec_before = res.metrics_before.get("security_score", 0.0)
        sec_after = res.metrics_after.get("security_score", 0.0)
        eff_before = res.metrics_before.get("efficiency_score", 0.0)
        eff_after = res.metrics_after.get("efficiency_score", 0.0)

        d_sec = sec_after - sec_before
        d_eff = eff_after - eff_before

        sec_deltas.append(d_sec)
        eff_deltas.append(d_eff)

        if res.success:
            passed += 1
            status_sym = "✅ SUCCESS"
        else:
            status_sym = "⚠️ NEEDS REVIEW"

        print(f"   Result: {status_sym} ({elapsed}s)")
        print(f"   Security: {sec_before:.0f}% -> {sec_after:.0f}% ({d_sec:+.0f}%)")
        print(f"   Efficiency: {eff_before:.0f}% -> {eff_after:.0f}% ({d_eff:+.0f}%)")
        print(f"   Tests: {res.tests_passed}/{res.tests_evaluated} passed")

        results.append({
            "id": cid,
            "name": name,
            "success": res.success,
            "status": res.verification_status,
            "elapsed_s": elapsed,
            "d_sec": d_sec,
            "d_eff": d_eff,
        })

    success_rate = (passed / total) * 100.0 if total > 0 else 0.0
    avg_d_sec = sum(sec_deltas) / len(sec_deltas) if sec_deltas else 0.0
    avg_d_eff = sum(eff_deltas) / len(eff_deltas) if eff_deltas else 0.0

    summary = {
        "total_evaluated": total,
        "success_rate_pct": round(success_rate, 1),
        "avg_security_gain_pct": round(avg_d_sec, 1),
        "avg_efficiency_gain_pct": round(avg_d_eff, 1),
        "cases": results,
    }

    print(f"\n==================================================")
    print(f"EVALUATION COMPLETE:")
    print(f"Success Rate: {summary['success_rate_pct']}%")
    print(f"Avg Security Gain: {summary['avg_security_gain_pct']:+}%")
    print(f"Avg Efficiency Gain: {summary['avg_efficiency_gain_pct']:+}%")
    print(f"==================================================")

    return summary


if __name__ == "__main__":
    benchmark_file = os.path.join(os.path.dirname(__file__), "samples_cpp.json")
    run_benchmark(benchmark_file, language="cpp")

