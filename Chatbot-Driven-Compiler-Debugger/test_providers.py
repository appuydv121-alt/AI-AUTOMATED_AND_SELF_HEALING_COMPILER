#!/usr/bin/env python3
"""test_providers.py — Quick connectivity and sanity test for each LLM provider.

Calls each available provider once with a tiny prompt and prints OK / FAIL.
Run from the project root:
    python test_providers.py

Prerequisites:
    - Set GEMINI_API_KEY in .env or as an env var to test Gemini.
    - Ensure Ollama is running for Ollama tests.
    - pip install -r requirements.txt
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
except ImportError:
    pass

import llm_provider as _llm

_TINY_PROMPT = (
    "Reply with exactly one valid JSON object: "
    '{"ok": true, "msg": "hello"}. '
    "No prose, no code fences."
)

_PASS = "\033[32mOK\033[0m"
_FAIL = "\033[31mFAIL\033[0m"


def _result_line(label: str, ok: bool, detail: str) -> str:
    status = _PASS if ok else _FAIL
    return f"  [{status}]  {label:<40}  {detail}"


def test_gemini() -> None:
    print("\n-- Gemini --------------------------------------------------------")
    api_key = _llm._GEMINI_API_KEY()
    gemini_model = _llm._env("GEMINI_MODEL", "gemini-3.1-flash-lite")
    if not api_key:
        print(_result_line(f"gemini/{gemini_model}", False, "GEMINI_API_KEY not set in .env"))
        return
    try:
        result, prov, mdl = _llm.generate_task("default", _TINY_PROMPT, provider_name="gemini")
        if isinstance(result, dict) and result.get("ok") is True:
            print(_result_line(f"gemini/{gemini_model}", True, f"Responded OK via {prov}/{mdl}"))
        elif isinstance(result, str) and "ok" in result:
            print(_result_line(f"gemini/{gemini_model}", True, f"Responded (string) via {prov}/{mdl}"))
        else:
            print(_result_line(f"gemini/{gemini_model}", False, f"Unexpected result: {str(result)[:80]}"))
    except Exception as exc:
        print(_result_line(f"gemini/{gemini_model}", False, str(exc)[:120]))


def _test_ollama_model(model: str, task: str = "default") -> None:
    available, err = _llm._ollama_model_available(model)
    if not available:
        print(_result_line(f"ollama/{model}", False, err))
        return
    try:
        result, prov, mdl = _llm.generate_task(task, _TINY_PROMPT, provider_name="ollama", model=model)
        if result:
            snippet = str(result)[:60].replace("\n", " ")
            print(_result_line(f"ollama/{model}", True, f"Responded via {prov}/{mdl}: {snippet}"))
        else:
            print(_result_line(f"ollama/{model}", False, "Empty response"))
    except Exception as exc:
        print(_result_line(f"ollama/{model}", False, str(exc)[:120]))


def test_ollama() -> None:
    print("\n-- Ollama --------------------------------------------------------")
    live_model = _llm._env("LIVE_MODEL", "qwen2.5-coder:3b")
    codegen_model = _llm._env("CODEGEN_MODEL", "qwen2.5-coder:7b")

    _test_ollama_model(live_model, task="live_suggestions")
    if codegen_model != live_model:
        _test_ollama_model(codegen_model, task="optimal_code")


def test_ollama_model_not_found() -> None:
    print("\n-- Ollama missing-model detection --------------------------------")
    fake_model = "definitely-not-a-real-model:999b"
    available, err = _llm._ollama_model_available(fake_model)
    if not available and "ollama pull" in err:
        print(_result_line("missing-model detection", True, err))
    else:
        print(_result_line("missing-model detection", False,
                           f"Expected pull hint, got: available={available} err={err!r}"))


def test_fallback_combined_error() -> None:
    """If both providers fail, the error message must contain both failure reasons."""
    print("\n-- Fallback combined-error message -------------------------------")
    old_key = os.environ.get("GEMINI_API_KEY", "")
    os.environ["GEMINI_API_KEY"] = "fake-key-for-test"
    _llm._ENV_LOADED = False  # force re-read

    fake_model = "definitely-not-a-real-model:999b"
    try:
        _llm.generate_task("default", _TINY_PROMPT, provider_name="gemini", model=fake_model)
        print(_result_line("combined-error message", False, "No exception raised — expected one"))
    except RuntimeError as exc:
        msg = str(exc)
        has_gemini = "Gemini" in msg or "gemini" in msg.lower()
        has_ollama = "Ollama" in msg or "ollama" in msg.lower()
        if has_gemini and has_ollama:
            print(_result_line("combined-error message", True, msg[:100]))
        else:
            print(_result_line("combined-error message", False,
                               f"Missing Gemini or Ollama in error: {msg[:100]}"))
    finally:
        # Restore
        if old_key:
            os.environ["GEMINI_API_KEY"] = old_key
        else:
            os.environ.pop("GEMINI_API_KEY", None)
        _llm._ENV_LOADED = False


def main() -> None:
    print("=" * 70)
    print("  Provider connectivity test")
    print("=" * 70)
    print(f"  GEMINI_MODEL : {_llm._env('GEMINI_MODEL', 'gemini-3.1-flash-lite')}")
    print(f"  LIVE_MODEL   : {_llm._env('LIVE_MODEL', 'qwen2.5-coder:3b')}")
    print(f"  CODEGEN_MODEL: {_llm._env('CODEGEN_MODEL', 'qwen2.5-coder:7b')}")
    print(f"  OLLAMA_HOST  : {_llm._env('OLLAMA_HOST', 'http://localhost:11434')}")
    print(f"  GEMINI_KEY   : {'set' if _llm._GEMINI_API_KEY() else 'not set'}")

    test_gemini()
    test_ollama()
    test_ollama_model_not_found()
    test_fallback_combined_error()

    print("\n" + "=" * 70)
    print("  Done. See OK/FAIL above.")
    print("=" * 70)


if __name__ == "__main__":
    main()


