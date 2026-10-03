from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable

import requests


_ENV_KEYS = {
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

_DEFAULT_MODELS = {
    "ollama": "qwen2.5-coder:7b",
    "gemini": "gemini-3.1-flash-lite",
    "groq": "llama-3.1-8b-instant",
    "openrouter": "openrouter/auto",
}


def _load_dotenv_file(path: str | None = None) -> None:
    dot_env = Path(path or Path(__file__).resolve().parent / ".env")
    if not dot_env.exists():
        return
    try:
        with dot_env.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
    except OSError:
        pass


def _read_env(name: str, default: str = "") -> str:
    _load_dotenv_file(None)
    value = os.environ.get(name)
    if value is not None:
        return value.strip()
    return default


def _http_post(url: str, *, json_payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: float | tuple[float, float] = 30) -> requests.Response:
    return requests.post(url, json=json_payload, headers=headers, timeout=timeout)


class LLMProvider:
    """Minimal interface for local and cloud LLM providers."""

    provider_name = "base"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or _DEFAULT_MODELS.get(self.provider_name, "")
        self.api_key = api_key or _read_env(_ENV_KEYS.get(self.provider_name, ""), "")

    def generate(self, prompt: str, system: str | None = None, *, json_mode: bool = False, temperature: float = 0.1, max_tokens: int = 512, timeout: int = 20, progress_callback: Callable[[dict[str, Any]], None] | None = None) -> str:
        raise NotImplementedError

    def list_models(self) -> list[str]:
        return []

    def is_available(self) -> bool:
        return False


class OllamaProvider(LLMProvider):
    provider_name = "ollama"

    def __init__(self, model: str | None = None, url: str = "http://localhost:11434"):
        self.url = url.rstrip("/")
        super().__init__(model=model or _read_env("OLLAMA_MODEL", _DEFAULT_MODELS["ollama"]))

    def list_models(self) -> list[str]:
        try:
            response = requests.get(f"{self.url}/api/tags", timeout=(2, 5))
            response.raise_for_status()
            payload = response.json()
            items = payload.get("models", []) if isinstance(payload, dict) else []
            return [item.get("name", "") for item in items if isinstance(item, dict)]
        except (requests.RequestException, ValueError):
            return []

    def is_available(self) -> bool:
        try:
            models = self.list_models()
            if not models:
                return False
            return any(model == self.model or model.split(":", 1)[0] == self.model.split(":", 1)[0] for model in models)
        except Exception:
            return False

    def generate(self, prompt: str, system: str | None = None, *, json_mode: bool = False, temperature: float = 0.1, max_tokens: int = 512, timeout: int = 20, progress_callback: Callable[[dict[str, Any]], None] | None = None) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": progress_callback is not None,
            "options": {"temperature": float(temperature), "num_predict": int(max_tokens)},
        }
        if system:
            payload["system"] = system
        if json_mode:
            payload["format"] = "json"
        try:
            response = requests.post(
                f"{self.url}/api/generate",
                json=payload,
                timeout=(3, timeout),
                stream=progress_callback is not None,
            )
            if response.status_code == 429:
                raise RuntimeError("rate limited")
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")
            if progress_callback is None:
                data = response.json()
                return str(data.get("response", "")).strip()

            output: list[str] = []
            character_count = 0
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
                    character_count += len(fragment)
                    progress_callback({
                        "stage": "generating",
                        "characters": character_count,
                        "text": fragment,
                    })
                if data.get("done"):
                    progress_callback({
                        "stage": "done",
                        "characters": character_count,
                        "tokens": data.get("eval_count"),
                    })
            return "".join(output).strip()
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc


class GeminiProvider(LLMProvider):
    provider_name = "gemini"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        super().__init__(model=model or _read_env("GEMINI_MODEL", _DEFAULT_MODELS["gemini"]), api_key=api_key or _read_env("GEMINI_API_KEY", ""))

    def is_available(self) -> bool:
        return bool(self.api_key)

    def list_models(self) -> list[str]:
        if not self.api_key:
            return []
        try:
            response = requests.get(f"{self.base_url}/models?key={self.api_key}", timeout=(3, 10))
            if response.status_code != 200:
                return []
            payload = response.json()
            models = payload.get("models", [])
            return [item.get("name", "").replace("models/", "") for item in models if isinstance(item, dict)]
        except (requests.RequestException, ValueError):
            return []

    def generate(self, prompt: str, system: str | None = None, *, json_mode: bool = False, temperature: float = 0.1, max_tokens: int = 512, timeout: int = 20, progress_callback: Callable[[dict[str, Any]], None] | None = None) -> str:
        if not self.api_key:
            raise RuntimeError("Gemini API key missing")
        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": f"{system}\n\n{prompt}" if system else prompt}]}],
            "generationConfig": {
                "temperature": float(temperature),
                "maxOutputTokens": int(max_tokens),
            },
        }
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        response = _http_post(
            f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}",
            json_payload=body,
            timeout=(3, timeout),
        )
        if response.status_code == 429:
            raise RuntimeError("rate limited")
        if response.status_code not in (200, 201):
            raise RuntimeError(f"HTTP {response.status_code}")
        payload = response.json()
        candidates = payload.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        return text.strip()


