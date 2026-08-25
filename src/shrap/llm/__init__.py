"""LLM tier client (ADR-0009).

Tier aliases are the contract between agent code and the model layer.
Agents ask for a tier (``cloud-default``, ``local-classification``, …); the
registry in :mod:`shrap.llm.registry` resolves it to a provider + model, and
:class:`shrap.llm.client.TierLLMClient` executes the call. The code defaults
mirror ``docs/infrastructure/llm-registry.md`` v0.1 — that document remains
the decision record; changing a tier's model goes through its shadow-eval
protocol, then the code mirror is updated in the same PR.

Per ``docs/infrastructure/llm-routing.md``, there is deliberately no LLM
proxy: this wrapper is the per-agent client. The Ollama backend is live;
Anthropic-provider tiers resolve but raise ``ProviderNotConfiguredError``
until API billing is set up (Mike's ruling, 2026-07-15: local-only for now).

:mod:`shrap.llm.tracing` records each completion to Langfuse. An agent opts in
with one line — ``tracer=tracer_from_env(env, http)`` at the point it builds
its client — and a deployment without Langfuse keys simply does not trace.
"""

from shrap.llm.client import (
    LLMError,
    LLMResult,
    OllamaError,
    ProviderNotConfiguredError,
    TierLLMClient,
)
from shrap.llm.http import HTTPClient, HTTPResponse
from shrap.llm.registry import (
    PROVIDER_ANTHROPIC,
    PROVIDER_OLLAMA,
    ModelBinding,
    TierRegistry,
    UnknownTierError,
)
from shrap.llm.tracing import (
    LangfuseTracer,
    TracedCall,
    TracingConfig,
    tracer_from_env,
    tracing_config_from_env,
)

__all__ = [
    "PROVIDER_ANTHROPIC",
    "PROVIDER_OLLAMA",
    "HTTPClient",
    "HTTPResponse",
    "LLMError",
    "LLMResult",
    "LangfuseTracer",
    "ModelBinding",
    "OllamaError",
    "ProviderNotConfiguredError",
    "TierLLMClient",
    "TierRegistry",
    "TracedCall",
    "TracingConfig",
    "UnknownTierError",
    "tracer_from_env",
    "tracing_config_from_env",
]
