"""Deterministic semantic naming/type checks for conservative C++ declarations."""

from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
import json
import os
import re
import time

from cpp_declarations import Declaration, extract_declarations, tokenize


@dataclass(frozen=True)
class FixPatch:
    edits: tuple[tuple[int, int, str], ...]


@dataclass
class SemanticDiagnostic:
    type: str
    severity: str
    variable: str
    declared_type: str
    expected_category: str
    expected_families: list[str]
    suggested_type: str | None
    confidence: float
    confidence_label: str
    message: str
    reason: str
    line: int
    column: int
    suggested_fix: str | None
    fix: FixPatch | None = None
    fix_blocked_reason: str | None = None
    adds_include: bool = False


@dataclass
class SemanticReport:
    diagnostics: list[SemanticDiagnostic] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    partial: bool = False


@dataclass(frozen=True)
class SemanticDictionary:
    raw: dict
    token_categories: dict[str, str]
    token_strengths: dict[str, float]
    compound_index: dict[str, dict]
    fallback: bool = False


_DEFAULT_DICTIONARY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "semantic_dictionary.json")
_MAX_SOURCE_SIZE = 500_000
_MAX_DECLARATIONS = 5_000
_NUMERIC_TEXT_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")
_INTEGER_TEXT_RE = re.compile(r"[+-]?\d+")
_FAMILY_COMPATIBILITY = {
    "TEXT": {"TEXT": 1.0, "INTEGER": 0.0, "FLOAT": 0.0, "BOOL": 0.0, "CHAR": 0.3, "COLLECTION": 0.3, "POINTER": 0.4},
    "EMAIL": {"TEXT": 1.0, "INTEGER": 0.0, "FLOAT": 0.0, "BOOL": 0.0, "CHAR": 0.0, "COLLECTION": 0.0, "POINTER": 0.4},
    "PHONE": {"TEXT": 1.0, "INTEGER": 0.1, "FLOAT": 0.0, "BOOL": 0.0, "CHAR": 0.0, "COLLECTION": 0.0, "POINTER": 0.4},
    "SECRET": {"TEXT": 1.0, "INTEGER": 0.0, "FLOAT": 0.0, "BOOL": 0.0, "CHAR": 0.0, "COLLECTION": 0.0, "POINTER": 0.4},
    "URL": {"TEXT": 1.0, "INTEGER": 0.0, "FLOAT": 0.0, "BOOL": 0.0, "CHAR": 0.0, "COLLECTION": 0.0, "POINTER": 0.4},
    "FILE": {"TEXT": 1.0, "INTEGER": 0.0, "FLOAT": 0.0, "BOOL": 0.0, "CHAR": 0.0, "COLLECTION": 0.0, "POINTER": 0.4},
    "COUNT": {"TEXT": 0.15, "INTEGER": 1.0, "FLOAT": 0.4, "BOOL": 0.0, "CHAR": 0.2, "COLLECTION": 0.1, "POINTER": 0.1},
    "ID": {"TEXT": 0.9, "INTEGER": 1.0, "FLOAT": 0.0, "BOOL": 0.0, "CHAR": 0.3, "COLLECTION": 0.1, "POINTER": 0.2},
    "DIMENSION": {"TEXT": 0.1, "INTEGER": 0.9, "FLOAT": 1.0, "BOOL": 0.0, "CHAR": 0.0, "COLLECTION": 0.1, "POINTER": 0.1},
    "PHYSICAL": {"TEXT": 0.1, "INTEGER": 0.9, "FLOAT": 1.0, "BOOL": 0.0, "CHAR": 0.0, "COLLECTION": 0.1, "POINTER": 0.1},
    "MONEY": {"TEXT": 0.2, "INTEGER": 0.6, "FLOAT": 1.0, "BOOL": 0.0, "CHAR": 0.0, "COLLECTION": 0.1, "POINTER": 0.1},
    "RATE": {"TEXT": 0.15, "INTEGER": 0.5, "FLOAT": 1.0, "BOOL": 0.0, "CHAR": 0.0, "COLLECTION": 0.1, "POINTER": 0.1},
    "DATE": {"TEXT": 0.6, "INTEGER": 0.5, "FLOAT": 0.3, "BOOL": 0.0, "CHAR": 0.0, "COLLECTION": 0.1, "POINTER": 0.1, "TIME": 1.0},
    "BOOLEAN": {"TEXT": 0.0, "INTEGER": 0.35, "FLOAT": 0.0, "BOOL": 1.0, "CHAR": 0.4, "COLLECTION": 0.0, "POINTER": 0.3},
    "STATUS": {"TEXT": 0.9, "INTEGER": 0.9, "FLOAT": 0.2, "BOOL": 0.5, "CHAR": 0.5, "COLLECTION": 0.1, "POINTER": 0.2, "USER": 1.0},
    "COLLECTION": {"TEXT": 0.1, "INTEGER": 0.0, "FLOAT": 0.0, "BOOL": 0.0, "CHAR": 0.1, "COLLECTION": 1.0, "POINTER": 0.6},
    "OBJECT": {"TEXT": 0.3, "INTEGER": 0.1, "FLOAT": 0.0, "BOOL": 0.0, "CHAR": 0.0, "COLLECTION": 0.5, "POINTER": 1.0},
}
_CATEGORY_STRENGTH = {
    "TEXT": 0.94, "EMAIL": 0.96, "PHONE": 0.92, "SECRET": 0.95, "URL": 0.94,
    "FILE": 0.88, "COUNT": 0.92, "ID": 0.85, "DIMENSION": 0.90, "PHYSICAL": 0.91,
    "MONEY": 0.94, "RATE": 0.90, "DATE": 0.90, "BOOLEAN": 0.90, "STATUS": 0.85,
    "COLLECTION": 0.88, "OBJECT": 0.72,
}
_AMBIGUOUS_NAMES = {"size", "value", "code", "id", "status", "state", "grade", "phone", "number", "index", "date", "time"}
_TYPE_SUGGESTIONS = {
    "TEXT": "std::string", "EMAIL": "std::string", "PHONE": "std::string", "SECRET": "std::string",
    "URL": "std::string", "FILE": "std::string", "COUNT": "int", "DIMENSION": "double",
    "PHYSICAL": "double", "MONEY": "double", "RATE": "double", "DATE": "std::string",
    "BOOLEAN": "bool",
}
_FALLBACK_CATEGORIES = {
    "TEXT": {"family": "TEXT", "description": "textual data", "names": ["name", "email", "phone", "address", "message", "title", "username", "password", "url"]},
    "COUNT": {"family": "INTEGER", "description": "a whole-number count", "names": ["age", "count", "number", "quantity", "index", "attempt", "retry", "year", "month", "day"]},
    "MONEY": {"family": "FLOAT", "description": "a financial value", "names": ["price", "cost", "amount", "salary", "balance", "tax"]},
    "RATE": {"family": "FLOAT", "description": "a ratio or average", "names": ["average", "avg", "ratio", "rate", "percent", "score"]},
    "BOOLEAN": {"family": "BOOL", "description": "a boolean state", "names": ["active", "enabled", "valid", "exists", "success"]},
}
_REASONS = {
    "TEXT": "This name commonly represents textual data.",
    "EMAIL": "Email addresses are textual identifiers and may contain non-numeric characters.",
    "PHONE": "Phone numbers are commonly stored as text to preserve leading zeros and symbols.",
    "SECRET": "Secrets and credentials are textual values, even when they contain only digits.",
    "URL": "URLs and network addresses are textual values.",
    "FILE": "File names and paths are textual values.",
    "COUNT": "This name commonly represents a whole-number count or quantity.",
    "DIMENSION": "This name commonly represents a numeric measurement.",
    "PHYSICAL": "This name commonly represents a physical measurement.",
    "MONEY": "Financial quantities commonly require a numeric type that can represent fractional values.",
    "RATE": "Rates and averages commonly require a numeric type that can represent fractions.",
    "DATE": "This name commonly represents a date or time value.",
    "BOOLEAN": "This name reads like a boolean state or predicate.",
    "COLLECTION": "This name commonly represents a collection of values.",
}


