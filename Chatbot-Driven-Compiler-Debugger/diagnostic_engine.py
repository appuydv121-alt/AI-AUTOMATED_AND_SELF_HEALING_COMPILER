import os
import re
import subprocess
import tempfile
import time
from functools import lru_cache
import html

from secure_scan import scan_all_security_issues
from semantic_analyzer import analyze_semantics, to_local_diagnostic
import ui_theme

DIAGNOSTIC_DEBOUNCE_SECONDS = 1.5
SYNTAX_CHECK_DEBOUNCE_SECONDS = 2.5
SYNTAX_CHECK_TIMEOUT_SECONDS = 8

CATEGORY_ORDER = {
    "ERROR": 0,
    "BUG": 1,
    "SECURITY_CRITICAL": 2,
    "LIKELY_BUG": 3,
    "SECURITY_WARNING": 4,
    "WARNING": 5,
    "POTENTIAL_ISSUE": 6,
    "SMELL": 7,
    "OPTIMIZATION": 8,
    "SUGGESTION": 9,
}
CATEGORY_ICONS = {
    "ERROR": "❌",
    "BUG": "🐛",
    "SECURITY_CRITICAL": "🚫",
    "LIKELY_BUG": "⚠️",
    "SECURITY_WARNING": "⚠️",
    "WARNING": "⚠️",
    "POTENTIAL_ISSUE": "❓",
    "SMELL": "👃",
    "OPTIMIZATION": "⚡",
    "SUGGESTION": "💡",
}
CATEGORY_LABELS = {
    "ERROR": "SYNTAX ERROR",
    "BUG": "BUG",
    "SECURITY_CRITICAL": "SECURITY WARNING",
    "LIKELY_BUG": "LIKELY BUG",
    "SECURITY_WARNING": "SECURITY WARNING",
    "WARNING": "WARNING",
    "POTENTIAL_ISSUE": "POTENTIAL ISSUE",
    "SMELL": "CODE SMELL",
    "OPTIMIZATION": "OPTIMIZATION",
    "SUGGESTION": "SUGGESTION",
}


def make_diagnostic(category, title, message, line=None, source="local", why=None, fix=None):
    return {
        "category": category,
        "title": title,
        "message": message,
        "line": line,
        "source": source,
        "why": why,
        "fix": fix,
    }


def check_security_issues(code):
    diagnostics = []
    for issue in scan_all_security_issues(code):
        message = issue["message"]
        category = "SECURITY_CRITICAL" if message.startswith("CRITICAL") else "SECURITY_WARNING"
        clean_message = message.split(":", 1)[1].strip() if ":" in message else message
        line = code.count("\n", 0, issue["match_span"][0]) + 1
        diagnostics.append(make_diagnostic(
            category=category,
            title="Security scanner match",
            message=clean_message,
            line=line,
            source="local",
            why="This construct is on the project's security denylist because it has a well-known history of memory-safety or injection vulnerabilities in C/C++.",
            fix="Follow the suggested alternative in the message above.",
        ))
    return diagnostics


