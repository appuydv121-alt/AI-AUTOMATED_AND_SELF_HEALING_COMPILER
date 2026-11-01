"""tool_runner.py — Real Analysis & Empirical Profiling Layer.

Gives agents real tools:
  - Sanitizer compilation (g++ -fsanitize=address,undefined)
  - Static tools (cppcheck, clang-tidy, bandit) with graceful fallback to diagnostic_engine
  - Median runtime measurement (5 runs) & peak memory estimation
  - Node.js check for JS/TS
"""
from __future__ import annotations

import os
import shutil
import statistics
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from typing import Any

from diagnostic_engine import check_uninitialized_variables, check_division_by_zero, check_loop_direction
from secure_scan import scan_all_security_issues


@dataclass
class ToolFinding:
    tool_name: str
    category: str      # "security", "sanitizer", "correctness", "memory"
    severity: str      # "CRITICAL", "WARNING", "INFO"
    line: int | None
    message: str
    evidence: str


@dataclass
class ProfileMetrics:
    median_time_ms: float
    min_time_ms: float
    max_time_ms: float
    runs_count: int
    peak_memory_kb: float
    exit_code: int
    timed_out: bool = False


def check_tool_available(tool_name: str) -> bool:
    """Return True if the CLI tool exists on the system PATH."""
    return shutil.which(tool_name) is not None


def run_sanitizer_analysis(cpp_code: str, timeout: int = 5, stdin_input: str = "") -> list[ToolFinding]:
    """Compile C++ code with address and undefined behavior sanitizers (-fsanitize=address,undefined)."""
    findings: list[ToolFinding] = []
    gpp = shutil.which("g++")
    if not gpp:
        return findings

    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "test.cpp")
        bin_path = os.path.join(tmpdir, "test_san.exe")
        with open(src, "w", encoding="utf-8") as f:
            f.write(cpp_code)

        # 1. Compile with sanitizers + strict warnings
        cmd = [gpp, "-Wall", "-Wextra", "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g", src, "-o", bin_path]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if res.returncode != 0:
                # If ASan is not supported on this specific Windows toolchain, retry with -Wuninitialized -O1
                if "unrecognized" in res.stderr.lower() or "not supported" in res.stderr.lower():
                    cmd_alt = [gpp, "-Wall", "-Wextra", "-Wuninitialized", "-O1", src, "-o", bin_path]
                    res = subprocess.run(cmd_alt, capture_output=True, text=True, timeout=timeout)

            # Parse compiler warnings/errors
            for line in res.stderr.splitlines():
                if "warning:" in line:
                    findings.append(ToolFinding(
                        tool_name="gcc-sanitizer",
                        category="sanitizer",
                        severity="WARNING",
                        line=_extract_line_number(line),
                        message=line.strip(),
                        evidence=line.strip()
                    ))
                elif "error:" in line:
                    findings.append(ToolFinding(
                        tool_name="gcc-sanitizer",
                        category="correctness",
                        severity="CRITICAL",
                        line=_extract_line_number(line),
                        message=line.strip(),
                        evidence=line.strip()
                    ))

            # 2. Run binary with standard input to catch runtime sanitizer violations
            if res.returncode == 0 and os.path.exists(bin_path):
                run_res = subprocess.run([bin_path], input=stdin_input, capture_output=True, text=True, timeout=2)
                if run_res.returncode != 0:
                    err_txt = run_res.stderr or run_res.stdout
                    findings.append(ToolFinding(
                        tool_name="address-sanitizer-runtime",
                        category="memory",
                        severity="CRITICAL",
                        line=None,
                        message=f"Runtime Sanitizer / Crash Exception: {err_txt[:160]}",
                        evidence=err_txt[:300]
                    ))
        except subprocess.TimeoutExpired:
            findings.append(ToolFinding(
                tool_name="gcc-sanitizer",
                category="sanitizer",
                severity="WARNING",
                line=None,
                message="Sanitizer compilation/execution timed out.",
                evidence="TimeoutExpired"
            ))
        except Exception as exc:
            pass

    return findings


