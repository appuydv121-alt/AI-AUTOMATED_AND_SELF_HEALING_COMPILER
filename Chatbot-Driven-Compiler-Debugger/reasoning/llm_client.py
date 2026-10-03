"""LLM client for the reasoning pipeline.

Routes every call through ``llm_provider.generate_task()`` so the sidebar
provider / model selection (Gemini primary, Ollama fallback) is respected.

Public API (unchanged):
    LLMClient          - abstract interface used by all pipeline stages
    get_client()       - returns the active singleton
    set_provider(...)  - called by app.py to inject sidebar settings
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# -- Provider injection (set by app.py before each run) ----------------------
_active_provider_name: str = "gemini"
_active_gemini_model: str | None = None
_active_ollama_model: str | None = None


def set_provider(
    provider_name: str,
    gemini_model: str | None = None,
    ollama_model: str | None = None,
) -> None:
    """Called by app.py (or tests) to set the sidebar provider for the next run."""
    global _active_provider_name, _active_gemini_model, _active_ollama_model
    _active_provider_name = provider_name or "gemini"
    _active_gemini_model  = gemini_model or None
    _active_ollama_model  = ollama_model or None


# -- Unified LLMClient backed by llm_provider --------------------------------

class LLMClient:
    """Provider-agnostic client wrapping ``llm_provider.generate_task()``.

    Constructor accepts the same kwargs as the old Ollama client so existing
    callers need zero changes. All positional/kwargs are silently ignored.
    """

    def __init__(self, url: str = "", model: str = "", **kwargs: Any):
        pass  # legacy signature kept for compat

    # -- availability --------------------------------------------------------

    def is_model_available(self) -> tuple[bool, str]:
        """Lightweight probe. Returns (True, '') to avoid blocking the pipeline."""
        try:
            import llm_provider as _llm
            if _active_provider_name == "gemini" and _llm._gemini_available():
                return True, ""
            if _active_provider_name == "ollama":
                ok, err = _llm._ollama_model_available(
                    _active_ollama_model or os.environ.get("CODEGEN_MODEL", "qwen2.5-coder:7b")
                )
                return ok, err
            return True, ""
        except Exception as exc:
            logger.debug("is_model_available check failed: %s", exc)
            return True, ""

    # -- generation ----------------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        timeout: int = 30,
        fallback: str = "",
    ) -> str:
        """Send a prompt and return raw text (JSON string when schema set)."""
        try:
            import llm_provider as _llm
            result, _prov, _mdl = _llm.generate_task(
                "reasoning",
                prompt,
                provider_name=_active_provider_name,
                gemini_model=_active_gemini_model,
                ollama_model=_active_ollama_model,
            )
            if isinstance(result, dict):
                return json.dumps(result, ensure_ascii=False)
            return str(result or fallback)
        except Exception as exc:
            logger.warning("LLMClient.generate failed via provider: %s", exc)
            return fallback

    def generate_json(
        self,
        prompt: str,
        schema: dict[str, Any] | None,
        *,
        timeout: int = 30,
        retries: int = 1,
    ) -> tuple[Optional[dict[str, Any]], str]:
        """Generate and parse JSON. Returns (parsed_dict_or_None, error_string)."""
        last_error = ""
        for attempt in range(1 + retries):
            raw = self.generate(prompt, schema=schema, timeout=timeout, fallback="")
            if not raw:
                last_error = "Empty LLM response"
                continue
            try:
                import re
                text = raw.strip()
                if text.startswith("```"):
                    m = re.match(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
                    if m:
                        text = m.group(1).strip()
                parsed = json.loads(text)
                return parsed, ""
            except json.JSONDecodeError as exc:
                last_error = f"Invalid JSON: {exc}"
                if attempt < retries:
                    prompt += (
                        f"\n\nYour previous response was not valid JSON: {exc}. "
                        "Please respond with valid JSON only."
                    )
        return None, last_error


# -- Module-level singleton --------------------------------------------------

_default_client: LLMClient | None = None


def get_client() -> LLMClient:
    """Return (or create) the module-level singleton client."""
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client
