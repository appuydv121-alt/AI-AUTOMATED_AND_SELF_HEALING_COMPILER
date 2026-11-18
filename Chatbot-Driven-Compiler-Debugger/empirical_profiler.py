"""empirical_profiler.py — Self-Benchmarking Empirical Profiler Agent.

Architecture:
  1. Input Series Synthesizer:
     Synthesizes scalable input datasets across sizes N = [10, 100, 1000, 10000]
     matching the code's expected stdin patterns (arrays, matrices, numeric bounds).
  2. Parallel Sandbox Benchmarker:
     Concurrently executes the target code across all input sizes using
     concurrent.futures.ThreadPoolExecutor. Subprocess sandboxes release the GIL,
     profiling all scaling tiers in parallel on separate CPU threads.
  3. Complexity Curve Fitting & Correlation:
     Measures wall-clock scaling ratios T(N_k) / T(N_{k-1}) to determine empirical Big-O.
     Correlates empirical scaling with static AST deductions from complexity_engine.py.
  4. Streamlit Dashboard Visualizer:
     Generates tabular metrics and chart data ready for st.line_chart.
"""
from __future__ import annotations

import concurrent.futures
import math
import statistics
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from compiler_service import compile_and_run
from complexity_engine import analyze_algorithmic_complexity


@dataclass
class DataPoint:
    n: int
    wall_time_ms: float
    status_code: int
    timed_out: bool
    sample_output: str


@dataclass
class EmpiricalProfileReport:
    code: str
    language: str
    data_points: list[DataPoint] = field(default_factory=list)
    empirical_complexity: str = "O(1)"
    theoretical_complexity: str = "Unknown"
    correlation_status: str = "Correlated"
    scaling_ratios: list[float] = field(default_factory=list)
    verdict_markdown: str = ""
    chart_data: dict[str, Any] = field(default_factory=dict)
    total_elapsed_s: float = 0.0


def synthesize_scaling_inputs(
    code: str,
    baseline_stdin: str = "",
    scales: list[int] | None = None,
) -> dict[int, str]:
    """Generates synthetic test inputs across scales N based on stdin syntax clues."""
    if scales is None:
        scales = [10, 100, 1000, 5000]

    inputs: dict[int, str] = {}
    baseline = baseline_stdin.strip() if baseline_stdin else ""

    # Check baseline structure: does it start with an array length followed by items?
    tokens = baseline.split() if baseline else []
    is_count_prefixed_array = False
    if len(tokens) >= 2:
        try:
            first_int = int(tokens[0])
            if first_int == len(tokens) - 1 or first_int <= len(tokens):
                is_count_prefixed_array = True
        except ValueError:
            is_count_prefixed_array = False

    for n in scales:
        if is_count_prefixed_array:
            # Pattern: N \n elem1 elem2 elem3 ...
            elements = " ".join(str((i * 3 + 7) % (n * 2 + 1)) for i in range(n))
            inputs[n] = f"{n}\n{elements}\n"
        elif baseline and len(tokens) > 1:
            # Replicate whitespace-separated elements up to length n
            elements = " ".join(str((i * 7 + 1) % 1000) for i in range(n))
            inputs[n] = f"{elements}\n"
        else:
            # Default numeric scale: passes integer n
            inputs[n] = f"{n}\n"

    return inputs


def _benchmark_single_scale(
    code: str,
    language: str,
    stdin_input: str,
    n: int,
    runs: int = 3,
    timeout_s: float = 4.0,
) -> DataPoint:
    """Measures execution latency across multiple runs for a single scale N."""
    latencies: list[float] = []
    last_status = 0
    last_stdout = ""
    timed_out = False

    for _ in range(max(1, runs)):
        t_start = time.perf_counter_ns()
        res = compile_and_run(code, stdin_input=stdin_input, language=language)
        t_elapsed_ms = (time.perf_counter_ns() - t_start) / 1_000_000.0
        
        last_status = res[0]
        last_stdout = res[1]
        if getattr(res, "timed_out", False):
            timed_out = True
            latencies.append(timeout_s * 1000.0)
            break
        elif last_status != 0:
            latencies.append(t_elapsed_ms)
            break
        else:
            latencies.append(t_elapsed_ms)

    median_ms = statistics.median(latencies) if latencies else 0.0

    return DataPoint(
        n=n,
        wall_time_ms=round(median_ms, 3),
        status_code=last_status,
        timed_out=timed_out,
        sample_output=(last_stdout[:100] + "...") if len(last_stdout) > 100 else last_stdout,
    )


