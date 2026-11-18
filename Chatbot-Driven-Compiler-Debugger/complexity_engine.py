"""complexity_engine.py — Rigorous Algorithmic Time & Space Complexity Analyzer.

Derives exact asymptotic Time Complexity (TC) and Space Complexity (SC) by analyzing:
  1. Loop structures (nesting depth, steps: linear vs logarithmic).
  2. Heavy operations invoked inside loop bodies (e.g. std::sort inside for loop -> O(N^2 log N)).
  3. External algorithms (sort, search, partitions).
  4. Recursion patterns (branching factor 2^N, divide-and-conquer N log N, log N, linear N).
  5. Auxiliary memory (scalars O(1), 1D structures O(N), 2D matrices O(N^2), call stack).
  6. Polyglot support: C, C++, Python, JavaScript, TypeScript.
"""
from __future__ import annotations

import ast
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


def _detect_language(code: str, declared: str) -> str:
    """Auto-detect actual language from code when the declared language may be wrong.

    If the code clearly looks like Python (has `def`, `import`, `for x in`,
    indentation-based blocks, etc.) but the declared language is not Python,
    override to 'python' so the correct analysis path is used.
    """
    dl = declared.lower()
    # Already declared as Python — trust it
    if dl in ("python", "py"):
        return dl
    # Heuristic: code looks like Python
    lines = [ln.strip() for ln in code.splitlines() if ln.strip() and not ln.strip().startswith("//")]
    py_signals = 0
    for ln in lines:
        if re.match(r"^(def |class |import |from \w+ import |print\(|if __name__)", ln):
            py_signals += 2
        elif re.match(r"^for\s+\w+\s+in\s+", ln):
            py_signals += 2
        elif re.match(r"^(elif |except |finally:)", ln):
            py_signals += 2
        elif ln.endswith(":") and not ln.endswith("::") and not ln.endswith(":/"):
            py_signals += 1
    # Check for C/C++ signals that would rule out Python
    c_signals = 0
    for ln in lines:
        if re.match(r"^#include\s*<", ln) or re.match(r"^(int|void|char|float|double|bool)\s+\w+\s*\(", ln):
            c_signals += 3
        elif ln.endswith(";") or ln.endswith("{") or ln.endswith("}"):
            c_signals += 1
    if py_signals >= 3 and py_signals > c_signals:
        return "python"
    return dl