class GroqProvider(LLMProvider):
    provider_name = "groq"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        super().__init__(model=model or _read_env("GROQ_MODEL", _DEFAULT_MODELS["groq"]), api_key=api_key or _read_env("GROQ_API_KEY", ""))

    def list_models(self) -> list[str]:
        if not self.api_key:
            return []
        try:
            response = requests.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {self.api_key}"}, timeout=(3, 10))
            if response.status_code != 200:
                return []
            payload = response.json()
            data = payload.get("data", [])
            return [item.get("id", "") for item in data if isinstance(item, dict)]
        except (requests.RequestException, ValueError):
            return []

    def is_available(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str, system: str | None = None, *, json_mode: bool = False, temperature: float = 0.1, max_tokens: int = 512, timeout: int = 20, progress_callback: Callable[[dict[str, Any]], None] | None = None) -> str:
        if not self.api_key:
            raise RuntimeError("Groq API key missing")
        messages = [{"role": "user", "content": prompt}]
        if system:
            messages.insert(0, {"role": "system", "content": system})
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        response = _http_post(
            "https://api.groq.com/openai/v1/chat/completions",
            json_payload=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout=(3, timeout),
        )
        if response.status_code == 429:
            raise RuntimeError("rate limited")
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        payload = response.json()
        choices = payload.get("choices", [])
        if not choices:
            return ""
        message = choices[0].get("message", {})
        return str(message.get("content", "")).strip()


class OpenRouterProvider(LLMProvider):
    provider_name = "openrouter"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        super().__init__(model=model or _read_env("OPENROUTER_MODEL", _DEFAULT_MODELS["openrouter"]), api_key=api_key or _read_env("OPENROUTER_API_KEY", ""))

    def list_models(self) -> list[str]:
        if not self.api_key:
            return []
        try:
            response = requests.get("https://openrouter.ai/api/v1/models", headers={"Authorization": f"Bearer {self.api_key}"}, timeout=(3, 10))
            if response.status_code != 200:
                return []
            payload = response.json()
            data = payload.get("data", [])
            return [item.get("id", "") for item in data if isinstance(item, dict)]
        except (requests.RequestException, ValueError):
            return []

    def is_available(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str, system: str | None = None, *, json_mode: bool = False, temperature: float = 0.1, max_tokens: int = 512, timeout: int = 20, progress_callback: Callable[[dict[str, Any]], None] | None = None) -> str:
        if not self.api_key:
            raise RuntimeError("OpenRouter API key missing")
        messages = [{"role": "user", "content": prompt}]
        if system:
            messages.insert(0, {"role": "system", "content": system})
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        response = _http_post(
            "https://openrouter.ai/api/v1/chat/completions",
            json_payload=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "HTTP-Referer": "http://localhost", "X-Title": "TOACD"},
            timeout=(3, timeout),
        )
        if response.status_code == 429:
            raise RuntimeError("rate limited")
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        payload = response.json()
        choices = payload.get("choices", [])
        if not choices:
            return ""
        message = choices[0].get("message", {})
        return str(message.get("content", "")).strip()


_PROVIDER_FACTORY = {
    "ollama": OllamaProvider,
    "gemini": GeminiProvider,
    "groq": GroqProvider,
    "openrouter": OpenRouterProvider,
}


def get_provider_for_name(name: str, **kwargs: Any) -> LLMProvider:
    key = (name or "ollama").lower().strip()
    factory = _PROVIDER_FACTORY.get(key, OllamaProvider)
    return factory(**kwargs)


def resolve_provider_chain(selected_provider: str | None = None, *, allow_cloud: bool = False) -> list[str]:
    selected = (selected_provider or "ollama").lower().strip()
    ordered = [selected]
    for name in ["ollama", "gemini", "groq", "openrouter"]:
        if name not in ordered and (allow_cloud or name == "ollama"):
            ordered.append(name)
    if selected == "ollama":
        return ["ollama"]
    return ordered[:1] if selected else ["ollama"]


__all__ = [
    "LLMProvider",
    "OllamaProvider",
    "GeminiProvider",
    "GroqProvider",
    "OpenRouterProvider",
    "get_provider_for_name",
    "resolve_provider_chain",
    "_http_post",
]
