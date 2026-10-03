from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import Counter, defaultdict
from typing import Any, Callable

import llm_provider as _llm
from ai_providers import get_provider_for_name
from compiler_service import compile_and_run
from secure_scan import run_security_guardrail, scan_all_security_issues


SUGGESTION_STORE: dict[str, dict[str, Any]] = {}
SUGGESTION_LOCK = threading.RLock()
_REQUEST_TTL_SECONDS = 10.0


def _stable_code_key(code: str, settings: dict[str, Any] | None = None) -> str:
    payload = {
        "code": code,
        "settings": settings or {},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _normalize_line_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _resolve_suggestion_range(code: str, suggestion: dict[str, Any]) -> tuple[int, int] | None:
    line_start = int(suggestion.get("line_start", 1))
    line_end = int(suggestion.get("line_end", line_start))
    original = str(suggestion.get("original", "")).strip()
    if apply_suggestion_to_code(code, line_start, line_end, "", original) is not None:
        return line_start, line_end
    if not original:
        return None

    source_lines = code.splitlines()
    expected_lines = [_normalize_line_text(line) for line in original.splitlines()]
    if not expected_lines:
        return None
    matches = [
        index for index in range(len(source_lines) - len(expected_lines) + 1)
        if [_normalize_line_text(line) for line in source_lines[index:index + len(expected_lines)]] == expected_lines
    ]
    if len(matches) != 1:
        return None
    resolved_start = matches[0] + 1
    return resolved_start, resolved_start + len(expected_lines) - 1


def _resolve_suggestion_scope(code: str, suggestion: dict[str, Any]) -> tuple[int, int, str] | None:
    resolved_range = _resolve_suggestion_range(code, suggestion)
    if resolved_range is None:
        return None
    line_start, line_end = resolved_range
    original = str(suggestion.get("original", "")).strip()
    replacement = str(suggestion.get("optimized", ""))

    try:
        from reasoning.syntax_tree import parse_source

        source_functions = parse_source(code).get("functions", [])
        replacement_names = {
            function.get("name")
            for function in parse_source(replacement).get("functions", [])
        }
    except Exception:
        return line_start, line_end, original

    source_lines = code.splitlines(True)
    for function in source_functions:
        function_start = int(function.get("start_line", 0))
        function_end = int(function.get("end_line", 0))
        function_name = function.get("name")
        if (
            function_name in replacement_names
            and function_start <= line_start <= line_end <= function_end
        ):
            full_function = "".join(source_lines[function_start - 1:function_end]).strip()
            if full_function:
                return function_start, function_end, full_function
    return line_start, line_end, original


def apply_suggestion_to_code(code: str, line_start: int, line_end: int, replacement: str, original: str | None = None) -> str | None:
    if not code:
        return None
    lines = code.splitlines(True)
    if line_start < 1 or line_end < line_start or line_end > len(lines):
        return None
    target = "".join(lines[line_start - 1:line_end])
    if original is not None and _normalize_line_text(target) != _normalize_line_text(original):
        return None
    new_lines = lines[: line_start - 1] + replacement.splitlines(True) + lines[line_end:]
    return "".join(new_lines)


def parse_suggestions_json(raw_response: str) -> list[dict[str, Any]]:
    if not raw_response or not isinstance(raw_response, str):
        return []

    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL | re.IGNORECASE)
        if match:
            cleaned = match.group(1).strip()

    candidates: list[str] = []
    if cleaned:
        candidates.append(cleaned)

    if "{" in cleaned or "[" in cleaned:
        for pattern in [r"\[[\s\S]*\]", r"\{[\s\S]*\}"]:
            matches = re.findall(pattern, cleaned)
            for match in matches:
                if match not in candidates:
                    candidates.append(match)

    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue

        if isinstance(data, dict):
            if "items" in data and isinstance(data["items"], list):
                items = data["items"]
            elif "suggestions" in data and isinstance(data["suggestions"], list):
                items = data["suggestions"]
            elif "choices" in data and isinstance(data["choices"], list):
                first = data["choices"][0]
                if isinstance(first, dict):
                    message = first.get("message", {})
                    if isinstance(message, dict) and "content" in message:
                        nested = parse_suggestions_json(str(message["content"]))
                        if nested:
                            return nested
                    items = []
                else:
                    items = []
            else:
                items = [data]
        elif isinstance(data, list):
            items = data
        else:
            items = []

        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                suggestion = {
                    "line_start": int(item.get("line_start", 1)),
                    "line_end": int(item.get("line_end", 1)),
                    "severity": str(item.get("severity", "info")).lower(),
                    "issue": str(item.get("issue", "")).strip(),
                    "reason": str(item.get("reason", "")).strip(),
                    "original": str(item.get("original", "")).strip(),
                    "optimized": str(item.get("optimized", "")).strip(),
                }
            except (TypeError, ValueError):
                continue
            if suggestion["severity"] == "error":
                suggestion["severity"] = "warning"
            elif suggestion["severity"] not in {"info", "warning", "improvement"}:
                suggestion["severity"] = "info"
            if not suggestion["issue"] or not suggestion["reason"]:
                continue
            signature = json.dumps({
                "issue": suggestion["issue"],
                "reason": suggestion["reason"],
                "line_start": suggestion["line_start"],
                "line_end": suggestion["line_end"],
            }, sort_keys=True)
            if signature in seen:
                continue
            seen.add(signature)
            parsed.append(suggestion)

    return parsed


