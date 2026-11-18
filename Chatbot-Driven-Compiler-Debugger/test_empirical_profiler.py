"""Unit tests for empirical_profiler.py."""
import pytest
from empirical_profiler import (
    DataPoint,
    synthesize_scaling_inputs,
    run_parallel_benchmarks,
    fit_empirical_complexity,
    run_empirical_profiler,
)


def test_synthesize_scaling_inputs():
    code = "n = int(input())\nfor i in range(n): pass"
    inputs = synthesize_scaling_inputs(code, baseline_stdin="5\n", scales=[10, 100])
    assert 10 in inputs
    assert 100 in inputs
    assert inputs[10].strip() == "10"


def test_synthesize_scaling_inputs_array_pattern():
    code = "cin >> n;\nfor(int i=0;i<n;i++) cin >> a[i];"
    inputs = synthesize_scaling_inputs(code, baseline_stdin="3\n1 2 3", scales=[10, 50])
    assert 10 in inputs
    lines_10 = inputs[10].strip().split("\n")
    assert lines_10[0] == "10"
    assert len(lines_10[1].split()) == 10


def test_fit_empirical_complexity_constant():
    pts = [
        DataPoint(n=10, wall_time_ms=5.0, status_code=0, timed_out=False, sample_output=""),
        DataPoint(n=100, wall_time_ms=5.1, status_code=0, timed_out=False, sample_output=""),
        DataPoint(n=1000, wall_time_ms=5.2, status_code=0, timed_out=False, sample_output=""),
    ]
    comp, ratios = fit_empirical_complexity(pts)
    assert comp in ("O(1)", "O(log N)")


def test_fit_empirical_complexity_linear():
    pts = [
        DataPoint(n=10, wall_time_ms=2.0, status_code=0, timed_out=False, sample_output=""),
        DataPoint(n=100, wall_time_ms=18.0, status_code=0, timed_out=False, sample_output=""),
        DataPoint(n=1000, wall_time_ms=175.0, status_code=0, timed_out=False, sample_output=""),
    ]
    comp, ratios = fit_empirical_complexity(pts)
    assert comp in ("O(N)", "O(N log N)")


def test_fit_empirical_complexity_quadratic():
    pts = [
        DataPoint(n=10, wall_time_ms=2.0, status_code=0, timed_out=False, sample_output=""),
        DataPoint(n=100, wall_time_ms=190.0, status_code=0, timed_out=False, sample_output=""),
        DataPoint(n=1000, wall_time_ms=18500.0, status_code=0, timed_out=False, sample_output=""),
    ]
    comp, ratios = fit_empirical_complexity(pts)
    assert comp == "O(N²)"


def test_run_empirical_profiler_python():
    code = "import sys\nprint('Done')\n"
    report = run_empirical_profiler(code, language="python", scales=[10, 50])
    assert len(report.data_points) == 2
    assert report.total_elapsed_s > 0
    assert "Scale (N)" in report.chart_data
    assert "Latency (ms)" in report.chart_data
    assert report.correlation_status in ("CONFIRMED", "DIVERGENCE OBSERVED")