def check_brace_balance(code):
    """Find likely unmatched brackets and unterminated literals without claiming compiler errors."""
    diagnostics = []
    stack = []
    pairs = {')': '(', '}': '{', ']': '['}
    openers = set(pairs.values())
    closers = set(pairs)

    in_line_comment = False
    in_block_comment = False
    in_string = False
    in_char = False
    escape = False
    line_number = 1

    index = 0
    while index < len(code):
        char = code[index]
        next_char = code[index + 1] if index + 1 < len(code) else ""

        if char == "\n":
            line_number += 1
            in_line_comment = False
            index += 1
            continue

        if in_line_comment:
            index += 1
            continue

        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 2
                continue
            index += 1
            continue

        if in_string or in_char:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif (in_string and char == '"') or (in_char and char == "'"):
                in_string = False
                in_char = False
            index += 1
            continue

        if char == "/" and next_char == "/":
            in_line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            in_block_comment = True
            index += 2
            continue
        if char == '"':
            in_string = True
            index += 1
            continue
        if char == "'":
            in_char = True
            index += 1
            continue

        if char in openers:
            stack.append((char, line_number))
        elif char in closers:
            if not stack or stack[-1][0] != pairs[char]:
                diagnostics.append(make_diagnostic(
                    category="POTENTIAL_ISSUE",
                    title=f"Unexpected '{char}'",
                    message=f"Found a closing '{char}' with no matching opening bracket on the same nesting level.",
                    line=line_number,
                    source="local",
                    why="Brackets, braces, and parentheses must be opened before they are closed.",
                    fix="Check the bracket nesting above this line.",
                ))
                if stack:
                    stack.pop()
            else:
                stack.pop()
        index += 1

    if in_string:
        diagnostics.append(make_diagnostic(
            "POTENTIAL_ISSUE", "Unterminated string literal",
            'A " was opened but never closed.', line=line_number, source="local",
            why="Every opening double-quote needs a matching closing double-quote on the same logical string.",
            fix="Add the missing closing quote.",
        ))
    if in_char:
        diagnostics.append(make_diagnostic(
            "POTENTIAL_ISSUE", "Unterminated character literal",
            "A ' was opened but never closed.", line=line_number, source="local",
            why="Every opening single-quote needs a matching closing single-quote.",
            fix="Add the missing closing quote.",
        ))
    for opener, opening_line in stack:
        closer = {value: key for key, value in pairs.items()}[opener]
        diagnostics.append(make_diagnostic(
            "POTENTIAL_ISSUE", f"Unclosed '{opener}'",
            f"This '{opener}' is never closed with a matching '{closer}'.",
            line=opening_line, source="local",
            why="Every opening bracket needs a matching closing bracket.",
            fix=f"Add a matching '{closer}'.",
        ))
    return diagnostics


GENERIC_IDENTIFIER_NAMES = {
    "value", "data", "item", "result", "number", "object", "info", "tmp", "temp",
    "var", "x", "y", "z", "a", "b", "c"
}


def _generic_identifier_base(name):
    """Normalize numeric suffixes so numbered generic names are treated consistently."""
    return re.sub(r"\d+$", "", name).lower()

KEYWORD_NAMES = {
    "if", "for", "while", "switch", "return", "case", "else", "catch", "throw", "new",
    "delete", "sizeof", "using", "namespace", "class", "struct", "enum", "template",
    "typename", "constexpr", "static", "const", "volatile", "inline", "virtual", "friend",
    "do", "try", "yield", "public", "private", "protected"
}

_TYPE_WORDS = {
    "auto", "bool", "char", "double", "float", "int", "long", "short", "signed",
    "unsigned", "size_t", "ssize_t", "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    "int8_t", "int16_t", "int32_t", "int64_t", "std::string", "std::string_view",
    "std::vector", "std::array", "std::map", "std::set", "std::unordered_map",
    "std::unordered_set", "std::pair", "std::tuple", "std::queue", "std::stack",
    "std::deque"
}


def _iter_declarations(code):
    """Yield (name, line_number, declaration_index) for actual declarations only."""
    i = 0
    line_number = 1
    length = len(code)
    while i < length:
        ch = code[i]
        if ch == '\n':
            line_number += 1
            i += 1
            continue

        if ch in ('"', "'"):
            quote = ch
            i += 1
            while i < length:
                if code[i] == '\\':
                    i += 2
                    continue
                if code[i] == quote:
                    i += 1
                    break
                if code[i] == '\n':
                    line_number += 1
                i += 1
            continue

        if ch == '/' and i + 1 < length and code[i + 1] == '/':
            i += 2
            while i < length and code[i] != '\n':
                i += 1
            continue

        if ch == '/' and i + 1 < length and code[i + 1] == '*':
            i += 2
            while i + 1 < length and not (code[i] == '*' and code[i + 1] == '/'):
                if code[i] == '\n':
                    line_number += 1
                i += 1
            i += 2
            continue

        if ch.isspace():
            i += 1
            continue

        start = i
        if ch.isalpha() or ch == '_':
            j = i + 1
            while j < length and (code[j].isalnum() or code[j] == '_'):
                j += 1
            word = code[i:j]

            if word in KEYWORD_NAMES:
                i = j
                continue

            if word in _TYPE_WORDS or word.islower() or word[0].isupper():
                k = j
                while k < length and code[k].isspace():
                    k += 1
                if k < length and code[k] in '*&':
                    k += 1
                    while k < length and code[k].isspace():
                        k += 1
                name_start = k
                if k < length and (code[k].isalpha() or code[k] == '_'):
                    n = k + 1
                    while n < length and (code[n].isalnum() or code[n] == '_'):
                        n += 1
                    candidate = code[k:n]
                    if candidate not in KEYWORD_NAMES:
                        suffix_start = n
                        while suffix_start < length and code[suffix_start].isspace():
                            suffix_start += 1
                        if suffix_start >= length or code[suffix_start] not in '=(,;)]':
                            if suffix_start < length and code[suffix_start] == '(':
                                i = j
                                continue
                            if suffix_start < length and code[suffix_start] == '<':
                                i = j
                                continue
                        line = line_number
                        yield candidate, line, start
                        i = n
                        continue

        i += 1


