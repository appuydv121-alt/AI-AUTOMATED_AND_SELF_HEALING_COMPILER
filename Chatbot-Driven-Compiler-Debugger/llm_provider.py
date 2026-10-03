"""llm_provider.py — Task-aware LLM provider with Gemini-first, Ollama-fallback,
SHA-256 disk+memory caching, and structured output (Gemini) / json_mode (Ollama).

Public API
----------
generate_task(task, prompt, *, schema=None, provider_name=None, model=None,
              gemini_model=None, ollama_model=None, max_tokens=512,
              progress_callback=None)
    -> (result_dict_or_str, active_provider_name, active_model_name)

active_provider_label(provider_name=None, model=None)
    -> "gemini • gemini-3.1-flash-lite"   (for the UI caption)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

# Load .env early so downstream imports see the env vars.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
except ImportError:
    pass

logger = logging.getLogger(__name__)

# ── Env config ──────────────────────────────────────────────────────────────
# Re-read env at call time (via _env()) so .env edits take effect without restart.
_ENV_LOADED = False


def _load_env_once() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    except ImportError:
        pass
    _ENV_LOADED = True


def _env(key: str, default: str = "") -> str:
    _load_env_once()
    return os.environ.get(key, default).strip()


def _GEMINI_API_KEY() -> str:  # type: ignore[assignment]
    return _env("GEMINI_API_KEY")


# Model defaults — configurable via .env
# Primary fast model: gemini-3.1-flash-lite
# Higher quality option: gemini-3.5-flash
_DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
_GEMINI_MODEL    = _env("GEMINI_MODEL", _DEFAULT_GEMINI_MODEL)
_LIVE_MODEL      = _env("LIVE_MODEL",   "qwen2.5-coder:3b")
_CODEGEN_MODEL   = _env("CODEGEN_MODEL", "qwen2.5-coder:7b")
_OLLAMA_URL      = _env("OLLAMA_HOST",  "http://localhost:11434").rstrip("/")

# Per-task Ollama token limits (CPU-optimised)
_OLLAMA_MAX_TOKENS: dict[str, int] = {
    "live_suggestions": 400,
    "optimal_code":     1200,
    "chat":             1024,
    "reasoning":        512,
    "default":          512,
}
_GEMINI_MAX_TOKENS: dict[str, int] = {
    "live_suggestions": 1024,
    "optimal_code":     2048,
    "chat":             2048,
    "reasoning":        1024,
    "default":          512,
}
_GEMINI_TIMEOUT  = 15   # seconds
_OLLAMA_TIMEOUT  = 90   # seconds

# ── Disk cache ───────────────────────────────────────────────────────────────
_CACHE_DIR = Path(__file__).resolve().parent / ".llm_cache"
_CACHE_DIR.mkdir(exist_ok=True)
_MEM_CACHE: dict[str, Any] = {}


def _cache_key(task: str, prompt: str, provider: str, model: str) -> str:
    raw = json.dumps({"task": task, "prompt": prompt, "provider": provider, "model": model},
                     sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key: str) -> Any | None:
    if key in _MEM_CACHE:
        return _MEM_CACHE[key]
    path = _CACHE_DIR / f"{key}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8"))
            _MEM_CACHE[key] = data
            return data
        except Exception:
            pass
    return None


def _cache_put(key: str, value: Any) -> None:
    _MEM_CACHE[key] = value
    try:
        (_CACHE_DIR / f"{key}.json").write_text(
            json.dumps(value, ensure_ascii=False), "utf-8"
        )
    except Exception:
        pass


# ── Schemas (Gemini structured output) ──────────────────────────────────────
_SUGGESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis": {
            "type": "object",
            "properties": {
                "specification":         {"type": "string"},
                "current_complexity":    {"type": "string"},
                "alternative":           {"type": "string"},
                "alternative_complexity":{"type": "string"},
                "tradeoffs":             {"type": "array", "items": {"type": "string"}},
                "recommendation":        {"type": "string"},
            },
            "required": ["specification", "current_complexity", "alternative",
                         "alternative_complexity", "tradeoffs", "recommendation"],
        },
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line_start": {"type": "integer"},
                    "line_end":   {"type": "integer"},
                    "severity":   {"type": "string", "enum": ["info", "warning", "improvement"]},
                    "issue":      {"type": "string"},
                    "reason":     {"type": "string"},
                    "original":   {"type": "string"},
                    "optimized":  {"type": "string"},
                },
                "required": ["line_start", "line_end", "severity", "issue",
                             "reason", "original", "optimized"],
            },
        },
    },
    "required": ["analysis", "suggestions"],
}

# ── Gemini provider (google-genai SDK) ───────────────────────────────────────
def _gemini_available() -> bool:
    return bool(_GEMINI_API_KEY())


def _sanitize_gemini_error(exc: Exception) -> str:
    raw_msg = str(exc)
    key = _GEMINI_API_KEY()
    if key and key in raw_msg:
        raw_msg = raw_msg.replace(key, "[KEY]")
    return raw_msg.strip()


def _is_ollama_model_name(name: str) -> bool:
    if not name:
        return False
    lower = name.lower()
    return any(p in lower for p in ("qwen", "llama", "mistral", "codellama", "deepseek", "phi"))


def _call_gemini(
    task: str,
    prompt: str,
    *,
    model: str | None = None,
    schema: dict | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> str:
    """Call Gemini via google-genai SDK. Returns raw text (JSON string when schema set)."""
    api_key = _GEMINI_API_KEY()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in .env")

    gemini_model = model or _env("GEMINI_MODEL", _DEFAULT_GEMINI_MODEL)
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("google-genai not installed") from exc

    client = genai.Client(api_key=api_key)
    max_tok = _GEMINI_MAX_TOKENS.get(task, _GEMINI_MAX_TOKENS["default"])

    gen_config_kwargs: dict[str, Any] = {
        "temperature": 0.0,
        "max_output_tokens": max_tok,
        "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
    }
    if schema is not None:
        gen_config_kwargs["response_mime_type"] = "application/json"
        gen_config_kwargs["response_schema"] = schema

    _report(progress_callback, {"stage": "requesting"})
    t0 = time.monotonic()

    result_holder: dict[str, Any] = {}
    exc_holder: dict[str, Exception] = {}

    def _do_generate():
        try:
            resp = client.models.generate_content(
                model=gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(**gen_config_kwargs),
            )
            result_holder["text"] = resp.text or ""
        except Exception as exc:
            exc_holder["exc"] = exc

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_do_generate)
        fut.result(timeout=_GEMINI_TIMEOUT)

    if exc_holder:
        raise exc_holder["exc"]

    text = result_holder.get("text", "")
    elapsed = time.monotonic() - t0
    char_count = len(text)
    _report(progress_callback, {
        "stage": "generating",
        "characters": char_count,
        "text": text,
    })
    _report(progress_callback, {
        "stage": "done",
        "characters": char_count,
        "elapsed": elapsed,
    })
    return text


# ── Ollama helpers ───────────────────────────────────────────────────────────
def _ollama_model_available(model_name: str) -> tuple[bool, str]:
    """Return (available, error_message).

    Calls GET /api/tags. Returns (False, 'Model X not found. Run: ollama pull X')
    when the model is missing. Never raises.
    """
    import requests

    url = _env("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    try:
        resp = requests.get(f"{url}/api/tags", timeout=(2, 5))
        resp.raise_for_status()
        installed = [
            m.get("name", "") for m in resp.json().get("models", [])
            if isinstance(m, dict)
        ]
    except requests.exceptions.ConnectionError:
        return False, f"Ollama is not running at {url}"
    except Exception as exc:
        return False, f"Ollama tags check failed: {exc}"

    def _base(n: str) -> str:
        return n.split(":")[0]

    if any(m == model_name or _base(m) == _base(model_name) for m in installed):
        return True, ""
    return (
        False,
        f"Model '{model_name}' not found in Ollama."
        f" Run: ollama pull {model_name}",
    )


# ── Ollama provider (requests-based, optimised) ──────────────────────────────
def _call_ollama(
    task: str,
    prompt: str,
    *,
    model: str | None = None,
    json_mode: bool = False,
    progress_callback: Callable[[dict], None] | None = None,
) -> str:
    """Call Ollama with CPU-optimised options. Returns raw text."""
    import requests

    ollama_url = _env("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    # Model names strictly separate: never allow a Gemini model name to reach Ollama
    if model and not model.startswith("gemini"):
        use_model = model
    else:
        use_model = (_env("LIVE_MODEL", "qwen2.5-coder:3b")
                     if task == "live_suggestions"
                     else _env("CODEGEN_MODEL", "qwen2.5-coder:7b"))

    max_tok = _OLLAMA_MAX_TOKENS.get(task, _OLLAMA_MAX_TOKENS["default"])
    stream = progress_callback is not None

    # Pre-flight: verify model is available
    ok, err_msg = _ollama_model_available(use_model)
    if not ok:
        raise RuntimeError(err_msg)

    payload: dict[str, Any] = {
        "model": use_model,
        "prompt": prompt,
        "stream": stream,
        "options": {
            "temperature": 0,
            "num_predict": max_tok,
            "num_ctx": 4096,
            "num_thread": 8,
            "keep_alive": -1,
        },
    }
    if json_mode:
        payload["format"] = "json"

    _report(progress_callback, {"stage": "requesting"})
    try:
        response = requests.post(
            f"{ollama_url}/api/generate",
            json=payload,
            timeout=(3, _OLLAMA_TIMEOUT),
            stream=stream,
        )
    except requests.exceptions.Timeout:
        raise RuntimeError(f"Ollama timed out after {_OLLAMA_TIMEOUT}s")
    except requests.exceptions.ConnectionError:
        raise RuntimeError(f"Ollama is not running at {ollama_url}")

    if response.status_code == 429:
        raise RuntimeError("Ollama rate limited")
    if response.status_code == 404:
        raise RuntimeError(
            f"Model '{use_model}' not found by Ollama (HTTP 404)."
            f" Run: ollama pull {use_model}"
        )
    if response.status_code != 200:
        raise RuntimeError(f"Ollama HTTP {response.status_code}")

    if not stream:
        data = response.json()
        return str(data.get("response", "")).strip()

    output: list[str] = []
    char_count = 0
    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line:
            continue
        if isinstance(raw_line, bytes):
            raw_line = raw_line.decode("utf-8", errors="replace")
        try:
            data = json.loads(raw_line)
        except (TypeError, json.JSONDecodeError):
            continue
        fragment = str(data.get("response", ""))
        if fragment:
            output.append(fragment)
            char_count += len(fragment)
            _report(progress_callback, {
                "stage": "generating",
                "characters": char_count,
                "text": fragment,
            })
        if data.get("done"):
            _report(progress_callback, {
                "stage": "done",
                "characters": char_count,
                "tokens": data.get("eval_count"),
            })
    return "".join(output).strip()


# ── Public generate_task API ─────────────────────────────────────────────────
def generate_task(
    task: str,
    prompt: str,
    *,
    schema: dict | None = None,
    provider_name: str | None = None,
    model: str | None = None,
    gemini_model: str | None = None,
    ollama_model: str | None = None,
    max_tokens: int | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> tuple[Any, str, str]:
    """Generate a response for *task* using Gemini (primary) or Ollama (fallback).

    Returns
    -------
    (result, active_provider, active_model)
      - result is a parsed dict if the response is JSON, else a raw string.
      - active_provider is "gemini" or "ollama" (the provider that actually served).
      - active_model is the model name actually used.

    Error messages always include BOTH failure reasons when Gemini falls back and
    Ollama also fails. The Gemini API key is never logged or included in messages.
    """
    # ── Resolve Gemini model (strictly separate from Ollama) ───────────────
    default_gemini = _env("GEMINI_MODEL", _DEFAULT_GEMINI_MODEL)
    if gemini_model and not _is_ollama_model_name(gemini_model):
        eff_gemini_model = gemini_model
    elif model and model.startswith("gemini"):
        eff_gemini_model = model
    else:
        eff_gemini_model = default_gemini

    # ── Resolve Ollama model (strictly separate from Gemini) ───────────────
    default_ollama = (
        _env("LIVE_MODEL", "qwen2.5-coder:3b") if task == "live_suggestions"
        else _env("CODEGEN_MODEL", "qwen2.5-coder:7b")
    )
    if ollama_model and not ollama_model.startswith("gemini"):
        eff_ollama_model = ollama_model
    elif model and not model.startswith("gemini"):
        eff_ollama_model = model
    else:
        eff_ollama_model = default_ollama

    # ── Determine provider strategy ───────────────────────────────────────
    forced = (provider_name or "").lower().strip()
    has_gemini_key = _gemini_available()

    # Gemini is tried first unless Ollama is forced
    try_gemini_first = (forced != "ollama")

    gemini_error: str = ""

    # ── Try Gemini ────────────────────────────────────────────────────────
    if try_gemini_first:
        if not has_gemini_key:
            gemini_error = "GEMINI_API_KEY is not set in .env"
            logger.info("Gemini skipped (%s); proceeding to Ollama", gemini_error)
            _report(progress_callback, {
                "stage": "fallback",
                "message": f"Gemini ({eff_gemini_model}): {gemini_error}. Trying local Ollama ({eff_ollama_model})…",
            })
        else:
            ck = _cache_key(task, prompt, "gemini", eff_gemini_model)
            cached = _cache_get(ck)
            if cached is not None:
                _report(progress_callback, {"stage": "cached"})
                return cached, "gemini", eff_gemini_model

            use_schema = schema
            if task == "live_suggestions" and use_schema is None:
                use_schema = _SUGGESTION_SCHEMA
            try:
                raw = _call_gemini(task, prompt, model=eff_gemini_model, schema=use_schema,
                                   progress_callback=progress_callback)
                result = _try_parse_json(raw)
                _cache_put(ck, result)
                return result, "gemini", eff_gemini_model
            except Exception as exc:
                gemini_error = _sanitize_gemini_error(exc)
                logger.warning("Gemini (%s) failed (%s); falling back to Ollama (%s)",
                               eff_gemini_model, gemini_error, eff_ollama_model)
                _report(progress_callback, {
                    "stage": "fallback",
                    "message": f"Gemini ({eff_gemini_model}) failed: {gemini_error}. Trying local Ollama ({eff_ollama_model})…",
                })

    # ── Ollama fallback ───────────────────────────────────────────────────
    ck2 = _cache_key(task, prompt, "ollama", eff_ollama_model)
    cached2 = _cache_get(ck2)
    if cached2 is not None:
        _report(progress_callback, {"stage": "cached"})
        return cached2, "ollama", eff_ollama_model

    json_mode = task in ("live_suggestions",)
    try:
        raw = _call_ollama(task, prompt, model=eff_ollama_model,
                           json_mode=json_mode,
                           progress_callback=progress_callback)
        result = _try_parse_json(raw)
        _cache_put(ck2, result)
        return result, "ollama", eff_ollama_model
    except Exception as exc:
        ollama_error = str(exc)
        # Surface real Gemini reason first, then the Ollama fallback result
        if gemini_error:
            combined = (
                f"Gemini ({eff_gemini_model}) failed: {gemini_error}. "
                f"Fallback Ollama ({eff_ollama_model}) failed: {ollama_error}"
            )
        else:
            combined = f"Ollama ({eff_ollama_model}) failed: {ollama_error}"
        _report(progress_callback, {"stage": "error", "message": combined})
        raise RuntimeError(combined) from exc


def active_provider_label(provider_name: str | None = None, model: str | None = None) -> str:
    """Return the UI label string, e.g. 'gemini • gemini-3.1-flash-lite'."""
    forced = (provider_name or "").lower().strip()
    live_model = _env("LIVE_MODEL", "qwen2.5-coder:3b")
    gemini_model = _env("GEMINI_MODEL", _DEFAULT_GEMINI_MODEL)
    if forced == "ollama" or not _gemini_available():
        use_model = model or live_model
        if use_model.startswith("gemini"):
            use_model = live_model
        return f"ollama • {use_model}"
    use_gem = model if (model and model.startswith("gemini")) else gemini_model
    return f"gemini • {use_gem}"


# ── Helpers ──────────────────────────────────────────────────────────────────
def _try_parse_json(raw: str) -> Any:
    if not raw:
        return raw
    text = raw.strip()
    import re
    m = re.match(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return raw


def _report(callback: Callable[[dict], None] | None, event: dict) -> None:
    if callback is not None:
        try:
            callback(event)
        except Exception:
            pass


__all__ = [
    "generate_task",
    "active_provider_label",
    "_DEFAULT_GEMINI_MODEL",
    "_GEMINI_MODEL",
    "_LIVE_MODEL",
    "_CODEGEN_MODEL",
]