@lru_cache(maxsize=1)
def load_dictionary(path: str | None = None) -> SemanticDictionary:
    dictionary_path = os.path.abspath(path or _DEFAULT_DICTIONARY_PATH)
    fallback = False
    try:
        with open(dictionary_path, "r", encoding="utf-8") as dictionary_file:
            raw = json.load(dictionary_file)
        if not isinstance(raw.get("categories"), dict):
            raise ValueError("dictionary has no categories object")
    except (OSError, ValueError, json.JSONDecodeError):
        raw = {
            "version": 1,
            "categories": _FALLBACK_CATEGORIES,
            "compounds": {},
            "overrides": {},
            "bool_prefixes": ["is", "has", "can", "should"],
            "count_markers": ["count", "num", "number", "qty"],
            "modifiers": {"average": "RATE", "avg": "RATE", "ratio": "RATE"},
            "abbreviations": {},
            "ignore_names": ["i", "j", "k", "n", "m", "x", "y", "z"],
        }
        fallback = True
    token_categories: dict[str, str] = {}
    token_strengths: dict[str, float] = {}
    for category, data in raw["categories"].items():
        for name in data.get("names", []):
            token_categories.setdefault(name, category)
            token_strengths[name] = max(token_strengths.get(name, 0.0), _CATEGORY_STRENGTH.get(category, 0.8))
    compound_index = {key.lower(): value for key, value in raw.get("compounds", {}).items()}
    return SemanticDictionary(raw, token_categories, token_strengths, compound_index, fallback)