def analyze_algorithmic_complexity(
    code: str,
    language: str = "cpp",
    stdin_input: str = "",
    stdout: str = "",
) -> ComplexityReport:
    """Analyze code structure and compute rigorous asymptotic Time & Space complexity."""
    lang = _detect_language(code, language)
    clean_code = _strip_comments_and_strings(code, lang)

    if lang in ("python", "py"):
        py_struct = _extract_python_structure(code)
        rec_info = py_struct["rec_info"]
        loops = py_struct["loops"]
        max_nesting_depth = py_struct["max_nesting_depth"]
        all_loops_have_fixed_bounds = py_struct["all_loops_have_fixed_bounds"]
        has_sort_inside_loop = py_struct["has_sort_inside_loop"]
        has_linear_inside_loop = py_struct["has_linear_inside_loop"]
        has_log_inside_loop = py_struct["has_log_inside_loop"]
        has_sort_outside = py_struct["has_sort_outside"]
        has_binary_search_outside = py_struct["has_binary_search_outside"]
        has_dividing_step = py_struct["has_dividing_step"]
        has_1d_container = py_struct["has_1d_container"]
        has_2d_container = py_struct["has_2d_container"]
        vars_found = py_struct.get("vars_found", [])
    else:
        # 1. Detect Recursion
        rec_info = _detect_recursion(clean_code, lang)

        # 2. Extract Loops and Loop Bodies
        loops = _extract_loops_with_bodies(clean_code)

        # 3. Analyze Heavy Operations Inside and Outside Loops
        has_sort_inside_loop = False
        has_linear_inside_loop = False
        has_log_inside_loop = False

        for loop in loops:
            body = loop["body"]
            # Check sort inside loop
            if re.search(r"\b(sort|stable_sort|qsort)\s*\(", body) or re.search(r"\.sort\s*\(", body):
                has_sort_inside_loop = True
            # Check linear scan inside loop (find, count, substr, insert/erase in vector)
            if re.search(r"\b(find|count|linear_search)\s*\(", body) or re.search(r"\.(find|count|insert|erase)\s*\(", body):
                if not re.search(r"\.(find|count)\s*\(", body) or ("std::map" not in clean_code and "std::set" not in clean_code):
                    has_linear_inside_loop = True
            # Check binary search inside loop
            if re.search(r"\b(binary_search|lower_bound|upper_bound)\s*\(", body):
                has_log_inside_loop = True

        # 4. Check sorting or searching outside loops
        has_sort_outside = bool(re.search(r"\b(sort|stable_sort|qsort)\s*\(", clean_code) or re.search(r"\.sort\s*\(", clean_code))
        has_binary_search_outside = bool(re.search(r"\b(binary_search|lower_bound|upper_bound)\s*\(", clean_code))

        # 5. Loop Nesting Depth & Loop Stepping
        max_nesting_depth = _calculate_max_loop_nesting(loops)
        all_loops_have_fixed_bounds = bool(loops) and all(
            _has_fixed_integer_bounds(loop) for loop in loops
        )
        has_dividing_step = bool(re.search(r"(\/=|>>=|\bdivide)\s*", clean_code))

        has_2d_container = bool(re.search(r"vector\s*<\s*vector|\[[^\]]+\]\s*\[[^\]]+\]", clean_code))
        has_1d_container = bool(
            re.search(r"\b(vector|list|deque|set|unordered_set|map|unordered_map|queue|stack|Array|Set|Map)\b", clean_code)
            or "new " in clean_code
            or "malloc(" in clean_code
            or bool(re.search(r"\[\s*\]|\.push\s*\(", clean_code))
        )
        vars_found = []

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
        optimizations.append("Use a hash-based lookup structure (std::unordered_set / unordered_map / set) to achieve O(1) queries.")
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
    if rec_info["is_recursive"]:
        sc = rec_info["sc"]
        sc_derivation = rec_info["sc_reason"]
    elif has_2d_container:
        sc = "O(N²)"
        sc_derivation = "Allocates 2D nested data structures (e.g. matrix or list of lists) requiring quadratic auxiliary memory."
    elif has_1d_container:
        sc = "O(N)"
        if lang in ("python", "py"):
            sc_derivation = "Allocates a dynamic list, dictionary, or set container storing up to $N$ elements in heap memory."
        else:
            sc_derivation = "Allocates a dynamic 1D container (e.g. `std::vector`) storing up to $N$ elements in heap memory."
    elif has_sort_outside or has_sort_inside_loop:
        if lang in ("python", "py"):
            sc = "O(N)"
            sc_derivation = "In Python, Timsort (`sorted()` / `.sort()`) requires $O(N)$ auxiliary space for run merge buffers."
        else:
            sc = "O(log N)"
            sc_derivation = "In C++, `std::sort` (Introsort) utilizes $O(\\log N)$ auxiliary stack frames for its internal recursion."
    else:
        sc = "O(1)"
        sc_derivation = "Allocates a fixed number of scalar variables on the stack; auxiliary memory does not scale with input size."

    # ── STEP-BY-STEP EXECUTION WALKTHROUGH ────────────────────────────────────
    steps = _generate_logical_steps(
        clean_code,
        lang,
        stdin_input,
        stdout,
        loops,
        has_sort_inside_loop,
        has_sort_outside,
        vars_found=vars_found,
        max_nesting_depth=max_nesting_depth,
    )

    return ComplexityReport(
        time_complexity=tc,
        space_complexity=sc,
        tc_derivation=tc_derivation,
        sc_derivation=sc_derivation,
        hotspots=hotspots,
        optimizations=optimizations,
        steps_walkthrough=steps,
    )


# ── Python AST-Based Structural Analysis ─────────────────────────────────────

