"""Small, conservative C++ tokenizer and variable-declaration extractor."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    line: int
    column: int
    start: int
    end: int


@dataclass(frozen=True)
class Declaration:
    name: str
    type_text: str
    type_family: str
    is_pointer: bool
    is_const: bool
    is_auto: bool
    is_array: bool
    init_kind: str
    init_text: str | None
    literal_value: str | None
    callee: str | None
    scope: str
    line: int
    col: int
    type_span: tuple[int, int]
    init_span: tuple[int, int] | None
    stmt_span: tuple[int, int]
    multi_declarator: bool


_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_NUMBER_RE = re.compile(
    r"(?:0[xX][0-9A-Fa-f']+|0[bB][01']+|(?:\d[\d']*)(?:\.[\d']*)?"
    r"(?:[eE][+-]?[\d']+)?)(?:[uUlLfF]*)"
)
_RAW_STRING_RE = re.compile(r"(?:u8|u|U|L)?R\"([^ ()\\\t\r\n]{0,16})\(")
_MULTI_PUNCTUATION = (
    "<=>", "->*", "...", "::", "->", ".*", "++", "--", "<<", ">>",
    "<=", ">=", "==", "!=", "&&", "||", "+=", "-=", "*=", "/=",
    "%=", "&=", "|=", "^=", "##",
)
_TYPE_WORDS = {
    "auto", "bool", "char", "char8_t", "char16_t", "char32_t", "double",
    "float", "int", "long", "short", "signed", "unsigned", "void",
    "wchar_t", "size_t", "ssize_t", "ptrdiff_t", "time_t", "string",
    "wstring", "string_view", "vector", "array", "list", "deque", "map",
    "unordered_map", "set", "unordered_set", "pair", "tuple", "queue",
    "stack", "chrono", "tm",
}
_QUALIFIERS = {"const", "volatile", "static", "constexpr", "mutable", "extern", "inline"}
_TYPE_QUALIFIERS = _QUALIFIERS | {"signed", "unsigned", "short", "long"}
_DECLARATION_BOUNDARIES = {";", "{", "}", ":"}


def tokenize(source: str) -> list[Token]:
    """Tokenize C++ while omitting comments and preprocessor directive bodies."""
    tokens: list[Token] = []
    index = 0
    line = 1
    column = 1
    length = len(source)

    def advance(end: int) -> None:
        nonlocal index, line, column
        consumed = source[index:end]
        newline_count = consumed.count("\n")
        if newline_count:
            line += newline_count
            column = len(consumed.rsplit("\n", 1)[-1]) + 1
        else:
            column += len(consumed)
        index = end

    while index < length:
        char = source[index]
        if char.isspace():
            advance(index + 1)
            continue

        if char == "#" and (index == 0 or source[source.rfind("\n", 0, index) + 1:index].strip() == ""):
            newline = source.find("\n", index)
            advance(length if newline < 0 else newline)
            continue

        if source.startswith("//", index):
            newline = source.find("\n", index)
            advance(length if newline < 0 else newline)
            continue
        if source.startswith("/*", index):
            comment_end = source.find("*/", index + 2)
            advance(length if comment_end < 0 else comment_end + 2)
            continue

        raw_match = _RAW_STRING_RE.match(source, index)
        if raw_match:
            terminator = ")" + raw_match.group(1) + '"'
            close = source.find(terminator, raw_match.end())
            end = length if close < 0 else close + len(terminator)
            tokens.append(Token("string", source[index:end], line, column, index, end))
            advance(end)
            continue

        prefix_match = re.match(r"(?:u8|u|U|L)?(?=[\"'])", source[index:])
        if char in "\"'" or prefix_match:
            quote_index = index if char in "\"'" else index + len(prefix_match.group(0))
            quote = source[quote_index]
            cursor = quote_index + 1
            escaped = False
            while cursor < length:
                current = source[cursor]
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == quote:
                    cursor += 1
                    break
                cursor += 1
            kind = "string" if quote == '"' else "char"
            tokens.append(Token(kind, source[index:cursor], line, column, index, cursor))
            advance(cursor)
            continue

        identifier_match = _IDENTIFIER_RE.match(source, index)
        if identifier_match:
            end = identifier_match.end()
            tokens.append(Token("identifier", identifier_match.group(), line, column, index, end))
            advance(end)
            continue

        number_match = _NUMBER_RE.match(source, index)
        if number_match:
            end = number_match.end()
            tokens.append(Token("number", number_match.group(), line, column, index, end))
            advance(end)
            continue

        punctuation = next((item for item in _MULTI_PUNCTUATION if source.startswith(item, index)), char)
        end = index + len(punctuation)
        tokens.append(Token("punctuation", punctuation, line, column, index, end))
        advance(end)

    return tokens


def classify_type(type_text: str) -> str:
    """Map common built-in and standard-library types to broad semantic families."""
    normalized = re.sub(r"\b(const|volatile|static|constexpr|mutable|extern|inline)\b", "", type_text)
    normalized = re.sub(r"\s+", "", normalized).lower()
    if normalized == "auto":
        return "AUTO"
    if "*" in normalized:
        if "char*" in normalized or "wchar_t*" in normalized:
            return "TEXT"
        return "POINTER"
    if any(word in normalized for word in ("vector<", "array<", "list<", "deque<", "map<", "set<", "queue<", "stack<", "[")):
        if "char[" in normalized or "wchar_t[" in normalized:
            return "TEXT"
        return "COLLECTION"
    if "string" in normalized or "char[" in normalized or "wchar_t[" in normalized:
        return "TEXT"
    if normalized in {"bool"}:
        return "BOOL"
    if normalized in {"char", "wchar_t", "char8_t", "char16_t", "char32_t"}:
        return "CHAR"
    if normalized in {"float", "double", "longdouble"}:
        return "FLOAT"
    if normalized in {
        "short", "shortint", "unsignedshort", "unsignedshortint", "signedshort",
        "signedshortint", "int", "unsigned", "unsignedint", "signed", "signedint",
        "long", "longint", "unsignedlong", "unsignedlongint", "signedlong",
        "signedlongint", "longlong", "longlongint", "unsignedlonglong",
        "unsignedlonglongint", "signedlonglong", "signedlonglongint", "size_t",
        "ssize_t", "ptrdiff_t", "int8_t", "int16_t", "int32_t", "int64_t",
        "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    }:
        return "INTEGER"
    if normalized in {"time_t", "tm", "std::chrono::seconds", "std::chrono::system_clock::time_point"}:
        return "TIME"
    if normalized in _TYPE_WORDS or "::" in normalized or "<" in normalized:
        return "USER"
    return "UNKNOWN"


def _matching_angle(tokens: list[Token], start: int, end: int) -> int | None:
    depth = 0
    for index in range(start, end):
        if tokens[index].text == "<":
            depth += 1
        elif tokens[index].text == ">":
            depth -= 1
            if depth == 0:
                return index
    return None


def _initializer(tokens: list[Token], start: int, end: int) -> tuple[str, str | None, tuple[int, int] | None, str | None, str | None, int]:
    if start >= end or tokens[start].text not in {"=", "{", "("}:
        return "NONE", None, None, None, None, start

    init_start = start + 1 if tokens[start].text == "=" else start
    opening = tokens[start].text if tokens[start].text in {"{", "("} else None
    closing = {"{": "}", "(": ")"}.get(opening)
    cursor = init_start
    nesting: list[str] = []
    while cursor < end:
        text = tokens[cursor].text
        if not nesting and text in {",", ";", ")"}:
            break
        if text in {"(", "{", "["}:
            nesting.append({"(": ")", "{": "}", "[": "]"}[text])
        elif nesting and text == nesting[-1]:
            nesting.pop()
            if not nesting and closing == text:
                cursor += 1
                break
        cursor += 1

    value_tokens = tokens[init_start:cursor]
    if not value_tokens:
        return "NONE", None, None, None, None, cursor
    init_text = "".join(token.text for token in value_tokens)
    init_span = (value_tokens[0].start, value_tokens[-1].end)
    literal_value = None
    callee = None

    if len(value_tokens) == 1 and value_tokens[0].kind == "string":
        kind = "LITERAL_STR"
        literal_value = value_tokens[0].text
    elif len(value_tokens) == 1 and value_tokens[0].kind == "char":
        kind = "LITERAL_CHAR"
        literal_value = value_tokens[0].text
    elif len(value_tokens) == 1 and value_tokens[0].text in {"true", "false"}:
        kind = "LITERAL_BOOL"
        literal_value = value_tokens[0].text
    elif len(value_tokens) == 1 and value_tokens[0].kind == "number":
        literal_value = value_tokens[0].text
        kind = "LITERAL_FLOAT" if any(char in literal_value.lower() for char in ".ef") else "LITERAL_INT"
    elif len(value_tokens) == 1 and value_tokens[0].kind == "identifier":
        kind = "IDENT"
    elif len(value_tokens) > 1 and value_tokens[0].kind == "identifier" and value_tokens[1].text == "(":
        kind = "CALL"
        callee = value_tokens[0].text
    elif opening == "{":
        kind = "BRACED"
    else:
        kind = "EXPR"
    return kind, init_text, init_span, literal_value, callee, cursor


def _block_scope(tokens: list[Token], opening_brace: int) -> str:
    previous = tokens[opening_brace - 1].text if opening_brace else ""
    if previous == ")":
        depth = 0
        open_paren = None
        for index in range(opening_brace - 1, -1, -1):
            if tokens[index].text == ")":
                depth += 1
            elif tokens[index].text == "(":
                depth -= 1
                if depth == 0:
                    open_paren = index
                    break
        before_paren = tokens[open_paren - 1].text if open_paren else ""
        if before_paren == "for":
            return "loop"
        if before_paren in {"if", "while", "switch", "catch"}:
            return "block"
        if before_paren.isidentifier():
            return "function"
    if opening_brace >= 2 and tokens[opening_brace - 2].text in {"class", "struct", "union"}:
        return "member"
    if previous == "namespace":
        return "namespace"
    return "block"


def _scope_for_tokens(tokens: list[Token]) -> list[str]:
    scopes: list[str] = []
    stack: list[str] = []
    for index, token in enumerate(tokens):
        if token.text == "}":
            if stack:
                stack.pop()
        scopes.append(stack[-1] if stack else "global")
        if token.text == "{":
            stack.append(_block_scope(tokens, index))
    return scopes


def extract_declarations(source: str, tokens: list[Token] | None = None) -> list[Declaration]:
    """Extract straightforward variable declarations; uncertain syntax is skipped."""
    token_list = tokenize(source) if tokens is None else tokens
    scopes = _scope_for_tokens(token_list)
    declarations: list[Declaration] = []
    seen_positions: set[int] = set()

    for index, token in enumerate(token_list):
        if token.kind != "identifier" or token.text in _QUALIFIERS:
            continue

        start = index
        cursor = start
        prefix: list[str] = []
        while cursor < len(token_list) and token_list[cursor].text in _TYPE_QUALIFIERS:
            prefix.append(token_list[cursor].text)
            cursor += 1

        type_start_index = cursor
        if cursor >= len(token_list) or token_list[cursor].kind != "identifier":
            continue

        type_parts = [token_list[cursor].text]
        cursor += 1
        while cursor + 1 < len(token_list) and token_list[cursor].text == "::" and token_list[cursor + 1].kind == "identifier":
            type_parts.extend(("::", token_list[cursor + 1].text))
            cursor += 2
        if cursor < len(token_list) and token_list[cursor].text == "<":
            angle_end = _matching_angle(token_list, cursor, len(token_list))
            if angle_end is None:
                continue
            type_parts.extend(item.text for item in token_list[cursor:angle_end + 1])
            cursor = angle_end + 1

        while cursor < len(token_list) and token_list[cursor].text in {"*", "&", "&&"}:
            type_parts.append(token_list[cursor].text)
            cursor += 1
        if cursor >= len(token_list) or token_list[cursor].kind != "identifier":
            continue

        name_token = token_list[cursor]
        after_name = cursor + 1
        if after_name < len(token_list) and token_list[after_name].text == "(":
            # Function declarations/definitions and function-style initialization are ambiguous.
            continue
        if after_name < len(token_list) and token_list[after_name].text not in {"=", "{", "[", ",", ";", ")", ":"}:
            continue

        # Require a statement boundary or a known built-in/qualified type at this position.
        previous = token_list[start - 1].text if start else ""
        qualified_type = "::" in type_parts or "<" in type_parts
        builtin_type = any(part in _TYPE_WORDS for part in type_parts)
        if previous not in _DECLARATION_BOUNDARIES and previous != "(" and not builtin_type and not qualified_type:
            continue
        if start in seen_positions:
            continue

        statement_start = start
        while statement_start > 0 and token_list[statement_start - 1].text not in _DECLARATION_BOUNDARIES | {"("}:
            statement_start -= 1

        statement_end = after_name
        nesting: list[str] = []
        while statement_end < len(token_list):
            text = token_list[statement_end].text
            if not nesting and text in {",", ";", ")"}:
                break
            if text in {"(", "{", "["}:
                nesting.append({"(": ")", "{": "}", "[": "]"}[text])
            elif nesting and text == nesting[-1]:
                nesting.pop()
            statement_end += 1

        init_kind, init_text, init_span, literal_value, callee, after_init = _initializer(
            token_list, after_name, len(token_list)
        )
        if init_kind == "NONE":
            after_init = after_name
        boundary = after_init < len(token_list) and token_list[after_init].text in {",", ";", ")", "["}
        if after_name < len(token_list) and token_list[after_name].text == "[":
            boundary = True
        if not boundary:
            continue

        is_array = (after_name < len(token_list) and token_list[after_name].text == "[") or "[" in type_parts
        declarator_end = after_init
        if declarator_end < len(token_list) and token_list[declarator_end].text == "[":
            array_close = next((pos for pos in range(declarator_end + 1, len(token_list)) if token_list[pos].text == "]"), None)
            if array_close is None:
                continue
            declarator_end = array_close + 1
            init_kind = "NONE"
            init_text = None
            init_span = None
        next_text = token_list[declarator_end].text if declarator_end < len(token_list) else ""
        multi_declarator = next_text == ","
        type_begin = token_list[type_start_index]
        type_end = token_list[cursor - 1]
        type_text = " ".join(prefix + type_parts)
        type_family = classify_type(type_text)
        if is_array and type_family == "CHAR":
            type_family = "TEXT"
        elif is_array and type_family != "TEXT":
            type_family = "COLLECTION"

        surrounding_scope = scopes[start]
        previous_token = token_list[start - 1].text if start else ""
        if previous_token == "(" and start >= 2 and token_list[start - 2].text == "for":
            declaration_scope = "loop"
        elif previous_token == "(" and start >= 2 and token_list[start - 2].text not in {"if", "while", "switch", "catch"}:
            declaration_scope = "param"
        elif surrounding_scope == "member":
            declaration_scope = "member"
        elif surrounding_scope == "loop":
            declaration_scope = "loop"
        elif surrounding_scope in {"function", "block"}:
            declaration_scope = "local"
        elif surrounding_scope == "namespace":
            declaration_scope = "global"
        else:
            declaration_scope = "global"

        declaration = Declaration(
            name=name_token.text,
            type_text=type_text,
            type_family=type_family,
            is_pointer="*" in type_parts,
            is_const="const" in prefix,
            is_auto="auto" in type_parts,
            is_array=is_array,
            init_kind=init_kind,
            init_text=init_text,
            literal_value=literal_value,
            callee=callee,
            scope=declaration_scope,
            line=type_begin.line,
            col=type_begin.column,
            type_span=(type_begin.start, type_end.end),
            init_span=init_span,
            stmt_span=(token_list[statement_start].start, token_list[min(declarator_end, len(token_list) - 1)].end),
            multi_declarator=multi_declarator,
        )
        declarations.append(declaration)
        seen_positions.add(start)

        # Avoid treating type/name tokens within this declaration as new declarations.
        for consumed_index in range(start + 1, declarator_end + 1):
            seen_positions.add(consumed_index)

    return declarations