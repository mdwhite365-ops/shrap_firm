"""Tier-driven LLM completion client.

One call surface for every LLM-using agent: ``complete(tier, prompt, ...)``.
The registry decides which provider serves the tier; this module executes
the call and returns a uniform :class:`LLMResult` with token counts and
latency for cost accounting (Langfuse tracing is a later card — until then,
callers log the result metadata through structlog).

Backends:

- **Ollama** (live): ``POST {base_url}/api/chat`` with ``stream=false``.
- **Anthropic** (deliberately not implemented): resolving a tier to the
  anthropic provider raises :class:`ProviderNotConfiguredError`. Per Mike's
  ruling (2026-07-15) the firm runs local-only until API billing is set up;
  failing loudly beats silently degrading to a model the registry did not
  promise.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

from shrap.llm.registry import PROVIDER_OLLAMA, ModelBinding, TierRegistry

log = structlog.get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120.0


class LLMError(Exception):
    """Base error for LLM calls."""


class ProviderNotConfiguredError(LLMError):
    """The tier resolves to a provider this deployment cannot call."""


class OllamaError(LLMError):
    """Ollama returned a non-success response or an unusable body."""


@dataclass(frozen=True, slots=True)
class LLMResult:
    """Uniform result of one completion call."""

    tier: str
    provider: str
    model: str
    content: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float


class HTTPResponse(Protocol):
    @property
    def status_code(self) -> int: ...

    def json(self) -> Any: ...

    @property
    def text(self) -> str: ...


class HTTPClient(Protocol):
    """The slice of httpx.AsyncClient the client needs."""

    async def post(self, url: str, *, json: dict[str, Any], timeout: float) -> HTTPResponse: ...


class TierLLMClient:
    """Execute completions against whatever provider a tier resolves to."""

    def __init__(
        self,
        registry: TierRegistry,
        http: HTTPClient,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._registry = registry
        self._http = http
        self._timeout = timeout_seconds

    async def complete(
        self,
        tier: str,
        prompt: str,
        system: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.2,
    ) -> LLMResult:
        binding = self._registry.resolve(tier)
        if binding.provider != PROVIDER_OLLAMA:
            raise ProviderNotConfiguredError(
                f"tier {tier!r} resolves to provider {binding.provider!r}, which is not "
                "configured in this deployment (local-only ruling 2026-07-15); either set "
                f"SHRAP_LLM_{tier.upper().replace('-', '_')}_PROVIDER=ollama or configure "
                "the provider"
            )
        return await self._complete_ollama(binding, prompt, system, json_mode, temperature)

    async def _complete_ollama(
        self,
        binding: ModelBinding,
        prompt: str,
        system: str | None,
        json_mode: bool,
        temperature: float,
    ) -> LLMResult:
        messages: list[dict[str, str]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {
            "model": binding.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_mode:
            body["format"] = "json"

        started = time.monotonic()
        response = await self._http.post(
            f"{binding.base_url}/api/chat", json=body, timeout=self._timeout
        )
        latency_ms = (time.monotonic() - started) * 1000.0
        if response.status_code != 200:
            raise OllamaError(
                f"ollama returned {response.status_code} for model {binding.model!r}: "
                f"{response.text[:500]}"
            )
        payload = response.json()
        message = payload.get("message") if isinstance(payload, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content:
            raise OllamaError(f"ollama response for model {binding.model!r} carried no content")

        result = LLMResult(
            tier=binding.tier,
            provider=binding.provider,
            model=binding.model,
            content=content,
            input_tokens=_int_or_none(payload.get("prompt_eval_count")),
            output_tokens=_int_or_none(payload.get("eval_count")),
            latency_ms=latency_ms,
        )
        log.info(
            "llm.completed",
            tier=result.tier,
            provider=result.provider,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=round(result.latency_ms, 1),
            json_mode=json_mode,
        )
        return result


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
