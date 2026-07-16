"""Tier-alias → model resolution (ADR-0009).

Defaults mirror the tier table in ``docs/infrastructure/llm-registry.md``
(v0.1, seeded 2026-05-30). The doc is the decision record; this module is the
runtime mirror. Deployments override per tier through environment variables,
which is how a local-only deployment points an agent's tier at Ollama without
touching the registry document's shadow-eval protocol:

- ``SHRAP_LLM_OLLAMA_URL`` — base URL for the Ollama backend
  (default ``http://ollama:11434``, the compose service).
- ``SHRAP_LLM_<TIER>_PROVIDER`` / ``SHRAP_LLM_<TIER>_MODEL`` — override one
  tier's binding; ``<TIER>`` is the alias upper-cased with ``-`` → ``_``
  (e.g. ``SHRAP_LLM_CLOUD_DEFAULT_PROVIDER=ollama``).

Overrides are deployment routing, not registry changes: the registry document
still records what each tier *should* be served by once its provider is
configured.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OLLAMA = "ollama"

TIER_CLOUD_JUDGMENT_HEAVY = "cloud-judgment-heavy"
TIER_CLOUD_DEFAULT = "cloud-default"
TIER_CLOUD_CHEAP = "cloud-cheap"
TIER_LOCAL_CLASSIFICATION = "local-classification"
TIER_LOCAL_HEAVY = "local-heavy"

DEFAULT_OLLAMA_URL = "http://ollama:11434"

# Mirror of docs/infrastructure/llm-registry.md v0.2. `no-llm` is not a
# resolvable tier — deterministic agents simply do not construct a client.
_DEFAULT_BINDINGS: dict[str, tuple[str, str]] = {
    TIER_CLOUD_JUDGMENT_HEAVY: (PROVIDER_ANTHROPIC, "claude-opus-4-7"),
    TIER_CLOUD_DEFAULT: (PROVIDER_ANTHROPIC, "claude-sonnet-4-6"),
    TIER_CLOUD_CHEAP: (PROVIDER_ANTHROPIC, "claude-haiku-4"),
    TIER_LOCAL_CLASSIFICATION: (PROVIDER_OLLAMA, "qwen3.5:9b-q4_K_M"),
    TIER_LOCAL_HEAVY: (PROVIDER_OLLAMA, "mistral-small:24b-instruct-q4_K_M"),
}

_KNOWN_PROVIDERS = frozenset({PROVIDER_ANTHROPIC, PROVIDER_OLLAMA})


class UnknownTierError(Exception):
    """The requested tier alias is not in the registry."""


@dataclass(frozen=True, slots=True)
class ModelBinding:
    """One resolved tier: which provider serves it, with which model."""

    tier: str
    provider: str
    model: str
    base_url: str | None


def _env_key(tier: str, suffix: str) -> str:
    return f"SHRAP_LLM_{tier.upper().replace('-', '_')}_{suffix}"


class TierRegistry:
    """Resolve tier aliases to model bindings, with env-var overrides."""

    def __init__(self, env: Mapping[str, str]) -> None:
        self._env = env
        self._ollama_url = env.get("SHRAP_LLM_OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")

    @property
    def ollama_url(self) -> str:
        return self._ollama_url

    def resolve(self, tier: str) -> ModelBinding:
        default = _DEFAULT_BINDINGS.get(tier)
        if default is None:
            raise UnknownTierError(
                f"unknown LLM tier {tier!r}; known tiers: {sorted(_DEFAULT_BINDINGS)}"
            )
        default_provider, default_model = default
        provider = self._env.get(_env_key(tier, "PROVIDER"), default_provider).strip().lower()
        if provider not in _KNOWN_PROVIDERS:
            raise UnknownTierError(
                f"tier {tier!r} resolves to unknown provider {provider!r}; "
                f"known providers: {sorted(_KNOWN_PROVIDERS)}"
            )
        model = self._env.get(_env_key(tier, "MODEL"), "").strip() or default_model
        base_url = self._ollama_url if provider == PROVIDER_OLLAMA else None
        return ModelBinding(tier=tier, provider=provider, model=model, base_url=base_url)
