"""Tier-driven LLM completion client.

One call surface for every LLM-using agent: ``complete(tier, prompt, ...)``.
The registry decides which provider serves the tier; this module executes
the call and returns a uniform :class:`LLMResult` with token counts and
latency for cost accounting.

Every completion is also traced to Langfuse when the deployment carries keys
(:mod:`shrap.llm.tracing`). Instrumenting here rather than in each agent is
the whole reason KI-018 was one card: eleven call sites across the Tech
Watcher, News Analyzer, Filing Processor, Hypothesis Generator and the two
research CLIs all pass through this method, and an agent added tomorrow is
traced by construction rather than by remembering.

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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from shrap.llm.http import HTTPClient, HTTPResponse
from shrap.llm.registry import PROVIDER_OLLAMA, ModelBinding, TierRegistry
from shrap.llm.tracing import LangfuseTracer, TracedCall

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


class TierLLMClient:
    """Execute completions against whatever provider a tier resolves to."""

    def __init__(
        self,
        registry: TierRegistry,
        http: HTTPClient,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        tracer: LangfuseTracer | None = None,
    ) -> None:
        self._registry = registry
        self._http = http
        self._timeout = timeout_seconds
        self._tracer = tracer

    async def complete(
        self,
        tier: str,
        prompt: str,
        system: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        think: bool | None = None,
        task: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
    ) -> LLMResult:
        """Complete a prompt on whatever model serves ``tier``.

        ``think`` controls reasoning-model behavior: ``False`` disables the
        thinking pass (bulk classification wants this — qwen3.5 reasons out
        loud by default and that is latency and tokens), ``True`` forces it,
        ``None`` leaves the model default.

        ``task`` names the job for tracing — ``tech-watcher.filter``, not the
        tier. llm-routing.md slices the migration sample by task type, and
        several unrelated jobs share a tier, so a trace named after the tier
        cannot be sliced back apart. It defaults to the tier anyway, because a
        caller that forgets should still produce a trace.

        ``metadata`` is merged into the trace, for whatever a caller wants to
        slice on later: a source feed, a prompt version, a regime.

        ``trace_id`` joins this call onto an existing trace rather than opening
        a new one — the escalation path scores one item twice and that is one
        unit of work, not two. ``session_id`` groups every trace from one pass,
        so a 300-item filter run reads as a run rather than 300 loose rows.
        """

        binding = self._registry.resolve(tier)
        if binding.provider != PROVIDER_OLLAMA:
            # No request leaves the process, so there is nothing to trace: a
            # trace here would record a call that never happened.
            raise ProviderNotConfiguredError(
                f"tier {tier!r} resolves to provider {binding.provider!r}, which is not "
                "configured in this deployment (local-only ruling 2026-07-15); either set "
                f"SHRAP_LLM_{tier.upper().replace('-', '_')}_PROVIDER=ollama or configure "
                "the provider"
            )

        started_at = datetime.now(UTC)
        started = time.monotonic()
        try:
            result = await self._complete_ollama(
                binding, prompt, system, json_mode, temperature, think
            )
        except Exception as exc:
            await self._trace(
                binding=binding,
                task=task,
                prompt=prompt,
                system=system,
                started_at=started_at,
                latency_ms=(time.monotonic() - started) * 1000.0,
                json_mode=json_mode,
                temperature=temperature,
                think=think,
                metadata=metadata,
                trace_id=trace_id,
                session_id=session_id,
                content=None,
                input_tokens=None,
                output_tokens=None,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

        await self._trace(
            binding=binding,
            task=task,
            prompt=prompt,
            system=system,
            started_at=started_at,
            latency_ms=result.latency_ms,
            json_mode=json_mode,
            temperature=temperature,
            think=think,
            metadata=metadata,
            trace_id=trace_id,
            session_id=session_id,
            content=result.content,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            error=None,
        )
        return result

    async def _trace(
        self,
        *,
        binding: ModelBinding,
        task: str | None,
        prompt: str,
        system: str | None,
        started_at: datetime,
        latency_ms: float,
        json_mode: bool,
        temperature: float,
        think: bool | None,
        metadata: Mapping[str, Any] | None,
        trace_id: str | None,
        session_id: str | None,
        content: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
        error: str | None,
    ) -> None:
        if self._tracer is None:
            return
        parameters: dict[str, Any] = {"temperature": temperature, "json_mode": json_mode}
        if think is not None:
            parameters["think"] = think
        await self._tracer.record(
            TracedCall(
                task=task or binding.tier,
                tier=binding.tier,
                provider=binding.provider,
                model=binding.model,
                prompt=prompt,
                system=system,
                started_at=started_at,
                # Derived from the measured latency rather than read off the
                # clock a second time, so the duration Langfuse shows is the
                # same number `llm.completed` logged. Two wall-clock reads
                # would disagree by however long the tracing branch took.
                ended_at=started_at + timedelta(milliseconds=latency_ms),
                content=content,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model_parameters=parameters,
                metadata=dict(metadata) if metadata else {},
                trace_id=trace_id,
                session_id=session_id,
                error=error,
            )
        )

    async def _complete_ollama(
        self,
        binding: ModelBinding,
        prompt: str,
        system: str | None,
        json_mode: bool,
        temperature: float,
        think: bool | None = None,
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
        if think is not None:
            body["think"] = think

        # ollama.com serves the same /api/chat contract as the local daemon and
        # acts as a remote Ollama host, so the only difference is the token.
        headers = (
            {"Authorization": f"Bearer {binding.api_key}"} if binding.api_key is not None else None
        )
        started = time.monotonic()
        response = await self._http.post(
            f"{binding.base_url}/api/chat", json=body, timeout=self._timeout, headers=headers
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


# `HTTPClient` and `HTTPResponse` moved to `shrap.llm.http` so the tracer could
# share them without an import cycle. Re-exported here because that is where
# every existing caller looks for them.
__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "HTTPClient",
    "HTTPResponse",
    "LLMError",
    "LLMResult",
    "OllamaError",
    "ProviderNotConfiguredError",
    "TierLLMClient",
]