def check_identifier_quality(code):
    """Removed from user-facing output (H3) — produced false positives on
    single-letter math variables (a, b) and loop counters (x, y).
    Kept as a no-op so existing call sites and tests don't break."""
    return []


def _check_shadowing_tree_sitter(code: str):
    try:
        import tree_sitter_cpp as tscpp
        from tree_sitter import Language, Parser
        lang = Language(tscpp.language())
        parser = Parser(lang)
        tree = parser.parse(code.encode("utf-8"))
    except Exception:
        return None

    SCOPE_TYPES = (
        "compound_statement",
        "for_statement",
        "while_statement",
        "if_statement",
        "function_definition",
        "translation_unit",
    )

    def find_scope(node):
        p = node.parent
        while p and p.type not in SCOPE_TYPES:
            p = p.parent
        return p

    decls = []

    def walk(node):
        if node.type == "declaration":
            for child in node.children:
                if child.type in ("init_declarator", "identifier"):
                    ident = child.child_by_field_name("declarator") if child.type == "init_declarator" else child
                    while ident and ident.type in ("pointer_declarator", "reference_declarator"):
                        ident = ident.child_by_field_name("declarator")
                    if ident and ident.type == "identifier":
                        name = ident.text.decode("utf-8")
                        scope = find_scope(node)
                        line = node.start_point[0] + 1
                        decls.append((name, line, scope))
        for c in node.children:
            walk(c)

    walk(tree.root_node)

    seen_by_scope = {}
    scope_parents = {}
    diagnostics = []

    for name, line, scope in decls:
        sid = scope.id if scope else 0
        if sid not in scope_parents:
            p = scope.parent if scope else None
            while p and p.type not in SCOPE_TYPES:
                p = p.parent
            scope_parents[sid] = p.id if p else None

        if name in seen_by_scope.get(sid, set()):
            diagnostics.append(make_diagnostic(
                category="WARNING",
                title="Duplicate declaration",
                message=f"`{name}` is declared multiple times in the same scope, which will cause a compilation error.",
                line=line,
                source="local",
                why="Declaring the same variable name more than once in the same scope causes a redefinition error in C++.",
                fix="Remove the duplicate declaration or use a different variable name.",
            ))
            continue

        curr = scope_parents.get(sid)
        while curr is not None:
            if name in seen_by_scope.get(curr, set()):
                diagnostics.append(make_diagnostic(
                    category="WARNING",
                    title="Variable shadowing",
                    message=f"`{name}` is declared again in a nested scope, which shadows the outer variable and may hide bugs.",
                    line=line,
                    source="local",
                    why="Repeated variable names at different scopes can make code harder to reason about and are often a sign of accidental reuse.",
                    fix="Choose a unique name or rename the inner declaration to reflect its local purpose.",
                ))
                break
            curr = scope_parents.get(curr)

        seen_by_scope.setdefault(sid, set()).add(name)

    return diagnostics


