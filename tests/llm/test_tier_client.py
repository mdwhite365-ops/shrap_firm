"""Tests for the ADR-0009 tier registry and LLM client."""

from __future__ import annotations

from typing import Any

import pytest

from shrap.llm import (
    LLMResult,
    ModelBinding,
    OllamaError,
    ProviderNotConfiguredError,
    TierLLMClient,
    TierRegistry,
    UnknownTierError,
)
from shrap.llm.registry import (
    PROVIDER_ANTHROPIC,
    PROVIDER_OLLAMA,
    TIER_CLOUD_DEFAULT,
    TIER_LOCAL_CLASSIFICATION,
    TIER_LOCAL_HEAVY,
)

# --- registry ------------------------------------------------------------------


def test_local_classification_resolves_to_dell_ollama_default() -> None:
    binding = TierRegistry({}).resolve(TIER_LOCAL_CLASSIFICATION)

    assert binding == ModelBinding(
        tier=TIER_LOCAL_CLASSIFICATION,
        provider=PROVIDER_OLLAMA,
        model="qwen2.5:9b-instruct-q4_K_M",
        base_url="http://ollama:11434",
    )


def test_cloud_default_mirrors_registry_doc_and_has_no_base_url() -> None:
    binding = TierRegistry({}).resolve(TIER_CLOUD_DEFAULT)

    assert binding.provider == PROVIDER_ANTHROPIC
    assert binding.model == "claude-sonnet-4-6"
    assert binding.base_url is None


def test_env_can_point_a_cloud_tier_at_ollama() -> None:
    registry = TierRegistry(
        {
            "SHRAP_LLM_CLOUD_DEFAULT_PROVIDER": "ollama",
            "SHRAP_LLM_CLOUD_DEFAULT_MODEL": "qwen2.5:9b-instruct-q4_K_M",
        }
    )

    binding = registry.resolve(TIER_CLOUD_DEFAULT)

    assert binding.provider == PROVIDER_OLLAMA
    assert binding.model == "qwen2.5:9b-instruct-q4_K_M"
    assert binding.base_url == "http://ollama:11434"


def test_ollama_url_override_strips_trailing_slash() -> None:
    registry = TierRegistry({"SHRAP_LLM_OLLAMA_URL": "http://dell:11434/"})

    assert registry.resolve(TIER_LOCAL_HEAVY).base_url == "http://dell:11434"


def test_unknown_tier_and_unknown_provider_raise() -> None:
    with pytest.raises(UnknownTierError):
        TierRegistry({}).resolve("no-llm")
    with pytest.raises(UnknownTierError):
        TierRegistry({"SHRAP_LLM_CLOUD_DEFAULT_PROVIDER": "openai"}).resolve(TIER_CLOUD_DEFAULT)


# --- client --------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int, payload: Any, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        return self._payload


class FakeHTTP:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests: list[tuple[str, dict[str, Any], float]] = []

    async def post(self, url: str, *, json: dict[str, Any], timeout: float) -> FakeResponse:
        self.requests.append((url, json, timeout))
        return self.response


def _ok_response(content: str = "melt-up continues") -> FakeResponse:
    return FakeResponse(
        200,
        {
            "message": {"role": "assistant", "content": content},
            "prompt_eval_count": 42,
            "eval_count": 17,
        },
    )


async def test_complete_calls_ollama_chat_and_returns_result() -> None:
    http = FakeHTTP(_ok_response())
    client = TierLLMClient(TierRegistry({}), http)

    result = await client.complete(
        TIER_LOCAL_CLASSIFICATION,
        prompt="Classify this regime.",
        system="You are a classifier.",
        json_mode=True,
    )

    assert isinstance(result, LLMResult)
    assert result.content == "melt-up continues"
    assert result.input_tokens == 42
    assert result.output_tokens == 17
    assert result.model == "qwen2.5:9b-instruct-q4_K_M"

    url, body, _timeout = http.requests[0]
    assert url == "http://ollama:11434/api/chat"
    assert body["model"] == "qwen2.5:9b-instruct-q4_K_M"
    assert body["stream"] is False
    assert body["format"] == "json"
    assert body["messages"][0] == {"role": "system", "content": "You are a classifier."}
    assert body["messages"][1] == {"role": "user", "content": "Classify this regime."}


async def test_complete_omits_format_and_system_when_not_requested() -> None:
    http = FakeHTTP(_ok_response())
    client = TierLLMClient(TierRegistry({}), http)

    await client.complete(TIER_LOCAL_CLASSIFICATION, prompt="hello")

    _url, body, _timeout = http.requests[0]
    assert "format" not in body
    assert body["messages"] == [{"role": "user", "content": "hello"}]


async def test_unconfigured_cloud_tier_fails_loudly_without_calling_anything() -> None:
    http = FakeHTTP(_ok_response())
    client = TierLLMClient(TierRegistry({}), http)

    with pytest.raises(ProviderNotConfiguredError):
        await client.complete(TIER_CLOUD_DEFAULT, prompt="synthesize")
    assert http.requests == []


async def test_cloud_tier_overridden_to_ollama_completes() -> None:
    http = FakeHTTP(_ok_response())
    registry = TierRegistry({"SHRAP_LLM_CLOUD_DEFAULT_PROVIDER": "ollama"})
    client = TierLLMClient(registry, http)

    result = await client.complete(TIER_CLOUD_DEFAULT, prompt="synthesize")

    assert result.provider == PROVIDER_OLLAMA
    # Model falls back to the tier's registry default when only the provider
    # is overridden — the loud failure here is Ollama missing the model, not
    # a silent substitution.
    assert result.model == "claude-sonnet-4-6"


async def test_non_200_response_raises_ollama_error() -> None:
    http = FakeHTTP(FakeResponse(500, {}, text="model not found"))
    client = TierLLMClient(TierRegistry({}), http)

    with pytest.raises(OllamaError, match="500"):
        await client.complete(TIER_LOCAL_CLASSIFICATION, prompt="hello")


async def test_empty_content_raises_ollama_error() -> None:
    http = FakeHTTP(FakeResponse(200, {"message": {"role": "assistant", "content": ""}}))
    client = TierLLMClient(TierRegistry({}), http)

    with pytest.raises(OllamaError, match="no content"):
        await client.complete(TIER_LOCAL_CLASSIFICATION, prompt="hello")