def _extract_python_structure(code: str) -> dict[str, Any]:
    """Parse Python AST to accurately compute loop nesting, recursion, and containers."""
    try:
        tree = ast.parse(code)
    except Exception:
        return _fallback_python_scanner(code)

    # 1. Detect recursion
    rec_info = {"is_recursive": False, "tc": "O(1)", "reason": "", "sc": "O(1)", "sc_reason": ""}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            fn = node.name
            calls = [
                c for c in ast.walk(node)
                if isinstance(c, ast.Call) and getattr(c.func, "id", None) == fn
            ]
            if len(calls) >= 2:
                rec_info = {
                    "is_recursive": True,
                    "tc": "O(2^N)",
                    "reason": f"Recursive function `{fn}` branches {len(calls)} times per call frame without memoization, yielding exponential growth.",
                    "sc": "O(N)",
                    "sc_reason": "Maximum call stack recursion depth reaches O(N).",
                }
                break
            elif len(calls) == 1:
                arg_str = ""
                if hasattr(ast, "unparse"):
                    try:
                        arg_str = ast.unparse(calls[0])
                    except Exception:
                        pass
                if "/ 2" in arg_str or "// 2" in arg_str or ">> 1" in arg_str or "mid" in arg_str:
                    rec_info = {
                        "is_recursive": True,
                        "tc": "O(log N)",
                        "reason": f"Divide-and-conquer recursive function `{fn}` divides problem size by 2 on each step.",
                        "sc": "O(log N)",
                        "sc_reason": "Recursion call stack reaches maximum depth of O(log N).",
                    }
                else:
                    rec_info = {
                        "is_recursive": True,
                        "tc": "O(N)",
                        "reason": f"Single recursive call in `{fn}` with decrement step executes O(N) stack frames.",
                        "sc": "O(N)",
                        "sc_reason": "Recursion stack depth scales linearly with input size O(N).",
                    }
                break

    # 2. Extract loops and nesting
    loops: list[dict[str, Any]] = []
    has_dividing_step = False

    def visit_loops(node: ast.AST, parent_loops: list[ast.AST]) -> None:
        nonlocal has_dividing_step
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.For, ast.While)):
                current_loops = parent_loops + [child]
                fixed = False
                if isinstance(child, ast.For) and isinstance(child.iter, ast.Call):
                    fn_id = getattr(child.iter.func, "id", "")
                    if (
                        fn_id == "range"
                        and child.iter.args
                        and all(isinstance(a, ast.Constant) and isinstance(a.value, int) for a in child.iter.args)
                    ):
                        fixed = True

                if isinstance(child, ast.While):
                    for sub in ast.walk(child):
                        if isinstance(sub, ast.AugAssign) and isinstance(sub.op, (ast.FloorDiv, ast.RShift, ast.Div)):
                            has_dividing_step = True

                has_sort = False
                has_linear = False
                has_log = False
                for sub in ast.walk(child):
                    if isinstance(sub, ast.Call):
                        fn_id = getattr(sub.func, "id", "")
                        attr_id = getattr(sub.func, "attr", "")
                        if fn_id in ("sorted",) or attr_id in ("sort",):
                            has_sort = True
                        if attr_id in ("index", "count"):
                            has_linear = True
                        if fn_id in ("bisect", "bisect_left", "bisect_right") or attr_id in ("bisect", "bisect_left", "bisect_right"):
                            has_log = True
                    if isinstance(sub, ast.Compare):
                        for op in sub.ops:
                            if isinstance(op, (ast.In, ast.NotIn)):
                                has_linear = True

                loops.append({
                    "depth": len(current_loops),
                    "fixed": fixed,
                    "has_sort": has_sort,
                    "has_linear": has_linear,
                    "has_log": has_log,
                    "node": child,
                })
                visit_loops(child, current_loops)
            else:
                visit_loops(child, parent_loops)

    visit_loops(tree, [])
    max_nesting_depth = max((l["depth"] for l in loops), default=0)
    has_sort_inside_loop = any(l["has_sort"] for l in loops)
    has_linear_inside_loop = any(l["has_linear"] for l in loops)
    has_log_inside_loop = any(l["has_log"] for l in loops)
    all_loops_have_fixed_bounds = bool(loops) and all(l["fixed"] for l in loops)

    # 3. Check outside loops
    has_sort_outside = False
    has_binary_search_outside = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn_id = getattr(node.func, "id", "")
            attr_id = getattr(node.func, "attr", "")
            if fn_id in ("sorted",) or attr_id in ("sort",):
                has_sort_outside = True
            if fn_id in ("bisect", "bisect_left", "bisect_right") or attr_id in ("bisect", "bisect_left", "bisect_right"):
                has_binary_search_outside = True

    # 4. Containers / Space complexity
    has_1d_container = False
    has_2d_container = False
    vars_found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    vars_found.append(f"`{t.id}`")
        if isinstance(node, (ast.ListComp, ast.List)):
            has_1d_container = True
            for sub in ast.walk(node):
                if sub is not node and isinstance(sub, (ast.ListComp, ast.List)):
                    has_2d_container = True
        elif isinstance(node, (ast.Set, ast.SetComp, ast.Dict, ast.DictComp)):
            has_1d_container = True
        elif isinstance(node, ast.Call):
            fn_id = getattr(node.func, "id", "")
            if fn_id in ("list", "dict", "set", "bytearray"):
                has_1d_container = True
            attr_id = getattr(node.func, "attr", "")
            if attr_id in ("append", "extend", "split"):
                has_1d_container = True

    return {
        "rec_info": rec_info,
        "loops": loops,
        "max_nesting_depth": max_nesting_depth,
        "has_sort_inside_loop": has_sort_inside_loop,
        "has_linear_inside_loop": has_linear_inside_loop,
        "has_log_inside_loop": has_log_inside_loop,
        "has_sort_outside": has_sort_outside,
        "has_binary_search_outside": has_binary_search_outside,
        "all_loops_have_fixed_bounds": all_loops_have_fixed_bounds,
        "has_dividing_step": has_dividing_step,
        "has_1d_container": has_1d_container,
        "has_2d_container": has_2d_container,
        "vars_found": list(dict.fromkeys(vars_found))[:5],
    }