def check_variable_shadowing(code):
    """Warn when a variable is declared in a nested scope that shadows an
    outer scope. Fixed (H4) using AST scope tree (or scope-range fallback)
    so sibling scopes (e.g. two sequential for-loops with ``int i``) no
    longer produce false positives."""
    ast_result = _check_shadowing_tree_sitter(code)
    if ast_result is not None:
        return ast_result

    diagnostics = []
    brace_positions = []
    i = 0
    length = len(code)
    in_line_comment = False
    in_block_comment = False
    in_string = False
    in_char = False
    escape = False
    while i < length:
        ch = code[i]
        nc = code[i + 1] if i + 1 < length else ''
        if ch == '\n':
            in_line_comment = False
            i += 1
            continue
        if in_line_comment:
            i += 1
            continue
        if in_block_comment:
            if ch == '*' and nc == '/':
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_string or in_char:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif (in_string and ch == '"') or (in_char and ch == "'"):
                in_string = False
                in_char = False
            i += 1
            continue
        if ch == '/' and nc == '/':
            in_line_comment = True
            i += 2
            continue
        if ch == '/' and nc == '*':
            in_block_comment = True
            i += 2
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch == "'":
            in_char = True
            i += 1
            continue
        if ch == '{':
            brace_positions.append((i, 'open'))
        elif ch == '}':
            brace_positions.append((i, 'close'))
        i += 1

    scope_ranges = []
    open_stack = []
    for pos, kind in brace_positions:
        if kind == 'open':
            open_stack.append(pos)
        elif kind == 'close' and open_stack:
            open_idx = open_stack.pop()
            scope_ranges.append((open_idx, pos))

    decl_info = []
    for name, line, index in _iter_declarations(code):
        prefix = code[max(0, index - 30):index]
        if re.search(r"for\s*\([^;)]*$", prefix):
            scope_key = (index, index + 30)
        else:
            containing = None
            for sr in scope_ranges:
                if sr[0] < index < sr[1]:
                    if containing is None or (sr[0] > containing[0]):
                        containing = sr
            scope_key = containing if containing else (-1, length)
        decl_info.append((name, line, index, scope_key))

    seen_by_scope = {}
    for name, line, index, scope_key in decl_info:
        if name in seen_by_scope.get(scope_key, set()):
            diagnostics.append(make_diagnostic(
                category="WARNING",
                title="Duplicate declaration",
                message=f"`{name}` is declared multiple times in the same scope, which will cause a compilation error.",
                line=line,
                source="local",
                why="Declaring the same variable name more than once in the same scope causes a redefinition error in C++.",
                fix="Remove the duplicate declaration or use a different variable name.",
            ))
            continue

        if scope_key != (-1, length):
            for other_scope, other_names in seen_by_scope.items():
                if other_scope == scope_key:
                    continue
                if other_scope[0] < scope_key[0] and other_scope[1] > scope_key[1]:
                    if name in other_names:
                        diagnostics.append(make_diagnostic(
                            category="WARNING",
                            title="Variable shadowing",
                            message=f"`{name}` is declared again in a nested scope, which shadows the outer variable and may hide bugs.",
                            line=line,
                            source="local",
                            why="Repeated variable names at different scopes can make code harder to reason about and are often a sign of accidental reuse.",
                            fix="Choose a unique name or rename the inner declaration to reflect its local purpose.",
                        ))
                        break

        seen_by_scope.setdefault(scope_key, set()).add(name)

    return diagnostics


def check_semantic_type_consistency(code, min_confidence=0.30):
    """Report likely variable-name/type mismatches without treating them as errors."""
    report = analyze_semantics(code, min_confidence=min_confidence)
    return [to_local_diagnostic(item) for item in report.diagnostics]


@lru_cache(maxsize=256)
def _cached_local_static_checks(code, semantic_enabled, semantic_min_confidence):
    diagnostics = (
        check_security_issues(code)
        + check_brace_balance(code)
        + check_variable_shadowing(code)
        + (check_semantic_type_consistency(code, semantic_min_confidence) if semantic_enabled else [])
    )
    return tuple(tuple(d.items()) for d in diagnostics)


def run_local_static_checks(code, *, semantic_enabled=True, semantic_min_confidence=0.30):
    """Run fast deterministic checks without a subprocess or LLM, reusing a memoized result for unchanged code."""
    if not code or not code.strip():
        return []
    cached = _cached_local_static_checks(code, semantic_enabled, semantic_min_confidence)
    return [dict(diagnostic_items) for diagnostic_items in cached]


_GCC_DIAG_RE = re.compile(
    r"^(?P<file>.+):(?P<line>\d+):(?P<col>\d+):\s*"
    r"(?P<severity>error|warning|note):\s*(?P<msg>.*)$"
)