def extract_cpp_code_block(response: str) -> str:
    if not response:
        return ""
    match = re.search(r"```\s*(?:cpp|c\+\+)?\s*\n?(.*?)\n?\s*```", response, re.DOTALL | re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip()


def output_equal(stdout_a: str, stdout_b: str) -> bool:
    if stdout_a is None and stdout_b is None:
        return True
    return (stdout_a or "").strip() == (stdout_b or "").strip()


def validate_optimized_program(code: str, diagnostics: Any | None = None, original_stdout: str | None = None) -> bool:
    if not isinstance(code, str) or not code.strip():
        return False
    safe, warning = run_security_guardrail(code)
    if not safe:
        return False
    status, stdout, stderr = compile_and_run(code)
    if status != 0:
        return False
    if original_stdout is not None and not output_equal(stdout, original_stdout):
        return False
    return True


def run_self_refine_loop(generate_fn: Callable[..., str], initial_code: str, max_attempts: int = 3) -> tuple[bool, str | None, int]:
    attempt = 0
    current = initial_code
    last_code = ""
    while attempt < max_attempts:
        attempt += 1
        try:
            raw = generate_fn(current)
        except Exception:
            raw = ""
        last_code = extract_cpp_code_block(raw)
        if not last_code:
            current = f"Refine this code and return a single valid C++ program in a ```cpp block.\n\n{initial_code}"
            continue
        if validate_optimized_program(last_code, ""):
            return True, last_code, attempt
        current = (
            "The previous version failed validation. Please fix the program and return only a valid "
            "C++ program in a single ```cpp block.\n\n"
            f"Previous output:\n{last_code}"
        )
    return False, last_code or None, attempt


def _report_progress(progress_callback: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]) -> None:
    if progress_callback is not None:
        try:
            progress_callback(event)
        except Exception:
            pass