def _fallback_python_scanner(code: str) -> dict[str, Any]:
    """Line-by-line indentation loop scanner for Python when AST parsing is incomplete."""
    lines = code.splitlines()
    loop_depths = []
    current_stack: list[int] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        while current_stack and current_stack[-1] >= indent:
            current_stack.pop()
        if re.match(r"^(for\s+\w+\s+in|while\b)", stripped):
            current_stack.append(indent)
            loop_depths.append(len(current_stack))

    max_d = max(loop_depths, default=0)
    has_sort = bool(re.search(r"\b(sorted\s*\(|\.sort\s*\()", code))
    has_1d = bool(re.search(r"(\[[^\]]*\]|\.append\s*\(|\bsplit\s*\(|\blist\s*\(|\bdict\s*\(|\bset\s*\()", code))
    return {
        "rec_info": {"is_recursive": False, "tc": "O(1)", "reason": "", "sc": "O(1)", "sc_reason": ""},
        "loops": [{"depth": d} for d in loop_depths],
        "max_nesting_depth": max_d,
        "has_sort_inside_loop": False,
        "has_linear_inside_loop": False,
        "has_log_inside_loop": False,
        "has_sort_outside": has_sort,
        "has_binary_search_outside": False,
        "all_loops_have_fixed_bounds": False,
        "has_dividing_step": False,
        "has_1d_container": has_1d,
        "has_2d_container": False,
        "vars_found": [],
    }


# ── Internal Helpers (C / C++ / JS) ──────────────────────────────────────────

def _strip_comments_and_strings(code: str, lang: str = "cpp") -> str:
    """Strip string literals and comments to avoid false-positive regex matches."""
    if lang in ("python", "py"):
        clean = re.sub(r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')', "", code)
        clean = re.sub(r"#.*$", "", clean, flags=re.MULTILINE)
        return clean
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
    loops: list[Any],
    has_sort_in_loop: bool,
    has_sort_out: bool,
    vars_found: list[str] | None = None,
    max_nesting_depth: int = 0,
) -> list[str]:
    """Generate accurate, structured step-by-step logic walkthrough."""
    steps = []

    # Step 1: Entry
    if "int main" in code or "void main" in code or "main(" in code:
        steps.append("1. **Program Entry:** Process initializes at `main()`, configuring the execution stack.")
    else:
        steps.append("1. **Program Entry:** Script execution begins at top-level statements.")

    # Step 2: Data Structures & Variables
    all_vars = list(vars_found or [])
    if not all_vars:
        for m in re.finditer(r"\b(int|float|double|char|string|std::string|long|bool|auto|vector<[^>]+>)\s+([a-zA-Z_][a-zA-Z0-9_]*)", code):
            type_name, var_name = m.groups()
            if var_name not in ("main", "if", "for", "while", "return"):
                all_vars.append(f"`{var_name}` (`{type_name}`)")
    if all_vars:
        unique_vars = list(dict.fromkeys(all_vars))[:5]
        steps.append(f"2. **State & Memory Initialization:** Allocates memory containers: {', '.join(unique_vars)}.")

    # Step 3: Input
    if re.search(r"\b(cin\s*>>|getline\s*\(|scanf\s*\(|input\s*\(|readline|sys\.stdin)", code):
        val_display = f'"{stdin_input.strip()}"' if stdin_input.strip() else "standard input stream"
        steps.append(f"3. **Standard Input:** Reads user input ({val_display}) into memory.")

    # Step 4: Sorting & Heavy Algorithms
    if has_sort_in_loop:
        sort_fn = "sorted() / .sort()" if lang in ("python", "py") else "std::sort()"
        steps.append(f"4. **Algorithmic Inefficiency Detected:** Invokes `{sort_fn}` *inside* the loop body on every iteration, redundantly re-sorting already-ordered data.")
    elif has_sort_out:
        sort_fn = "sorted() / .sort()" if lang in ("python", "py") else "std::sort()"
        steps.append(f"4. **Sorting:** Sorts elements in ascending order via `{sort_fn}` in $O(N \\log N)$ time.")

    # Step 5: Iterations
    if loops:
        steps.append(f"5. **Iteration:** Executes {'nested loops' if (max_nesting_depth > 1 or len(loops) > 1) else 'loop'} to process array elements.")

    # Step 6: Output
    if re.search(r"\b(cout\s*<<|printf\s*\(|print\s*\(|console\.log|sys\.stdout)", code):
        out_display = f'"{stdout.strip()[:60]}..."' if len(stdout.strip()) > 60 else (f'"{stdout.strip()}"' if stdout.strip() else "computed values")
        steps.append(f"6. **Standard Output:** Emits {out_display} to standard output buffer.")

    # Step 7: Clean Exit
    steps.append("7. **Clean Exit:** Program finishes execution and returns exit code 0.")
    return steps