def parse_compiler_diagnostics(stderr_text):
    """Parse GCC diagnostics for Unix and Windows paths into structured entries."""
    diagnostics = []
    if not stderr_text:
        return diagnostics
    for output_line in stderr_text.splitlines():
        match = _GCC_DIAG_RE.match(output_line.strip())
        if not match:
            continue
        severity = match.group("severity")
        category = "ERROR" if severity == "error" else "WARNING"
        diagnostics.append(make_diagnostic(
            category=category,
            title=f"Compiler {severity}",
            message=match.group("msg"),
            line=int(match.group("line")),
            source="compiler",
        ))
    return diagnostics


@lru_cache(maxsize=128)
def _cached_compiler_syntax_check(code, work_dir):
    source_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".cpp", prefix="_diag_",
            dir=work_dir, delete=False,
        ) as source_file:
            source_file.write(code)
            source_path = source_file.name

        result = subprocess.run(
            ["g++", "-fsyntax-only", "-std=c++17", source_path],
            capture_output=True,
            text=True,
            timeout=SYNTAX_CHECK_TIMEOUT_SECONDS,
        )
        return tuple((d["category"], d["title"], d["message"], d.get("line"), d.get("source")) for d in parse_compiler_diagnostics(result.stderr)), result.stderr
    except subprocess.TimeoutExpired:
        return (), "Syntax check timed out."
    except FileNotFoundError:
        return (), "g++ not found on PATH; syntax-only checking is unavailable."
    except Exception as error:
        return (), f"Syntax check error: {error}"
    finally:
        if source_path and os.path.exists(source_path):
            os.unlink(source_path)


def run_compiler_syntax_check(code, work_dir):
    """Run a bounded syntax-only GCC check using a unique, temporary source file."""
    cached = _cached_compiler_syntax_check(code, work_dir)
    diagnostics = [
        {
            "category": category,
            "title": title,
            "message": message,
            "line": line,
            "source": source,
        }
        for category, title, message, line, source in cached[0]
    ]
    return diagnostics, cached[1]


def should_run_debounced_check(session_state, code, now=None):
    """Return true once a changed buffer has been quiet long enough for GCC."""
    current_time = time.time() if now is None else now
    if code != session_state.get("diag_last_code"):
        return False
    if session_state.get("diag_syntax_checked_code") == code:
        return False
    last_change_time = session_state.get("diag_last_time", 0)
    return current_time - last_change_time >= SYNTAX_CHECK_DEBOUNCE_SECONDS


def merge_diagnostics(*diagnostic_lists):
    """Combine entries, deduplicating by line and message, then order by severity.

    Extended to fold diagnostics sharing ``fact_ids`` — a reasoning finding
    that supersedes a compiler fact replaces the raw compiler diagnostic.
    """
    seen = set()
    merged = []
    compiler_error_lines = {
        diagnostic.get("line")
        for diagnostics in diagnostic_lists
        for diagnostic in diagnostics
        if diagnostic.get("source") == "compiler"
        and diagnostic.get("category") == "ERROR"
        and diagnostic.get("line") is not None
    }
    # Collect all superseded fact IDs from reasoning findings
    superseded = set()
    for diagnostics in diagnostic_lists:
        for diagnostic in diagnostics:
            for fid in diagnostic.get("supersedes", []):
                superseded.add(fid)
    for diagnostics in diagnostic_lists:
        for diagnostic in diagnostics:
            if diagnostic.get("source") == "semantic" and diagnostic.get("line") in compiler_error_lines:
                continue
            # Skip if this diagnostic's fact_ids are superseded by a reasoning finding
            diag_fact_ids = diagnostic.get("fact_ids", [])
            if diag_fact_ids and all(fid in superseded for fid in diag_fact_ids):
                if diagnostic.get("source") != "reasoning":
                    continue
            key = (diagnostic.get("line"), diagnostic.get("message"))
            if key not in seen:
                seen.add(key)
                merged.append(diagnostic)
    merged.sort(key=lambda diagnostic: (
        CATEGORY_ORDER.get(diagnostic["category"], 99), diagnostic.get("line") or 0
    ))
    return merged


