"""Secure, schema-first OpenAI Responses API client for SPECTRA.

Secrets are read from the server process environment or an ignored .env.local
file. The client never logs or returns the API key.
"""

from __future__ import annotations

import json
import os
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


ROOT = Path(__file__).resolve().parents[1]


class LLMConfigurationError(RuntimeError):
    pass


class LLMResponseError(RuntimeError):
    pass


class LLMProviderError(RuntimeError):
    """A safe, actionable provider error without secret-bearing request data."""

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def load_local_env(path: Path | None = None) -> None:
    """Load simple KEY=VALUE entries without overwriting process variables."""
    env_path = path or ROOT / ".env.local"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise LLMConfigurationError(f"{name} must be an integer") from exc
    if value < 0:
        raise LLMConfigurationError(f"{name} must be zero or greater")
    return value


@dataclass(frozen=True)
class LLMSettings:
    api_key: str
    model: str
    timeout_seconds: int
    max_retries: int

    @classmethod
    def from_environment(cls, *, load_env_file: bool = True) -> "LLMSettings":
        if load_env_file:
            load_local_env()
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise LLMConfigurationError(
                "OPENAI_API_KEY is missing. Put a newly created key in the server "
                "environment or the ignored .env.local file."
            )
        model = os.environ.get("SPECTRA_MODEL", "gpt-5-mini").strip()
        if not model:
            raise LLMConfigurationError("SPECTRA_MODEL cannot be empty")
        return cls(
            api_key=api_key,
            model=model,
            timeout_seconds=_positive_int("SPECTRA_LLM_TIMEOUT", 120),
            max_retries=_positive_int("SPECTRA_LLM_MAX_RETRIES", 2),
        )


@dataclass(frozen=True)
class OllamaSettings:
    model: str
    base_url: str
    timeout_seconds: int
    num_ctx: int = 8192
    think: bool = False

    @classmethod
    def from_environment(cls, *, load_env_file: bool = True) -> "OllamaSettings":
        if load_env_file:
            load_local_env()
        model = os.environ.get("SPECTRA_MODEL", "qwen3:8b").strip()
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/")
        if not model:
            raise LLMConfigurationError("SPECTRA_MODEL cannot be empty")
        if not base_url.startswith(("http://127.0.0.1", "http://localhost", "https://")):
            raise LLMConfigurationError("OLLAMA_BASE_URL must be localhost HTTP or an HTTPS URL")
        return cls(
            model=model,
            base_url=base_url,
            timeout_seconds=_positive_int("OLLAMA_TIMEOUT", 600),
            num_ctx=_positive_int("OLLAMA_NUM_CTX", 8192),
            think=os.environ.get("OLLAMA_THINK", "false").strip().lower() in {"1", "true", "yes", "on"},
        )


class StructuredLLM(Protocol):
    def generate_json(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        ...


class OpenAIResponsesClient:
    """Small adapter around the current OpenAI Responses API."""

    def __init__(self, settings: LLMSettings | None = None):
        self.settings = settings or LLMSettings.from_environment()
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMConfigurationError(
                "The OpenAI Python package is not installed. Run: "
                "python3 -m pip install -r requirements-llm.txt"
            ) from exc
        self._client = OpenAI(
            api_key=self.settings.api_key,
            timeout=self.settings.timeout_seconds,
            max_retries=self.settings.max_retries,
        )

    def generate_json(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            response = self._client.responses.create(
                model=self.settings.model,
                instructions=instructions,
                input=input_text,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    }
                },
            )
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            body = getattr(exc, "body", None)
            error = body.get("error", body) if isinstance(body, dict) else {}
            code = error.get("code") if isinstance(error, dict) else None
            if code in {"credit_balance_exhausted", "insufficient_quota"} or status_code == 429:
                raise LLMProviderError(
                    "OpenAI API has no available quota or credits. Add billing credits to the configured project, then retry.",
                    status_code=status_code,
                    code=code or "insufficient_quota",
                ) from exc
            if status_code in {401, 403}:
                raise LLMProviderError(
                    "OpenAI API rejected the configured key or project permissions.",
                    status_code=status_code,
                    code=code,
                ) from exc
            raise LLMProviderError(
                f"OpenAI API request failed ({type(exc).__name__}).",
                status_code=status_code,
                code=code,
            ) from exc
        output_text = getattr(response, "output_text", None)
        if not output_text:
            raise LLMResponseError("OpenAI response did not contain output_text")
        try:
            payload = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise LLMResponseError("OpenAI response was not valid JSON") from exc
        usage = getattr(response, "usage", None)
        if usage is None:
            usage_data = None
        elif hasattr(usage, "model_dump"):
            usage_data = usage.model_dump()
        else:
            usage_data = {
                key: getattr(usage, key)
                for key in ("input_tokens", "output_tokens", "total_tokens")
                if hasattr(usage, key)
            }
        metadata = {
            "provider": "openai",
            "api": "responses",
            "response_id": getattr(response, "id", None),
            "model": getattr(response, "model", self.settings.model),
            "usage": usage_data,
        }
        return payload, metadata


class OllamaChatClient:
    """Local Ollama /api/chat adapter with native JSON Schema output."""

    def __init__(self, settings: OllamaSettings | None = None):
        self.settings = settings or OllamaSettings.from_environment()

    def generate_json(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        request_payload = {
            "model": self.settings.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": input_text},
            ],
            "format": schema,
            "think": self.settings.think,
            "options": {"temperature": 0, "num_ctx": self.settings.num_ctx},
        }
        request = Request(
            f"{self.settings.base_url}/api/chat",
            data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                raw_response = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise LLMProviderError(
                f"Ollama API returned HTTP {exc.code}: {detail}", status_code=exc.code, code="ollama_http_error"
            ) from exc
        except URLError as exc:
            raise LLMProviderError(
                "Cannot reach Ollama. Start it with `ollama serve` and retry.", code="ollama_unreachable"
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise LLMProviderError(
                f"Ollama request exceeded {self.settings.timeout_seconds}s. Reduce the batch size or increase OLLAMA_TIMEOUT.",
                code="ollama_timeout",
            ) from exc
        content = (raw_response.get("message") or {}).get("content")
        if not content:
            raise LLMResponseError("Ollama response did not contain message.content")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMResponseError("Ollama response was not valid JSON") from exc
        metadata = {
            "provider": "ollama",
            "api": "chat",
            "response_id": None,
            "model": raw_response.get("model", self.settings.model),
            "usage": {
                "input_tokens": raw_response.get("prompt_eval_count"),
                "output_tokens": raw_response.get("eval_count"),
                "total_tokens": (
                    (raw_response.get("prompt_eval_count") or 0) + (raw_response.get("eval_count") or 0)
                ),
            },
            "duration_ns": raw_response.get("total_duration"),
            "schema_name": schema_name,
        }
        return payload, metadata


def create_llm_client(provider: str | None = None) -> StructuredLLM:
    load_local_env()
    selected = (provider or os.environ.get("SPECTRA_LLM_PROVIDER", "openai")).strip().lower()
    if selected == "openai":
        return OpenAIResponsesClient()
    if selected == "ollama":
        return OllamaChatClient()
    raise LLMConfigurationError(f"Unsupported SPECTRA_LLM_PROVIDER: {selected}")