@lru_cache(maxsize=4096)
def normalize(name: str, abbreviations: tuple[tuple[str, str], ...] = ()) -> tuple[str, ...]:
    value = name.strip("_")
    value = re.sub(r"^(?:m_|s_|g_)", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^k(?=[A-Z])", "", value)
    value = re.sub(r"_$", "", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    value = re.sub(r"([A-Za-z])([0-9]+)$", r"\1", value)
    tokens = [part.lower() for part in re.split(r"[_\-\s]+", value) if part]
    abbreviation_map = dict(abbreviations)
    return tuple(abbreviation_map.get(token, token) for token in tokens)


def resolve_category(name: str, dictionary: SemanticDictionary | None = None) -> tuple[str | None, float, bool, str | None]:
    data = dictionary or load_dictionary()
    normalized_tokens = normalize(name, tuple(sorted(data.raw.get("abbreviations", {}).items())))
    if not normalized_tokens:
        return None, 0.0, False, None
    joined = "".join(normalized_tokens)
    if name.lower() in set(data.raw.get("ignore_names", [])) or joined in set(data.raw.get("ignore_names", [])):
        return None, 0.0, False, "ignored"

    compound = data.compound_index.get(joined)
    if compound:
        return compound["category"], compound.get("strength", 0.9), compound.get("ambiguous", False), "compound"

    overrides = data.raw.get("overrides", {})
    head = normalized_tokens[-1]
    override = overrides.get(head)
    if override:
        categories = override.get("categories", [])
        # The head token is the strongest signal; choose its best fit after type compatibility.
        return categories[0], override.get("strength", 0.4), True, "ambiguous-token"

    prefixes = set(data.raw.get("bool_prefixes", []))
    if len(normalized_tokens) > 1 and normalized_tokens[0] in prefixes:
        return "BOOLEAN", 0.97, False, "boolean-prefix"

    markers = set(data.raw.get("count_markers", []))
    if any(token in markers for token in normalized_tokens) and (head in markers or normalized_tokens[0] in markers):
        return "COUNT", 0.91, False, "count-marker"

    modifiers = data.raw.get("modifiers", {})
    if normalized_tokens[0] in modifiers:
        return modifiers[normalized_tokens[0]], 0.86, False, "numeric-modifier"

    if head in data.token_categories:
        return data.token_categories[head], data.token_strengths.get(head, 0.8), head in _AMBIGUOUS_NAMES, "head-token"

    candidates = [token for token in normalized_tokens if token in data.token_categories]
    if candidates:
        strongest = max(candidates, key=lambda token: data.token_strengths.get(token, 0.0))
        return data.token_categories[strongest], data.token_strengths[strongest] * 0.7, strongest in _AMBIGUOUS_NAMES, "token-fallback"
    return None, 0.0, False, None


def _compatibility(category: str, family: str, declaration: Declaration) -> float:
    score = _FAMILY_COMPATIBILITY.get(category, {}).get(family, 0.0)
    if category == "DATE" and family == "TEXT":
        return 1.0
    if category == "BOOLEAN" and family == "INTEGER" and declaration.literal_value in {"0", "1"}:
        return 0.2
    if category == "BOOLEAN" and family == "BOOL" and declaration.init_kind == "LITERAL_INT" and declaration.literal_value not in {"0", "1"}:
        return 0.0
    if category in {"COUNT", "MONEY", "RATE", "DIMENSION", "PHYSICAL"} and family == "TEXT":
        if declaration.init_kind == "LITERAL_STR" and declaration.literal_value:
            body = declaration.literal_value[1:-1]
            if _NUMERIC_TEXT_RE.fullmatch(body):
                return score
    return score


def _confidence_label(confidence: float) -> str | None:
    if confidence >= 0.75:
        return "HIGH"
    if confidence >= 0.50:
        return "MEDIUM"
    if confidence >= 0.30:
        return "LOW"
    return None


def _usage_conflicts(declaration: Declaration, usage_positions: dict[str, list[int]]) -> bool:
    return any(position > declaration.stmt_span[1] for position in usage_positions.get(declaration.name, ()))


@lru_cache(maxsize=128)
def _suggested_type(category: str, source: str) -> str | None:
    suggestion = _TYPE_SUGGESTIONS.get(category)
    if suggestion == "std::string" and re.search(r"\busing\s+namespace\s+std\s*;", source) and re.search(r"\bstring\b", source):
        return "string"
    return suggestion


@lru_cache(maxsize=128)
def _string_include_info(source: str) -> tuple[bool, tuple[int, str] | None]:
    if re.search(r"#\s*include\s*<string>", source):
        return True, None
    newline = "\r\n" if "\r\n" in source else "\n"
    include_matches = list(re.finditer(
        r"(?m)^[\t ]*#\s*include\s*<[^>]+>[\t ]*(?:\r?\n|$)", source
    ))
    insertion = include_matches[-1].end() if include_matches else 0
    return False, (insertion, f"#include <string>{newline}")


def _make_patch(
    source: str,
    declaration: Declaration,
    suggested_type: str,
    label: str,
    usage_positions: dict[str, list[int]],
) -> tuple[FixPatch | None, str | None, bool]:
    if label != "HIGH":
        return None, "confidence below the automatic-fix threshold", False
    if declaration.multi_declarator:
        return None, "multi-declarator statement", False
    if declaration.scope != "local" or declaration.is_auto or declaration.is_pointer or declaration.is_array:
        return None, "declaration scope or type is not safe to change automatically", False
    if declaration.type_family in {"COLLECTION", "POINTER", "USER", "UNKNOWN", "AUTO", "CHAR", "TIME"}:
        return None, "non-primitive or user-defined type", False
    if "&" in declaration.type_text or re.search(r"\b(unsigned|signed|short|long)\b", declaration.type_text):
        return None, "reference or width-qualified type", False
    if declaration.init_kind not in {"NONE", "LITERAL_INT", "LITERAL_FLOAT", "LITERAL_BOOL", "LITERAL_STR"}:
        return None, "initializer type is not proven", False
    if suggested_type == "std::string" and declaration.type_family not in {"TEXT", "INTEGER", "FLOAT"}:
        return None, "initializer cannot be converted losslessly to text", False
    if suggested_type == "bool" and (
        declaration.type_family != "INTEGER"
        or declaration.init_kind != "LITERAL_INT"
        or declaration.literal_value not in {"0", "1"}
    ):
        return None, "boolean conversion requires an integer literal of zero or one", False
    if suggested_type in {"int", "double"} and declaration.type_family not in {"INTEGER", "FLOAT"}:
        return None, "numeric conversion is not proven", False
    if _usage_conflicts(declaration, usage_positions):
        return None, "later uses may depend on the declared type", False

    edits: list[tuple[int, int, str]] = [(declaration.type_span[0], declaration.type_span[1], suggested_type)]
    if suggested_type == "std::string" and declaration.init_kind in {"LITERAL_INT", "LITERAL_FLOAT"} and declaration.init_span:
        literal = declaration.literal_value or ""
        edits.append((declaration.init_span[0], declaration.init_span[1], f'"{literal}"'))
    elif suggested_type == "bool" and declaration.init_kind == "LITERAL_INT" and declaration.literal_value in {"0", "1"} and declaration.init_span:
        edits.append((declaration.init_span[0], declaration.init_span[1], "true" if declaration.literal_value == "1" else "false"))
    elif suggested_type == "int" and declaration.init_kind == "LITERAL_STR" and declaration.literal_value:
        body = declaration.literal_value[1:-1]
        if not _INTEGER_TEXT_RE.fullmatch(body) or declaration.init_span is None:
            return None, "string initializer is not a lossless integer literal", False
        edits.append((declaration.init_span[0], declaration.init_span[1], body))

    adds_include = False
    if suggested_type == "std::string":
        has_string_include, include_edit = _string_include_info(source)
        if not has_string_include and include_edit:
            edits.append((include_edit[0], include_edit[0], include_edit[1]))
            adds_include = True
    edits.sort(key=lambda edit: edit[0])
    if any(edits[index][1] > edits[index + 1][0] for index in range(len(edits) - 1)):
        return None, "planned edits overlap", False
    return FixPatch(tuple(edits)), None, adds_include


def analyze_semantics(
    source: str,
    *,
    min_confidence: float = 0.30,
    dictionary: SemanticDictionary | None = None,
) -> SemanticReport:
    started = time.perf_counter()
    if not source or not source.strip():
        return SemanticReport(stats={"variables_seen": 0, "matched": 0, "skipped": 0, "elapsed_ms": 0.0})
    if len(source) > _MAX_SOURCE_SIZE:
        return SemanticReport(stats={"variables_seen": 0, "matched": 0, "skipped": 0, "elapsed_ms": 0.0, "error": "source exceeds the analysis size limit"}, partial=True)

    try:
        data = dictionary or load_dictionary()
        tokens = tokenize(source)
        declarations = extract_declarations(source, tokens=tokens)
    except Exception as error:
        return SemanticReport(stats={"variables_seen": 0, "matched": 0, "skipped": 0, "elapsed_ms": 0.0, "error": str(error)}, partial=True)

    usage_positions: dict[str, list[int]] = {}
    for token in tokens:
        if token.kind == "identifier":
            usage_positions.setdefault(token.text, []).append(token.start)

    report = SemanticReport()
    report.partial = len(declarations) >= _MAX_DECLARATIONS
    skipped = 0
    seen: set[tuple[str, int, int]] = set()
    for declaration in declarations[:_MAX_DECLARATIONS]:
        if declaration.type_family in {"USER", "UNKNOWN"}:
            skipped += 1
            continue
        category, strength, ambiguous, _rule = resolve_category(declaration.name, data)
        if not category:
            skipped += 1
            continue
        family = declaration.type_family
        if family == "AUTO":
            family = {"LITERAL_STR": "TEXT", "LITERAL_CHAR": "CHAR", "LITERAL_BOOL": "BOOL", "LITERAL_INT": "INTEGER", "LITERAL_FLOAT": "FLOAT"}.get(declaration.init_kind, "UNKNOWN")
            if family == "UNKNOWN":
                skipped += 1
                continue
        if family in {"USER", "UNKNOWN"}:
            skipped += 1
            continue
        compatibility = _compatibility(category, family, declaration)
        if compatibility >= 0.85:
            continue

        context = 1.0
        if declaration.init_kind in {"LITERAL_INT", "LITERAL_FLOAT", "LITERAL_BOOL", "LITERAL_STR"}:
            context *= 1.1
        if category in {"COUNT", "MONEY"} and declaration.init_kind == "LITERAL_STR" and declaration.literal_value:
            if _NUMERIC_TEXT_RE.fullmatch(declaration.literal_value[1:-1]):
                context *= 0.75
        elif declaration.init_kind == "NONE":
            context *= 0.85
        elif declaration.init_kind == "CALL":
            context *= 0.6
        if declaration.scope != "local":
            context *= 0.9
        if ambiguous:
            context *= 0.75
        confidence = min(0.99, max(0.0, strength * (1.0 - compatibility) * context))
        if ambiguous:
            confidence = min(confidence, 0.45)
        label = _confidence_label(confidence)
        if label is None or confidence < min_confidence:
            skipped += 1
            continue

        identity = (declaration.name, declaration.line, declaration.col)
        if identity in seen:
            continue
        seen.add(identity)
        suggestion = _suggested_type(category, source)
        patch, blocked_reason, adds_include = (None, "no unambiguous suggested type", False)
        if suggestion:
            patch, blocked_reason, adds_include = _make_patch(
                source, declaration, suggestion, label, usage_positions
            )
        category_description = data.raw["categories"].get(category, {}).get("description", category.lower())
        severity = "warning" if label in {"HIGH", "MEDIUM"} else "suggestion"
        message = (
            f"Variable '{declaration.name}' is declared as '{declaration.type_text}', "
            f"but its name strongly suggests {category_description}."
            if label == "HIGH" else
            f"Variable '{declaration.name}' is declared as '{declaration.type_text}', "
            f"which may not match what this name commonly represents. This may be intentional."
        )
        suggested_fix = f"Consider declaring `{declaration.name}` as `{suggestion}`." if suggestion else None
        diagnostic = SemanticDiagnostic(
            type="semantic_type_mismatch",
            severity=severity,
            variable=declaration.name,
            declared_type=declaration.type_text,
            expected_category=category,
            expected_families=[data.raw["categories"][category]["family"]],
            suggested_type=suggestion,
            confidence=round(confidence, 3),
            confidence_label=label,
            message=message,
            reason=_REASONS.get(category, "The variable name suggests a different kind of value."),
            line=declaration.line,
            column=declaration.col,
            suggested_fix=suggested_fix,
            fix=patch,
            fix_blocked_reason=blocked_reason,
            adds_include=adds_include,
        )
        report.diagnostics.append(diagnostic)

    report.diagnostics.sort(key=lambda item: (item.line, item.column))
    report.stats = {
        "variables_seen": len(declarations),
        "matched": len(report.diagnostics),
        "skipped": skipped,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "source_hash": hashlib.sha1(source.encode("utf-8")).hexdigest(),
    }
    if data.fallback:
        report.stats["dictionary_warning"] = "The semantic dictionary was unavailable; a small built-in dictionary was used."
    return report


def apply_safe_fixes(
    source: str,
    report: SemanticReport,
    *,
    only_high: bool = True,
) -> tuple[str, list[SemanticDiagnostic]]:
    edits = [
        edit
        for diagnostic in report.diagnostics
        if diagnostic.fix and (not only_high or diagnostic.confidence_label == "HIGH")
        for edit in diagnostic.fix.edits
    ]
    edits = sorted(set(edits), key=lambda edit: edit[0], reverse=True)
    for index in range(len(edits) - 1):
        if edits[index][0] < edits[index + 1][1]:
            return source, []
    corrected = source
    for start, end, replacement in edits:
        corrected = corrected[:start] + replacement + corrected[end:]
    applied = [diagnostic for diagnostic in report.diagnostics if diagnostic.fix and (not only_high or diagnostic.confidence_label == "HIGH")]
    return corrected, applied


def to_local_diagnostic(diagnostic: SemanticDiagnostic) -> dict:
    category = "WARNING" if diagnostic.severity == "warning" else "SUGGESTION"
    return {
        "category": category,
        "title": f"Semantic type mismatch: {diagnostic.variable}",
        "message": diagnostic.message,
        "line": diagnostic.line,
        "source": "semantic",
        "why": diagnostic.reason,
        "fix": diagnostic.suggested_fix,
        "semantic": {
            "variable": diagnostic.variable,
            "declared_type": diagnostic.declared_type,
            "expected_category": diagnostic.expected_category,
            "expected_families": diagnostic.expected_families,
            "suggested_type": diagnostic.suggested_type,
            "confidence": diagnostic.confidence,
            "confidence_label": diagnostic.confidence_label,
            "suggested_fix": diagnostic.suggested_fix,
            "fix_blocked_reason": diagnostic.fix_blocked_reason,
        },
    }