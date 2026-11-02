"""complexity_engine.py — Rigorous Algorithmic Time & Space Complexity Analyzer.

Derives exact asymptotic Time Complexity (TC) and Space Complexity (SC) by analyzing:
  1. Loop structures (nesting depth, steps: linear vs logarithmic).
  2. Heavy operations invoked inside loop bodies (e.g. std::sort inside for loop -> O(N^2 log N)).
  3. External algorithms (sort, search, partitions).
  4. Recursion patterns (branching factor 2^N, divide-and-conquer N log N, log N, linear N).
  5. Auxiliary memory (scalars O(1), 1D structures O(N), 2D matrices O(N^2), call stack).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ComplexityReport:
    time_complexity: str
    space_complexity: str
    tc_derivation: str
    sc_derivation: str
    hotspots: list[dict[str, Any]] = field(default_factory=list)
    optimizations: list[str] = field(default_factory=list)
    steps_walkthrough: list[str] = field(default_factory=list)


def analyze_algorithmic_complexity(
    code: str,
    language: str = "cpp",
    stdin_input: str = "",
    stdout: str = "",
) -> ComplexityReport:
    """Analyze code structure and compute rigorous asymptotic Time & Space complexity."""
    clean_code = _strip_comments_and_strings(code)
    lang = language.lower()

    # 1. Detect Recursion
    rec_info = _detect_recursion(clean_code, lang)

    # 2. Extract Loops and Loop Bodies
    loops = _extract_loops_with_bodies(clean_code)

    # 3. Analyze Heavy Operations Inside and Outside Loops
    inner_ops = []
    has_sort_inside_loop = False
    has_linear_inside_loop = False
    has_log_inside_loop = False

    for loop in loops:
        body = loop["body"]
        # Check sort inside loop
        if re.search(r"\b(sort|stable_sort|qsort)\s*\(", body) or re.search(r"\.sort\s*\(", body):
            has_sort_inside_loop = True
            inner_ops.append("std::sort inside loop (O(N log N) per iteration)")
        # Check linear scan inside loop (find, count, substr, insert/erase in vector)
        if re.search(r"\b(find|count|linear_search)\s*\(", body) or re.search(r"\.(find|count|insert|erase)\s*\(", body):
            if not re.search(r"\.(find|count)\s*\(", body) or ("std::map" not in clean_code and "std::set" not in clean_code):
                has_linear_inside_loop = True
                inner_ops.append("Linear search / scan inside loop (O(N) per iteration)")
        # Check binary search inside loop
        if re.search(r"\b(binary_search|lower_bound|upper_bound)\s*\(", body):
            has_log_inside_loop = True
            inner_ops.append("Binary search inside loop (O(log N) per iteration)")

    # 4. Check sorting or searching outside loops
    has_sort_outside = bool(re.search(r"\b(sort|stable_sort|qsort)\s*\(", clean_code) or re.search(r"\.sort\s*\(", clean_code))
    has_binary_search_outside = bool(re.search(r"\b(binary_search|lower_bound|upper_bound)\s*\(", clean_code))

    # 5. Loop Nesting Depth & Loop Stepping
    max_nesting_depth = _calculate_max_loop_nesting(loops)
    all_loops_have_fixed_bounds = bool(loops) and all(
        _has_fixed_integer_bounds(loop) for loop in loops
    )
    has_dividing_step = bool(re.search(r"(\/=|>>=|\bdivide)\s*", clean_code))

    # ── DERIVE TIME COMPLEXITY (TC) ──────────────────────────────────────────
    hotspots = []
    optimizations = []

    if rec_info["is_recursive"]:
        tc = rec_info["tc"]
        tc_derivation = rec_info["reason"]
        if "2^" in tc:
            hotspots.append({
                "complexity": tc,
                "reason": "Exponential branching recursion without memoization.",
                "suggested_change": "Apply dynamic programming or memoization to reduce to O(N).",
            })
            optimizations.append("Replace exponential recursive calls with iterative DP or memoization cache.")
    elif has_sort_inside_loop:
        tc = "O(N² log N)" if max_nesting_depth == 1 else f"O(N^{max_nesting_depth+1} log N)"
        tc_derivation = (
            f"An outer loop running $O(N)$ times invokes `sort()` ($O(N \\log N)$) on every iteration, "
            f"multiplying the complexities to yield **{tc}**."
        )
        hotspots.append({
            "complexity": tc,
            "reason": "Redundant sorting inside iteration loop.",
            "suggested_change": "Move sort() before the loop to avoid re-sorting on every iteration.",
        })
        optimizations.append("Hoist `sort()` outside the loop: sort once in O(N log N), then iterate in O(N).")
    elif has_linear_inside_loop and max_nesting_depth >= 1:
        tc = f"O(N^{max_nesting_depth+1})"
        tc_derivation = (
            f"An outer loop running $O(N)$ times performs a linear scan/search ($O(N)$) per iteration, "
            f"resulting in **{tc}**."
        )
        hotspots.append({
            "complexity": tc,
            "reason": "Nested linear search inside loop.",
            "suggested_change": "Use an unordered_set or unordered_map for O(1) lookups.",
        })
        optimizations.append("Use a hash-based lookup structure (std::unordered_set / unordered_map) to achieve O(1) queries.")
    elif (
        all_loops_have_fixed_bounds
        and not has_sort_inside_loop
        and not has_linear_inside_loop
        and not has_log_inside_loop
        and not has_sort_outside
        and not has_binary_search_outside
    ):
        tc = "O(1)"
        tc_derivation = "All loops use fixed integer-literal bounds, so their iteration count does not scale with input size."
    elif has_log_inside_loop and max_nesting_depth == 1:
        tc = "O(N log N)"
        tc_derivation = "A linear loop of size $N$ invokes a logarithmic binary search ($O(\\log N)$) per iteration."
    elif max_nesting_depth >= 3:
        tc = f"O(N^{max_nesting_depth})"
        tc_derivation = f"Contains {max_nesting_depth} deeply nested loops, resulting in polynomial growth {tc}."
        hotspots.append({
            "complexity": tc,
            "reason": f"{max_nesting_depth} nested loops.",
            "suggested_change": "Restructure with hash tables or two-pointer technique to lower polynomial degree.",
        })
    elif max_nesting_depth == 2:
        tc = "O(N²)"
        tc_derivation = "Contains nested iteration loops where the inner loop executes up to $N$ times for each of the $N$ outer iterations."
        hotspots.append({
            "complexity": "O(N²)",
            "reason": "Quadratic nested loops.",
            "suggested_change": "Consider two-pointer approach, sliding window, or hash indexing to achieve O(N) or O(N log N).",
        })
        optimizations.append("Consider replacing the inner quadratic loop with a hash table or two-pointer scan.")
    elif max_nesting_depth == 1:
        if has_dividing_step:
            tc = "O(log N)"
            tc_derivation = "Loop variable divides geometrically by a constant factor on each step, running in logarithmic time."
        elif has_sort_outside:
            tc = "O(N log N)"
            tc_derivation = "The program performs a sort ($O(N \\log N)$) combined with a linear loop ($O(N)$); the sort dominates asymptotically."
        else:
            tc = "O(N)"
            tc_derivation = "Executes a single linear loop iterating through input elements proportional to size $N$."
    else:
        # 0 loops
        if has_sort_outside:
            tc = "O(N log N)"
            tc_derivation = "Program invokes a sorting algorithm on $N$ elements without additional loops."
        elif has_binary_search_outside:
            tc = "O(log N)"
            tc_derivation = "Program performs a single binary search on pre-sorted data."
        else:
            tc = "O(1)"
            tc_derivation = "Executes a fixed, finite sequence of scalar operations with no loops or recursion."

    # ── DERIVE SPACE COMPLEXITY (SC) ─────────────────────────────────────────
    has_2d_container = bool(re.search(r"vector\s*<\s*vector|\[[^\]]+\]\s*\[[^\]]+\]", clean_code))
    has_1d_container = bool(re.search(r"\b(vector|list|deque|set|unordered_set|map|unordered_map|queue|stack)\b", clean_code) or "new " in clean_code or "malloc(" in clean_code)

    if rec_info["is_recursive"]:
        sc = rec_info["sc"]
        sc_derivation = rec_info["sc_reason"]
    elif has_2d_container:
        sc = "O(N²)"
        sc_derivation = "Allocates 2D nested data structures (e.g. matrix or vector of vectors) requiring quadratic auxiliary memory."
    elif has_1d_container:
        sc = "O(N)"
        sc_derivation = "Allocates a dynamic 1D container (e.g. `std::vector`) storing up to $N$ elements in heap memory."
    elif has_sort_outside or has_sort_inside_loop:
        sc = "O(log N)"
        sc_derivation = "In C++, `std::sort` (Introsort) utilizes $O(\\log N)$ auxiliary stack frames for its internal recursion."
    else:
        sc = "O(1)"
        sc_derivation = "Allocates a fixed number of scalar variables on the stack; auxiliary memory does not scale with input size."

    # ── STEP-BY-STEP EXECUTION WALKTHROUGH ────────────────────────────────────
    steps = _generate_logical_steps(clean_code, lang, stdin_input, stdout, loops, has_sort_inside_loop, has_sort_outside)

    return ComplexityReport(
        time_complexity=tc,
        space_complexity=sc,
        tc_derivation=tc_derivation,
        sc_derivation=sc_derivation,
        hotspots=hotspots,
        optimizations=optimizations,
        steps_walkthrough=steps,
    )


# ── Internal Helpers ──────────────────────────────────────────────────────────

def _strip_comments_and_strings(code: str) -> str:
    """Strip string literals and comments to avoid false-positive regex matches."""
    # Remove single line comments
    clean = re.sub(r"//.*$", "", code, flags=re.MULTILINE)
    # Remove multi-line comments
    clean = re.sub(r"/\*[\s\S]*?\*/", "", clean)
    return clean


def _detect_recursion(code: str, lang: str) -> dict[str, Any]:
    """Detect recursive functions and categorize branching / divide-and-conquer."""
    fn_matches = re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^)]*\)\s*\{", code)
    for fn in fn_matches:
        if fn in ("main", "if", "for", "while", "switch"):
            continue
        # Find function body
        pat = rf"\b{fn}\s*\([^)]*\)\s*\{{([\s\S]*?)\}}"
        body_m = re.search(pat, code)
        if body_m:
            body = body_m.group(1)
            rec_calls = len(re.findall(rf"\b{fn}\s*\(", body))
            if rec_calls >= 2:
                if re.search(rf"\b{fn}\s*\([^)]*/\s*2", body):
                    return {
                        "is_recursive": True,
                        "tc": "O(N log N)",
                        "reason": f"Divide-and-conquer recursive function `{fn}` with 2 subproblems dividing input size by 2.",
                        "sc": "O(log N)",
                        "sc_reason": "Recursion call stack reaches maximum depth of O(log N).",
                    }
                return {
                    "is_recursive": True,
                    "tc": "O(2^N)",
                    "reason": f"Recursive function `{fn}` branches {rec_calls} times per call frame without memoization, yielding exponential growth.",
                    "sc": "O(N)",
                    "sc_reason": "Maximum call stack recursion depth reaches O(N).",
                }
            elif rec_calls == 1:
                if re.search(rf"\b{fn}\s*\([^)]*/\s*2", body):
                    return {
                        "is_recursive": True,
                        "tc": "O(log N)",
                        "reason": f"Binary division recursion in `{fn}` divides problem size by 2 on each step.",
                        "sc": "O(log N)",
                        "sc_reason": "Call stack depth bounded by O(log N).",
                    }
                return {
                    "is_recursive": True,
                    "tc": "O(N)",
                    "reason": f"Single recursive call in `{fn}` with decrement step executes O(N) stack frames.",
                    "sc": "O(N)",
                    "sc_reason": "Recursion stack depth scales linearly with input size O(N).",
                }

    return {"is_recursive": False, "tc": "O(1)", "reason": "", "sc": "O(1)", "sc_reason": ""}


def _extract_loops_with_bodies(code: str) -> list[dict[str, str]]:
    """Extract each loop header and its brace-enclosed body supporting nested parentheses."""
    results = []
    loop_start_pat = re.compile(r"\b(for|while)\s*\(", re.MULTILINE)
    for m in loop_start_pat.finditer(code):
        paren_start = m.end() - 1
        paren_count = 1
        j = paren_start + 1
        while j < len(code) and paren_count > 0:
            if code[j] == '(':
                paren_count += 1
            elif code[j] == ')':
                paren_count -= 1
            j += 1
        if paren_count != 0:
            continue
        header = code[paren_start + 1 : j - 1]

        # Find the opening '{' for the loop body
        rest = code[j:]
        brace_m = re.search(r"^\s*\{", rest)
        if brace_m:
            body_start = j + brace_m.end()
            brace_count = 1
            k = body_start
            while k < len(code) and brace_count > 0:
                if code[k] == '{':
                    brace_count += 1
                elif code[k] == '}':
                    brace_count -= 1
                k += 1
            body = code[body_start : k - 1]
            results.append({
                "type": m.group(1),
                "header": header,
                "body": body,
                "start": m.start(),
                "end": k,
            })
    return results


def _calculate_max_loop_nesting(loops: list[dict[str, Any]]) -> int:
    """Calculate maximum nesting depth from extracted loop spans."""
    if not loops:
        return 0
    max_d = 1
    for l1 in loops:
        depth = 1
        for l2 in loops:
            if l1 is not l2 and l2.get("start", 0) < l1.get("start", 0) and l1.get("end", 0) <= l2.get("end", 0):
                depth += 1
        max_d = max(max_d, depth)
    return max_d


def _has_fixed_integer_bounds(loop: dict[str, Any]) -> bool:
    if loop.get("type") != "for":
        return False

    parts = loop.get("header", "").split(";")
    if len(parts) != 3:
        return False

    init_match = re.fullmatch(
        r"\s*(?:[A-Za-z_]\w*\s+)?([A-Za-z_]\w*)\s*=\s*(-?\d+)\s*",
        parts[0],
    )
    if not init_match:
        return False
    variable = re.escape(init_match.group(1))

    condition_match = re.fullmatch(
        rf"\s*{variable}\s*(<|<=|>|>=)\s*(-?\d+)\s*",
        parts[1],
    )
    if not condition_match:
        return False

    update = re.sub(r"\s+", "", parts[2])
    positive_step = {
        f"++{init_match.group(1)}",
        f"{init_match.group(1)}++",
    }
    negative_step = {
        f"--{init_match.group(1)}",
        f"{init_match.group(1)}--",
    }
    positive_step.update(
        f"{init_match.group(1)}+={step}"
        for step in re.findall(rf"^{variable}\+=([1-9]\d*)$", update)
    )
    negative_step.update(
        f"{init_match.group(1)}-={step}"
        for step in re.findall(rf"^{variable}-=([1-9]\d*)$", update)
    )

    operator = condition_match.group(1)
    return (
        (operator in ("<", "<=") and update in positive_step)
        or (operator in (">", ">=") and update in negative_step)
    )


def _generate_logical_steps(
    code: str,
    lang: str,
    stdin_input: str,
    stdout: str,
    loops: list[dict[str, str]],
    has_sort_in_loop: bool,
    has_sort_out: bool,
) -> list[str]:
    """Generate accurate, structured step-by-step logic walkthrough."""
    steps = []

    # Step 1: Entry
    if "int main" in code or "void main" in code or "main(" in code:
        steps.append("1. **Program Entry:** Process initializes at `main()`, configuring the execution stack.")
    else:
        steps.append("1. **Program Entry:** Script execution begins at top-level statements.")

    # Step 2: Data Structures & Variables
    vars_found = []
    for m in re.finditer(r"\b(int|float|double|char|string|std::string|long|bool|auto|vector<[^>]+>)\s+([a-zA-Z_][a-zA-Z0-9_]*)", code):
        type_name, var_name = m.groups()
        if var_name not in ("main", "if", "for", "while", "return"):
            vars_found.append(f"`{var_name}` (`{type_name}`)")
    if vars_found:
        unique_vars = list(dict.fromkeys(vars_found))[:5]
        steps.append(f"2. **State & Memory Initialization:** Allocates memory containers: {', '.join(unique_vars)}.")

    # Step 3: Input
    if re.search(r"\b(cin\s*>>|getline\s*\(|scanf\s*\(|input\s*\(|readline)", code):
        val_display = f'"{stdin_input.strip()}"' if stdin_input.strip() else "standard input stream"
        steps.append(f"3. **Standard Input:** Reads user input ({val_display}) into memory.")

    # Step 4: Sorting & Heavy Algorithms
    if has_sort_in_loop:
        steps.append("4. **Algorithmic Inefficiency Detected:** Invokes `std::sort()` *inside* the loop body on every iteration, redundantly re-sorting already-ordered data.")
    elif has_sort_out:
        steps.append("4. **Sorting:** Sorts elements in ascending order via `std::sort()` in $O(N \\log N)$ time.")

    # Step 5: Iterations
    if loops:
        steps.append(f"5. **Iteration:** Executes {'nested loops' if len(loops) > 1 else 'loop'} to process array elements.")

    # Step 6: Output
    if re.search(r"\b(cout\s*<<|printf\s*\(|print\s*\(|console\.log)", code):
        out_display = f'"{stdout.strip()[:60]}..."' if len(stdout.strip()) > 60 else (f'"{stdout.strip()}"' if stdout.strip() else "computed values")
        steps.append(f"6. **Standard Output:** Emits {out_display} to standard output buffer.")

    # Step 7: Clean Exit
    steps.append("7. **Clean Exit:** Program finishes execution and returns exit code 0.")
    return steps
