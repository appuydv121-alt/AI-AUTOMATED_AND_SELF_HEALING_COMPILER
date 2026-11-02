#!/usr/bin/env python3
"""test_providers.py — Quick connectivity and sanity test for each LLM provider.

Calls each available provider once with a tiny prompt and prints OK / FAIL.
Run from the project root:
    python test_providers.py

Prerequisites:
    - Ensure Ollama is running with the selected models pulled.
    - pip install -r requirements.txt
"""
from __future__ import annotations

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


def main() -> None:
    print("=" * 70)
    print("  Provider connectivity test")
    print("=" * 70)
    print(f"  LIVE_MODEL   : {_llm._env('LIVE_MODEL', 'qwen2.5-coder:3b')}")
    print(f"  CODEGEN_MODEL: {_llm._env('CODEGEN_MODEL', 'qwen2.5-coder:7b')}")
    print(f"  OLLAMA_HOST  : {_llm._env('OLLAMA_HOST', 'http://localhost:11434')}")

    test_ollama()
    test_ollama_model_not_found()

    print("\n" + "=" * 70)
    print("  Done. See OK/FAIL above.")
    print("=" * 70)


if __name__ == "__main__":
    main()