def _extract_ai_suggestions(
    code: str,
    provider_name: str,
    model: str | None,
    diagnostics: list[dict[str, Any]] | None = None,
    max_items: int = 5,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    gemini_model: str | None = None,
    ollama_model: str | None = None,
) -> tuple[list[dict[str, Any]], str, str]:
    """Returns (validated_suggestions, active_provider, active_model)."""
    # Build a tight prompt: code + diagnostics + output schema reminder.
    prompt_parts = [
        "Review this C++ source as an algorithms engineer.",
        "Return a JSON object with two keys: \"analysis\" and \"suggestions\".",
        "analysis: {specification, current_complexity, alternative, alternative_complexity,"
        " tradeoffs (array of strings), recommendation (change|optional|leave_as_is)}.",
        "suggestions: array of {line_start, line_end,"
        " severity (info|warning|improvement), issue, reason, original, optimized}.",
        "Derive complexity from actual loops/data structures. Provide working code in 'optimized'.",
        "Do not invent line numbers. If no suggestions, return suggestions: [].",
        "\nCode:\n" + code,
    ]
    if diagnostics:
        prompt_parts.append("Diagnostics:\n" + json.dumps(diagnostics, ensure_ascii=False))
    prompt = "\n".join(prompt_parts)

    active_provider = ""
    active_model    = ""
    try:
        result, active_provider, active_model = _llm.generate_task(
            "live_suggestions",
            prompt,
            provider_name=provider_name,
            model=model,
            gemini_model=gemini_model,
            ollama_model=ollama_model,
            progress_callback=progress_callback,
        )
    except Exception as error:
        _report_progress(progress_callback, {"stage": "error", "message": str(error)})
        return [], "", ""

    # Result may be a parsed dict (Gemini structured output) or raw string (Ollama)
    if isinstance(result, dict):
        response_payload = result
        raw_response = json.dumps(result, ensure_ascii=False)
    else:
        raw_response = result or ""
        try:
            response_payload = json.loads(raw_response)
        except (TypeError, json.JSONDecodeError):
            response_payload = None

    _report_progress(progress_callback, {"stage": "validating", "characters": len(raw_response)})
    if isinstance(response_payload, dict) and isinstance(response_payload.get("analysis"), dict):
        _report_progress(progress_callback, {"stage": "analysis", "analysis": response_payload["analysis"]})

    suggestions = parse_suggestions_json(raw_response)
    validated: list[dict[str, Any]] = []
    source_security_counts = Counter(
        issue["pattern"] for issue in scan_all_security_issues(code)
    )
    for suggestion in suggestions[:max_items]:
        replacement = suggestion.get("optimized") or ""
        if not replacement.strip() or _normalize_line_text(replacement) == _normalize_line_text(suggestion.get("original", "")):
            continue
        resolved_scope = _resolve_suggestion_scope(code, suggestion)
        if resolved_scope is None:
            continue
        suggestion["line_start"], suggestion["line_end"], suggestion["original"] = resolved_scope
        candidate = apply_suggestion_to_code(
            code,
            suggestion["line_start"],
            suggestion["line_end"],
            replacement,
            suggestion.get("original"),
        )
        if candidate is None:
            continue
        candidate_security_counts = Counter(
            issue["pattern"] for issue in scan_all_security_issues(candidate)
        )
        if any(
            count > source_security_counts[pattern]
            for pattern, count in candidate_security_counts.items()
        ):
            continue
        validated.append(suggestion)
    _report_progress(progress_callback, {
        "stage": "complete" if validated else "no_suggestions",
        "suggestions": len(validated),
        "characters": len(raw_response),
    })
    return validated, active_provider, active_model


def _iter_live_store() -> dict[str, dict[str, Any]]:
    with SUGGESTION_LOCK:
        return dict(SUGGESTION_STORE)


def queue_live_suggestion_request(code: str, settings: dict[str, Any] | None = None) -> str:
    key = _stable_code_key(code, settings or {})
    with SUGGESTION_LOCK:
        SUGGESTION_STORE[key] = {
            "status": "pending",
            "code": code,
            "settings": settings or {},
            "updated_at": time.time(),
        }
    return key


def get_live_suggestion_result(code: str, settings: dict[str, Any] | None = None) -> dict[str, Any] | None:
    key = _stable_code_key(code, settings or {})
    with SUGGESTION_LOCK:
        return SUGGESTION_STORE.get(key)