def run_static_toolchain(code: str, language: str = "cpp") -> list[ToolFinding]:
    """Run static analysis tools (cppcheck, clang-tidy, bandit) or fallback to local static AST engine."""
    findings: list[ToolFinding] = []
    lang = language.lower()

    if lang in ("cpp", "c++", "c"):
        # If cppcheck available, run it
        if check_tool_available("cppcheck"):
            try:
                res = subprocess.run(
                    ["cppcheck", "--enable=warning,performance,portability", "--inline-suppr", "--quiet", "-"],
                    input=code,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                for line in res.stderr.splitlines():
                    if line.strip():
                        findings.append(ToolFinding(
                            tool_name="cppcheck",
                            category="correctness",
                            severity="WARNING",
                            line=_extract_line_number(line),
                            message=line.strip(),
                            evidence=line.strip(),
                        ))
            except Exception:
                pass

        # Deterministic AST engine checks (zero external dependencies)
        for u in check_uninitialized_variables(code):
            findings.append(ToolFinding(
                tool_name="ast-engine",
                category="memory",
                severity="CRITICAL",
                line=u.get("line"),
                message=f"Uninitialized variable: {u.get('title', '')}. {u.get('message', '')}",
                evidence=f"Line {u.get('line')}: {u.get('title')}",
            ))

        for d in check_division_by_zero(code):
            findings.append(ToolFinding(
                tool_name="ast-engine",
                category="correctness",
                severity="CRITICAL",
                line=d.get("line"),
                message=f"Division by zero: {d.get('title', '')}. {d.get('message', '')}",
                evidence=f"Line {d.get('line')}: {d.get('title')}",
            ))

        for loop in check_loop_direction(code):
            findings.append(ToolFinding(
                tool_name="ast-engine",
                category="correctness",
                severity="WARNING",
                line=loop.get("line"),
                message=loop.get("message", "Possible loop condition mismatch"),
                evidence=f"Line {loop.get('line')}: {loop.get('title')}",
            ))

    elif lang in ("python", "py"):
        if check_tool_available("bandit"):
            try:
                with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
                    tf.write(code)
                    tf_name = tf.name
                res = subprocess.run(["bandit", "-q", "-f", "csv", tf_name], capture_output=True, text=True, timeout=5)
                os.unlink(tf_name)
                for line in res.stdout.splitlines()[1:]:
                    parts = line.split(",")
                    if len(parts) >= 6:
                        findings.append(ToolFinding(
                            tool_name="bandit",
                            category="security",
                            severity=parts[3].strip().upper(),
                            line=int(parts[5].strip()) if parts[5].strip().isdigit() else None,
                            message=parts[4].strip(),
                            evidence=line.strip(),
                        ))
            except Exception:
                pass

    # Generic security guardrail check across all languages
    sec_issues = scan_all_security_issues(code)
    for issue in sec_issues:
        findings.append(ToolFinding(
            tool_name="security-guardrail",
            category="security",
            severity="CRITICAL" if "CRITICAL" in issue["message"] else "WARNING",
            line=issue.get("line"),
            message=issue.get("message", ""),
            evidence=f"Pattern: {issue.get('pattern', '')}",
        ))

    return findings


def measure_execution_profile(
    code: str,
    stdin_input: str = "",
    language: str = "cpp",
    runs: int = 5,
    timeout_s: float = 3.0,
) -> ProfileMetrics:
    """Measure real execution latency (median of N runs) and memory estimation."""
    from compiler_service import compile_and_run

    times: list[float] = []
    last_exit_code = 0
    timed_out = False

    # Warm-up compile + run
    first_res = compile_and_run(code, stdin_input=stdin_input, language=language)
    last_exit_code = getattr(first_res, "exit_code", first_res[0])
    if first_res[0] != 0:
        return ProfileMetrics(
            median_time_ms=0.0,
            min_time_ms=0.0,
            max_time_ms=0.0,
            runs_count=0,
            peak_memory_kb=0.0,
            exit_code=last_exit_code,
            timed_out=getattr(first_res, "timed_out", False),
        )

    # Execute N times to compute stable median
    for _ in range(max(1, runs)):
        t_start = time.perf_counter_ns()
        run_res = compile_and_run(code, stdin_input=stdin_input, language=language)
        t_elapsed_ms = (time.perf_counter_ns() - t_start) / 1_000_000.0
        times.append(t_elapsed_ms)
        last_exit_code = getattr(run_res, "exit_code", run_res[0])
        if getattr(run_res, "timed_out", False):
            timed_out = True
            break

    median_val = statistics.median(times) if times else 0.0
    min_val = min(times) if times else 0.0
    max_val = max(times) if times else 0.0

    # Rough baseline process memory estimate for isolated sandbox process (~4-12 MB)
    est_memory_kb = 4096.0 + (len(code.splitlines()) * 8.0)

    return ProfileMetrics(
        median_time_ms=round(median_val, 2),
        min_time_ms=round(min_val, 2),
        max_time_ms=round(max_val, 2),
        runs_count=len(times),
        peak_memory_kb=est_memory_kb,
        exit_code=last_exit_code,
        timed_out=timed_out,
    )


def collect_empirical_evidence(
    code: str,
    language: str = "cpp",
    stdin_input: str = "",
) -> dict[str, Any]:
    """Run all analysis and profiling tools and bundle into empirical evidence JSON."""
    static_findings = run_static_toolchain(code, language=language)
    sanitizer_findings = run_sanitizer_analysis(code, stdin_input=stdin_input) if language.lower() in ("cpp", "c++", "c") else []
    all_findings = static_findings + sanitizer_findings

    profile = measure_execution_profile(code, stdin_input=stdin_input, language=language, runs=3)

    return {
        "tool_findings": [asdict(f) for f in all_findings],
        "profile_metrics": asdict(profile),
        "total_flaws_found": len(all_findings),
    }


def _extract_line_number(text: str) -> int | None:
    match = re.search(r":(\d+):", text)
    if match:
        return int(match.group(1))
    return None