def render_diagnostics_markdown(diagnostics):
    if not diagnostics:
        return "✓ No immediate issues detected. (This does not guarantee the code is fully correct — it means no local checks or compiler diagnostics found a problem yet.)"

    lines = ["### 🔎 AI Code Analysis\n"]
    for diagnostic in diagnostics:
        icon = CATEGORY_ICONS.get(diagnostic["category"], "•")
        label = CATEGORY_LABELS.get(diagnostic["category"], diagnostic["category"])
        header = f"{icon} **{label}**"
        if diagnostic.get("line"):
            header += f" — Line {diagnostic['line']}"
        lines.append(header)
        lines.append(f"\n{diagnostic['message']}\n")
        if diagnostic.get("why"):
            lines.append(f"*Why:* {diagnostic['why']}")
        if diagnostic.get("fix"):
            lines.append(f"*Suggested fix:* {diagnostic['fix']}")
        semantic = diagnostic.get("semantic")
        if semantic:
            lines.append(
                f"*Type check:* `{semantic['declared_type']}` vs "
                f"`{semantic['expected_category']}` ({semantic['confidence_label'].lower()} confidence)."
            )
        lines.append("\n---\n")
    return "\n".join(lines)


def render_diagnostics_html(diagnostics):
    """Render the existing diagnostic records as accessible, severity-coded status blocks."""
    if not diagnostics:
        empty_message = (
            "No immediate issues detected. (This does not guarantee the code is fully correct — "
            "it means no local checks or compiler diagnostics found a problem yet.)"
        )
        block = ui_theme.severity_block("success", "NO ISSUES", empty_message, icon="✓")
        return f'<section aria-label="AI Code Analysis"><h3>🔎 AI Code Analysis</h3>{block}</section>'

    tone_by_category = {
        "ERROR": ("error", "❌"),
        "BUG": ("error", "🐛"),
        "SECURITY_CRITICAL": ("error", "🚫"),
        "LIKELY_BUG": ("warning", "⚠️"),
        "SECURITY_WARNING": ("warning", "⚠️"),
        "WARNING": ("warning", "⚠️"),
        "POTENTIAL_ISSUE": ("info", "❓"),
        "SMELL": ("info", "👃"),
        "OPTIMIZATION": ("suggestion", "⚡"),
        "SUGGESTION": ("suggestion", "💡"),
    }
    severity_counts = {}
    for diagnostic in diagnostics:
        label = CATEGORY_LABELS.get(diagnostic["category"], diagnostic["category"])
        severity_counts[label] = severity_counts.get(label, 0) + 1

    count_markup = "".join(
        f'<span class="ui-chip ui-status-{tone}">{html.escape(label)} {count}</span>'
        for label, count, tone in (
            ("ERROR", severity_counts.get("SYNTAX ERROR", 0) + severity_counts.get("BUG", 0), "error"),
            ("WARNING", severity_counts.get("WARNING", 0) + severity_counts.get("SECURITY WARNING", 0) + severity_counts.get("LIKELY BUG", 0), "warning"),
            ("INFO", severity_counts.get("POTENTIAL ISSUE", 0) + severity_counts.get("CODE SMELL", 0), "info"),
            ("SUGGESTION", severity_counts.get("SUGGESTION", 0) + severity_counts.get("OPTIMIZATION", 0), "suggestion"),
        )
        if count
    )
    blocks = []
    for diagnostic in diagnostics:
        category = diagnostic["category"]
        tone, icon = tone_by_category.get(category, ("info", "•"))
        label = CATEGORY_LABELS.get(category, category)
        line = f"Line {diagnostic['line']}" if diagnostic.get("line") else None
        details = []
        if diagnostic.get("why"):
            details.append(("Why", diagnostic["why"]))
        if diagnostic.get("fix"):
            details.append(("Suggested fix", diagnostic["fix"]))
        semantic = diagnostic.get("semantic")
        if semantic:
            details.append((
                "Type check",
                f"{semantic['declared_type']} vs {semantic['expected_category']} "
                f"({semantic['confidence_label'].lower()} confidence).",
            ))
        blocks.append(ui_theme.severity_block(
            tone,
            label,
            diagnostic["message"],
            icon=icon,
            line=line,
            details=tuple(details),
        ))

    return (
        '<section aria-label="AI Code Analysis">'
        '<div class="ui-diagnostic-header"><h3>🔎 AI Code Analysis</h3>'
        f'<div class="ui-diagnostic-counts">{count_markup}</div></div>'
        + "".join(blocks)
        + "</section>"
    )