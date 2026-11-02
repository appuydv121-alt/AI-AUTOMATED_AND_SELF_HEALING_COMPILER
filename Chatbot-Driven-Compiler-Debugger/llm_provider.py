"""Task-aware local Ollama provider with SHA-256 disk and memory response caching.

Public API
----------
generate_task(task, prompt, *, schema=None, provider_name=None, model=None,
              ollama_model=None, max_tokens=512,
              progress_callback=None)
    -> (result_dict_or_str, active_provider_name, active_model_name)

active_provider_label(provider_name=None, model=None)
    -> "ollama • qwen2.5-coder:3b"   (for the UI caption)
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

# Load .env early so downstream imports see the env vars.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
except ImportError:
    pass

# ── Env config ──────────────────────────────────────────────────────────────
# Re-read env at call time (via _env()) so .env edits take effect without restart.
_ENV_LOADED = False


def _load_env_once() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
    except ImportError:
        pass


def _env(key: str, default: str = "") -> str:
    _load_env_once()
    return os.environ.get(key, default).strip()


# Model defaults — configurable via .env
_LIVE_MODEL      = _env("LIVE_MODEL",   "qwen2.5-coder:3b")
_CODEGEN_MODEL   = _env("CODEGEN_MODEL", "qwen2.5-coder:7b")
_OLLAMA_URL      = _env("OLLAMA_HOST",  "http://localhost:11434").rstrip("/")

# Per-task Ollama token limits (CPU-optimised)
_OLLAMA_MAX_TOKENS: dict[str, int] = {
    "live_suggestions": 400,
    "optimal_code":     int(_env("OLLAMA_CODEGEN_MAX_TOKENS", "4096")),
    "chat":             512,
    "reasoning":        512,
    "default":          512,
}
_OLLAMA_TIMEOUT  = 180  # seconds (allows CPU inference to finish reliably)

# ── Disk cache ───────────────────────────────────────────────────────────────
_CACHE_DIR = Path(__file__).resolve().parent / ".llm_cache"
_CACHE_DIR.mkdir(exist_ok=True)
_MEM_CACHE: dict[str, Any] = {}


def _cache_key(task: str, prompt: str, provider: str, model: str, language: str = "cpp") -> str:
    raw = json.dumps({"task": task, "prompt": prompt, "provider": provider, "model": model, "language": language},
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
    max_tokens: int | None = None,
) -> str:
    """Call Ollama with task-appropriate options. Returns raw text."""
    import requests

    ollama_url = _env("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    use_model = model or (_env("LIVE_MODEL", "qwen2.5-coder:3b")
                          if task == "live_suggestions"
                          else _env("CODEGEN_MODEL", "qwen2.5-coder:7b"))

    max_tok = max_tokens or _OLLAMA_MAX_TOKENS.get(task, _OLLAMA_MAX_TOKENS["default"])
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
            "num_ctx": int(_env("OLLAMA_NUM_CTX", "8192")),
            "num_thread": 8,
            "keep_alive": -1,
        },
    }
    if json_mode:
        payload["format"] = "json"

    task_timeouts = {
        "live_suggestions": 3,
        "chat": 45,
        "security_audit": 30,
        "efficiency_profile": 30,
        "test_generation": 30,
        "optimal_code": 60,
    }
    effective_timeout = task_timeouts.get(task, 30)

    _report(progress_callback, {"stage": "requesting"})
    try:
        response = requests.post(
            f"{ollama_url}/api/generate",
            json=payload,
            timeout=(3, effective_timeout),
            stream=stream,
        )
    except requests.exceptions.Timeout:
        raise RuntimeError(f"Ollama timed out after {effective_timeout}s")
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
    ollama_model: str | None = None,
    max_tokens: int | None = None,
    language: str = "cpp",
    progress_callback: Callable[[dict], None] | None = None,
) -> tuple[Any, str, str]:
    """Generate a task response with local Ollama, reusing an exact cached response when available.

    Returns
    -------
    (result, active_provider, active_model)
      - result is a parsed dict if the response is JSON, else a raw string.
      - active_provider is "ollama".
      - active_model is the model name actually used.

    Requests are handled locally by Ollama; no external API key is required.
    """
    selected_provider = (provider_name or "ollama").strip().lower()
    if selected_provider != "ollama":
        raise RuntimeError("Only the Ollama provider is enabled.")

    default_ollama = (
        _env("CODEGEN_MODEL", "qwen2.5-coder:7b") if task == "optimal_code"
        else _env("LIVE_MODEL", "qwen2.5-coder:3b")
    )
    eff_ollama_model = ollama_model or model or default_ollama

    ck2 = _cache_key(task, prompt, "ollama", eff_ollama_model, language=language)
    cached2 = _cache_get(ck2)
    if cached2 is not None:
        _report(progress_callback, {"stage": "cached"})
        return cached2, "ollama", eff_ollama_model

    json_mode = task in ("live_suggestions", "security_audit", "efficiency_profile", "test_generation")
    try:
        raw = _call_ollama(task, prompt, model=eff_ollama_model,
                           json_mode=json_mode,
                           progress_callback=progress_callback,
                           max_tokens=max_tokens)
        result = _try_parse_json(raw)
        _cache_put(ck2, result)
        return result, "ollama", eff_ollama_model
    except Exception as exc:
        ollama_error = str(exc)
        combined = f"Local Ollama ({eff_ollama_model}) error: {ollama_error}"
        _report(progress_callback, {"stage": "error", "message": combined})
        raise RuntimeError(combined) from exc


def active_provider_label(provider_name: str | None = None, model: str | None = None) -> str:
    """Return UI label string for local Ollama."""
    live_model = _env("LIVE_MODEL", "qwen2.5-coder:3b")
    use_model = model or live_model
    return f"ollama • {use_model}"


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
    "_LIVE_MODEL",
    "_CODEGEN_MODEL",
]