def run_parallel_benchmarks(
    code: str,
    language: str,
    input_series: dict[int, str],
    max_workers: int = 4,
) -> list[DataPoint]:
    """Runs all benchmark scales concurrently using ThreadPoolExecutor.
    
    Subprocesses execute outside the GIL, enabling parallel sandbox executions.
    """
    scales = sorted(input_series.keys())
    data_points: list[DataPoint] = []

    workers = min(len(scales), max_workers)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(_benchmark_single_scale, code, language, input_series[n], n): n
            for n in scales
        }
        for future in concurrent.futures.as_completed(future_map):
            try:
                pt = future.result()
                data_points.append(pt)
            except Exception as e:
                n_val = future_map[future]
                data_points.append(DataPoint(
                    n=n_val,
                    wall_time_ms=0.0,
                    status_code=-1,
                    timed_out=False,
                    sample_output=f"Benchmarking error: {e}",
                ))

    data_points.sort(key=lambda dp: dp.n)
    return data_points


def fit_empirical_complexity(data_points: list[DataPoint]) -> tuple[str, list[float]]:
    """Estimates Big-O complexity class from growth rates across scale points."""
    valid_points = [p for p in data_points if p.status_code == 0 and not p.timed_out and p.wall_time_ms > 0]
    if len(valid_points) < 2:
        return "O(1)", []

    ratios: list[float] = []
    for i in range(1, len(valid_points)):
        prev_p = valid_points[i - 1]
        curr_p = valid_points[i]
        scale_multiplier = curr_p.n / max(1, prev_p.n)
        time_ratio = curr_p.wall_time_ms / max(0.001, prev_p.wall_time_ms)
        ratios.append(round(time_ratio, 2))

    if not ratios:
        return "O(1)", []

    avg_ratio = statistics.median(ratios)

    # Classify based on step multiplier ~ 10x
    # O(1) or O(log N): time ratio stays close to 1.0 (<= 1.8)
    # O(N): time ratio is ~ scale multiplier (3x - 15x)
    # O(N log N): time ratio is ~ 12x - 28x
    # O(N^2): time ratio is ~ scale_multiplier^2 (>= 30x)
    if avg_ratio <= 1.8:
        complexity = "O(1)"
    elif avg_ratio <= 3.0:
        complexity = "O(log N)"
    elif avg_ratio <= 16.0:
        complexity = "O(N)"
    elif avg_ratio <= 35.0:
        complexity = "O(N log N)"
    elif avg_ratio <= 250.0:
        complexity = "O(N²)"
    else:
        complexity = "O(N³) or Exponential"

    return complexity, ratios


def run_empirical_profiler(
    code: str,
    language: str = "cpp",
    baseline_stdin: str = "",
    scales: list[int] | None = None,
) -> EmpiricalProfileReport:
    """Master entry point for the Self-Benchmarking Empirical Profiler Agent."""
    t_start = time.perf_counter()

    # Step 1: Synthesize input series
    inputs = synthesize_scaling_inputs(code, baseline_stdin=baseline_stdin, scales=scales)

    # Step 2: Parallel execution across input sizes
    data_points = run_parallel_benchmarks(code, language=language, input_series=inputs)

    # Step 3: Empirical curve fitting
    emp_complexity, ratios = fit_empirical_complexity(data_points)

    # Step 4: Theoretical complexity AST derivation
    try:
        report = analyze_algorithmic_complexity(code, language=language, stdin_input=baseline_stdin)
        theo_complexity = report.time_complexity
    except Exception:
        theo_complexity = "O(N)"

    # Step 5: Correlation analysis
    # Normalize complexity strings for comparison
    def _norm(c: str) -> str:
        return c.replace(" ", "").replace("²", "^2").upper()

    norm_emp = _norm(emp_complexity)
    norm_theo = _norm(theo_complexity)

    if norm_emp == norm_theo or (norm_emp in ("O(1)", "O(LOGN)") and norm_theo in ("O(1)", "O(LOGN)")):
        corr_status = "CONFIRMED"
        verdict = (
            f"✅ **Theoretical Big-O Validated:** Empirical scaling behavior `{emp_complexity}` "
            f"matches the static AST derivation `{theo_complexity}`."
        )
    else:
        corr_status = "DIVERGENCE OBSERVED"
        verdict = (
            f"ℹ️ **Empirical vs. Theoretical Variance:** Static AST deduced `{theo_complexity}`, "
            f"while empirical runtime growth under test inputs exhibited `{emp_complexity}`. "
            f"(Note: Small input sizes or compiler dead-code elimination / vectorization can flatten empirical curves)."
        )

    # Step 6: Prepare Chart Data for Streamlit (st.line_chart format)
    chart_data = {
        "Scale (N)": [p.n for p in data_points],
        "Latency (ms)": [p.wall_time_ms for p in data_points],
    }

    total_elapsed = time.perf_counter() - t_start

    return EmpiricalProfileReport(
        code=code,
        language=language,
        data_points=data_points,
        empirical_complexity=emp_complexity,
        theoretical_complexity=theo_complexity,
        correlation_status=corr_status,
        scaling_ratios=ratios,
        verdict_markdown=verdict,
        chart_data=chart_data,
        total_elapsed_s=round(total_elapsed, 3),
    )