def generate_optimal_program(
    code: str,
    diagnostics: Any | None = None,
    run_result: dict[str, Any] | None = None,
    provider_name: str = "ollama",
    model: str | None = None,
    gemini_model: str | None = None,
    ollama_model: str | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[str, str, str]:
    """Generate and verify an optimised version of *code*.

    Returns (optimized_code, change_description, verification_status).
    """
    _active_provider: list[str] = [provider_name or "ollama"]
    _active_model:    list[str] = [model or ""]

    def build_prompt(candidate: str) -> str:
        parts = [
            "You are a careful C++ optimization assistant.",
            "Return ONE complete C++ program only inside a single ```cpp block.",
            "Do not include extra prose before or after the block.",
            "Keep behavior equivalent unless there is a clear correctness issue.",
            "Infer the program's goal, prioritize algorithmic improvements before local cleanup.",
            "Preserve edge-case behavior and remove security risks.",
            "Do not make include-only or formatting-only changes.",
            "Original code:",
            code,
            "\nCurrent candidate:",
            candidate,
        ]
        if diagnostics:
            parts.append("Diagnostics: " + json.dumps(diagnostics, ensure_ascii=False))
        if run_result:
            parts.append("Runtime result: " + json.dumps(run_result, ensure_ascii=False))
        return "\n".join(parts)

    attempt_number = 0

    def generate_candidate(candidate: str) -> str:
        nonlocal attempt_number
        attempt_number += 1
        _report_progress(progress_callback, {"stage": "requesting", "attempt": attempt_number})

        def cb(event: dict[str, Any]) -> None:
            _report_progress(progress_callback, {**event, "attempt": attempt_number})

        try:
            result, ap, am = _llm.generate_task(
                "optimal_code",
                build_prompt(candidate),
                provider_name=provider_name,
                model=model,
                gemini_model=gemini_model,
                ollama_model=ollama_model,
                progress_callback=cb,
            )
            _active_provider[0] = ap
            _active_model[0]    = am
            # result is raw string (code block expected)
            return result if isinstance(result, str) else json.dumps(result)
        except Exception as error:
            _report_progress(progress_callback, {"stage": "error", "attempt": attempt_number, "message": str(error)})
            return ""

    success, optimized_code, attempts = run_self_refine_loop(generate_candidate, code, max_attempts=3)
    if success and optimized_code:
        safe, _ = run_security_guardrail(optimized_code)
        if not safe:
            return code, "AI optimization was rejected because the generated code failed the security scan.", "Rejected"
        _report_progress(progress_callback, {"stage": "complete", "attempts": attempts})
        ap = _active_provider[0]
        am = _active_model[0]
        return optimized_code, f"Verified after {attempts} pass(es) via {ap} ({am}).", "Verified"

    if not optimized_code:
        _report_progress(progress_callback, {"stage": "error", "message": "No valid C++ program was returned."})
        return code, "AI optimization timed out or did not return a valid C++ program.", "Unavailable"
    _report_progress(progress_callback, {"stage": "needs_review", "attempts": attempts})
    return optimized_code, f"Generated after {attempts} pass(es), but not fully verified.", "Needs review"


def render_live_suggestions(
    *,
    code: str,
    diagnostics: list[dict[str, Any]] | None = None,
    provider_name: str = "ollama",
    model: str | None = None,
    gemini_model: str | None = None,
    ollama_model: str | None = None,
    max_items: int = 5,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], str, str]:
    """Returns (suggestions, active_provider, active_model)."""
    if not code or not code.strip():
        _report_progress(progress_callback, {"stage": "no_code"})
        return [], "", ""
    try:
        return _extract_ai_suggestions(
            code,
            provider_name,
            model,
            diagnostics=diagnostics,
            max_items=max_items,
            progress_callback=progress_callback,
            gemini_model=gemini_model,
            ollama_model=ollama_model,
        )
    except Exception as error:
        _report_progress(progress_callback, {"stage": "error", "message": str(error)})
        return [], "", ""


__all__ = [
    "apply_suggestion_to_code",
    "extract_cpp_code_block",
    "generate_optimal_program",
    "get_live_suggestion_result",
    "output_equal",
    "parse_suggestions_json",
    "queue_live_suggestion_request",
    "render_live_suggestions",
    "run_self_refine_loop",
    "validate_optimized_program",
]
