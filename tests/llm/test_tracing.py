"""Tests for Langfuse tracing of LLM calls (KI-018).

The behaviour worth pinning down is mostly about what tracing must *not* do:
never raise into the agent, never claim a trace was stored when the 207 body
says it was rejected, and never silently shorten a sample the migration
protocol expects to be complete.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from shrap.llm import (
    LangfuseTracer,
    OllamaError,
    ProviderNotConfiguredError,
    TierLLMClient,
    TierRegistry,
    TracedCall,
    TracingConfig,
    tracer_from_env,
    tracing_config_from_env,
)
from shrap.llm.registry import TIER_CLOUD_DEFAULT, TIER_LOCAL_CLASSIFICATION
from shrap.llm.tracing import DEFAULT_HOST, LEVEL_DEFAULT, LEVEL_ERROR

KEYS = {"LANGFUSE_PUBLIC_KEY": "pk-lf-1", "LANGFUSE_SECRET_KEY": "sk-lf-1"}


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {"successes": [], "errors": []}
        self.text = text

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeHTTP:
    """Answers every POST from a queue, or with one canned response."""

    def __init__(self, *responses: FakeResponse) -> None:
        self._responses = list(responses)
        self.requests: list[tuple[str, dict[str, Any], float]] = []
        self.headers: list[dict[str, str] | None] = []

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        timeout: float,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        self.requests.append((url, json, timeout))
        self.headers.append(headers)
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


class ExplodingHTTP:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.requests: list[str] = []

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        timeout: float,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        self.requests.append(url)
        raise self.exc


def _config(**overrides: Any) -> TracingConfig:
    base: dict[str, Any] = {
        "host": "http://langfuse:3000",
        "public_key": "pk-lf-1",
        "secret_key": "sk-lf-1",
    }
    base.update(overrides)
    return TracingConfig(**base)


def _call(**overrides: Any) -> TracedCall:
    started = datetime(2026, 8, 23, 14, 0, 0, tzinfo=UTC)
    base: dict[str, Any] = {
        "task": "tech-watcher.filter",
        "tier": "local-classification",
        "provider": "ollama",
        "model": "qwen3.5:397b",
        "prompt": "Score this filing.",
        "system": "You are a filter.",
        "started_at": started,
        "ended_at": started + timedelta(milliseconds=1500),
        "content": '{"relevant": true}',
        "input_tokens": 42,
        "output_tokens": 17,
    }
    base.update(overrides)
    return TracedCall(**base)


def _batch(http: FakeHTTP) -> list[dict[str, Any]]:
    _url, body, _timeout = http.requests[0]
    batch: list[dict[str, Any]] = body["batch"]
    return batch


def _event(http: FakeHTTP, event_type: str) -> dict[str, Any]:
    return next(e for e in _batch(http) if e["type"] == event_type)


# --- config from env -----------------------------------------------------------


def test_no_keys_means_no_tracer_rather_than_a_broken_one() -> None:
    assert tracing_config_from_env({}) is None
    assert tracer_from_env({}, FakeHTTP(FakeResponse(207))) is None


def test_one_key_alone_is_not_enough() -> None:
    assert tracing_config_from_env({"LANGFUSE_PUBLIC_KEY": "pk-lf-1"}) is None
    assert tracing_config_from_env({"LANGFUSE_SECRET_KEY": "sk-lf-1"}) is None


def test_blank_keys_are_absent_keys() -> None:
    # A compose file that passes LANGFUSE_PUBLIC_KEY through with nothing set
    # yields "" rather than a missing key, and must not count as configured.
    assert tracing_config_from_env({**KEYS, "LANGFUSE_PUBLIC_KEY": "   "}) is None


def test_host_defaults_to_the_compose_service() -> None:
    config = tracing_config_from_env(KEYS)

    assert config is not None
    assert config.host == DEFAULT_HOST
    assert config.ingestion_url == "http://langfuse:3000/api/public/ingestion"


def test_trailing_slash_on_host_does_not_double_up() -> None:
    config = tracing_config_from_env({**KEYS, "LANGFUSE_HOST": "http://langfuse:3000/"})

    assert config is not None
    assert config.ingestion_url == "http://langfuse:3000/api/public/ingestion"


def test_unparseable_numeric_overrides_fall_back_instead_of_crashing() -> None:
    config = tracing_config_from_env(
        {**KEYS, "LANGFUSE_TIMEOUT_SECONDS": "soon", "LANGFUSE_MAX_FIELD_CHARS": "lots"}
    )

    assert config is not None
    assert config.timeout_seconds == 5.0
    assert config.max_field_chars == 250_000


def test_authorization_is_basic_public_key_as_user() -> None:
    header = _config().authorization

    assert header.startswith("Basic ")
    decoded = base64.b64decode(header.removeprefix("Basic ")).decode()
    assert decoded == "pk-lf-1:sk-lf-1"


# --- the ingestion payload -----------------------------------------------------


async def test_record_posts_a_trace_and_a_generation_in_one_batch() -> None:
    http = FakeHTTP(FakeResponse(207))

    await LangfuseTracer(http, _config()).record(_call())

    url, _body, timeout = http.requests[0]
    assert url == "http://langfuse:3000/api/public/ingestion"
    assert timeout == 5.0
    assert [e["type"] for e in _batch(http)] == ["trace-create", "generation-create"]
    assert http.headers[0] is not None
    assert http.headers[0]["Authorization"].startswith("Basic ")


async def test_generation_hangs_off_the_trace_it_was_sent_with() -> None:
    http = FakeHTTP(FakeResponse(207))

    await LangfuseTracer(http, _config()).record(_call())

    trace = _event(http, "trace-create")["body"]
    generation = _event(http, "generation-create")["body"]
    assert generation["traceId"] == trace["id"]
    assert generation["id"] != trace["id"]


async def test_every_envelope_carries_its_own_dedupe_id() -> None:
    http = FakeHTTP(FakeResponse(207))

    await LangfuseTracer(http, _config()).record(_call())

    ids = [e["id"] for e in _batch(http)]
    assert len(set(ids)) == len(ids)
    assert all(e["timestamp"] for e in _batch(http))


async def test_full_input_and_output_are_recorded() -> None:
    http = FakeHTTP(FakeResponse(207))

    await LangfuseTracer(http, _config()).record(_call())

    generation = _event(http, "generation-create")["body"]
    assert generation["input"] == [
        {"role": "system", "content": "You are a filter."},
        {"role": "user", "content": "Score this filing."},
    ]
    assert generation["output"] == '{"relevant": true}'
    assert generation["model"] == "qwen3.5:397b"
    assert generation["level"] == LEVEL_DEFAULT
    assert generation["usage"] == {"unit": "TOKENS", "input": 42, "output": 17, "total": 59}


async def test_a_call_without_a_system_prompt_records_only_the_user_turn() -> None:
    http = FakeHTTP(FakeResponse(207))

    await LangfuseTracer(http, _config()).record(_call(system=None))

    generation = _event(http, "generation-create")["body"]
    assert generation["input"] == [{"role": "user", "content": "Score this filing."}]


async def test_missing_token_counts_omit_usage_rather_than_reporting_zero() -> None:
    # Ollama does not always return the counts. A usage block of zeros is
    # indistinguishable from a real zero and would drag any cost total down.
    http = FakeHTTP(FakeResponse(207))

    await LangfuseTracer(http, _config()).record(_call(input_tokens=None, output_tokens=None))

    assert "usage" not in _event(http, "generation-create")["body"]


async def test_partial_token_counts_omit_the_total() -> None:
    http = FakeHTTP(FakeResponse(207))

    await LangfuseTracer(http, _config()).record(_call(output_tokens=None))

    usage = _event(http, "generation-create")["body"]["usage"]
    assert usage == {"unit": "TOKENS", "input": 42}


async def test_task_names_the_trace_so_the_sample_can_be_sliced() -> None:
    http = FakeHTTP(FakeResponse(207))

    await LangfuseTracer(http, _config()).record(_call(task="filing-processor.classify"))

    trace = _event(http, "trace-create")["body"]
    assert trace["name"] == "filing-processor.classify"
    assert trace["metadata"]["tier"] == "local-classification"
    assert set(trace["tags"]) == {"local-classification", "ollama", "qwen3.5:397b"}


async def test_caller_metadata_survives_onto_the_trace() -> None:
    http = FakeHTTP(FakeResponse(207))

    await LangfuseTracer(http, _config()).record(_call(metadata={"prompt_version": 4}))

    assert _event(http, "trace-create")["body"]["metadata"]["prompt_version"] == 4


async def test_latency_is_the_measured_duration_not_a_second_clock_read() -> None:
    http = FakeHTTP(FakeResponse(207))

    await LangfuseTracer(http, _config()).record(_call())

    generation = _event(http, "generation-create")["body"]
    assert generation["metadata"]["latency_ms"] == 1500.0
    assert generation["startTime"] == "2026-08-23T14:00:00+00:00"
    assert generation["endTime"] == "2026-08-23T14:00:01.500000+00:00"


# --- failures ------------------------------------------------------------------


async def test_a_failed_completion_is_traced_as_an_error_with_no_output() -> None:
    http = FakeHTTP(FakeResponse(207))

    await LangfuseTracer(http, _config()).record(
        _call(content=None, input_tokens=None, output_tokens=None, error="OllamaError: 500")
    )

    generation = _event(http, "generation-create")["body"]
    assert generation["level"] == LEVEL_ERROR
    assert generation["statusMessage"] == "OllamaError: 500"
    assert generation["output"] is None


async def test_langfuse_being_unreachable_does_not_raise() -> None:
    http = ExplodingHTTP(ConnectionError("connection refused"))

    await LangfuseTracer(http, _config()).record(_call())

    assert http.requests  # it tried, and swallowed the failure


async def test_a_rejected_207_does_not_raise_either() -> None:
    # The endpoint answers 207 for input errors rather than a 4xx, so the
    # errors array is the only place a rejection shows up.
    http = FakeHTTP(FakeResponse(207, {"successes": [], "errors": [{"id": "x", "status": 400}]}))

    await LangfuseTracer(http, _config()).record(_call())

    assert len(http.requests) == 1


async def test_an_unparseable_body_is_not_read_as_a_rejection() -> None:
    http = FakeHTTP(FakeResponse(207, ValueError("not json")))

    await LangfuseTracer(http, _config()).record(_call())

    assert len(http.requests) == 1


async def test_a_hard_http_error_status_does_not_raise() -> None:
    http = FakeHTTP(FakeResponse(401, {}, text="unauthorized"))

    await LangfuseTracer(http, _config()).record(_call())

    assert len(http.requests) == 1


# --- truncation ----------------------------------------------------------------


async def test_oversized_fields_are_clipped_and_the_clipping_is_declared() -> None:
    # The batch cap is 3.5 MB and an oversized batch is rejected whole, so a
    # filing body has to be clipped. llm-routing.md asks for *full*
    # input/output, so a clipped sample must say so rather than look complete.
    http = FakeHTTP(FakeResponse(207))
    tracer = LangfuseTracer(http, _config(max_field_chars=100))

    await tracer.record(_call(prompt="p" * 5_000, content="c" * 5_000))

    generation = _event(http, "generation-create")["body"]
    assert generation["input"][1]["content"] == "p" * 100
    assert generation["output"] == "c" * 100
    assert generation["metadata"]["input_truncated"] is True
    assert generation["metadata"]["output_truncated"] is True


async def test_a_call_inside_the_limit_is_not_marked_truncated() -> None:
    http = FakeHTTP(FakeResponse(207))

    await LangfuseTracer(http, _config()).record(_call())

    metadata = _event(http, "generation-create")["body"]["metadata"]
    assert "input_truncated" not in metadata
    assert "output_truncated" not in metadata


# --- wiring through the client -------------------------------------------------


def _ollama_ok() -> FakeResponse:
    return FakeResponse(
        200,
        {
            "message": {"role": "assistant", "content": "yes"},
            "prompt_eval_count": 11,
            "eval_count": 3,
        },
    )


class RoutingHTTP:
    """One handle serving both Ollama and Langfuse, as the agents do."""

    def __init__(self, ollama: FakeResponse, langfuse: FakeResponse) -> None:
        self._ollama = ollama
        self._langfuse = langfuse
        self.ollama_requests: list[dict[str, Any]] = []
        self.trace_requests: list[dict[str, Any]] = []

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        timeout: float,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        if "ingestion" in url:
            self.trace_requests.append(json)
            return self._langfuse
        self.ollama_requests.append(json)
        return self._ollama


async def test_a_completion_is_traced_with_the_task_the_caller_named() -> None:
    http = RoutingHTTP(_ollama_ok(), FakeResponse(207))
    client = TierLLMClient(TierRegistry({}), http, tracer=LangfuseTracer(http, _config()))

    await client.complete(
        TIER_LOCAL_CLASSIFICATION,
        prompt="Score it.",
        task="tech-watcher.filter",
        metadata={"item_id": "abc"},
    )

    assert len(http.trace_requests) == 1
    trace = next(e for e in http.trace_requests[0]["batch"] if e["type"] == "trace-create")
    assert trace["body"]["name"] == "tech-watcher.filter"
    assert trace["body"]["metadata"]["item_id"] == "abc"


async def test_an_unnamed_task_falls_back_to_the_tier() -> None:
    http = RoutingHTTP(_ollama_ok(), FakeResponse(207))
    client = TierLLMClient(TierRegistry({}), http, tracer=LangfuseTracer(http, _config()))

    await client.complete(TIER_LOCAL_CLASSIFICATION, prompt="Score it.")

    trace = next(e for e in http.trace_requests[0]["batch"] if e["type"] == "trace-create")
    assert trace["body"]["name"] == TIER_LOCAL_CLASSIFICATION


async def test_model_parameters_are_recorded() -> None:
    http = RoutingHTTP(_ollama_ok(), FakeResponse(207))
    client = TierLLMClient(TierRegistry({}), http, tracer=LangfuseTracer(http, _config()))

    await client.complete(
        TIER_LOCAL_CLASSIFICATION, prompt="x", json_mode=True, temperature=0.4, think=False
    )

    generation = next(
        e for e in http.trace_requests[0]["batch"] if e["type"] == "generation-create"
    )
    assert generation["body"]["modelParameters"] == {
        "temperature": 0.4,
        "json_mode": True,
        "think": False,
    }


async def test_a_failing_completion_still_raises_after_being_traced() -> None:
    http = RoutingHTTP(FakeResponse(500, {}, text="model not found"), FakeResponse(207))
    client = TierLLMClient(TierRegistry({}), http, tracer=LangfuseTracer(http, _config()))

    with pytest.raises(OllamaError):
        await client.complete(TIER_LOCAL_CLASSIFICATION, prompt="x", task="tech-watcher.filter")

    generation = next(
        e for e in http.trace_requests[0]["batch"] if e["type"] == "generation-create"
    )
    assert generation["body"]["level"] == LEVEL_ERROR
    assert "OllamaError" in generation["body"]["statusMessage"]


async def test_a_dead_langfuse_does_not_break_the_completion() -> None:
    """The point of failing open. An observability outage must not stop the
    Tech Watcher from filtering."""

    class OllamaOkTracingDead:
        def __init__(self) -> None:
            self.ollama_requests: list[dict[str, Any]] = []

        async def post(
            self,
            url: str,
            *,
            json: dict[str, Any],
            timeout: float,
            headers: dict[str, str] | None = None,
        ) -> FakeResponse:
            if "ingestion" in url:
                raise ConnectionError("connection refused")
            self.ollama_requests.append(json)
            return _ollama_ok()

    http = OllamaOkTracingDead()
    client = TierLLMClient(TierRegistry({}), http, tracer=LangfuseTracer(http, _config()))

    result = await client.complete(TIER_LOCAL_CLASSIFICATION, prompt="x")

    assert result.content == "yes"


async def test_an_unconfigured_provider_traces_nothing_because_nothing_was_called() -> None:
    http = RoutingHTTP(_ollama_ok(), FakeResponse(207))
    client = TierLLMClient(TierRegistry({}), http, tracer=LangfuseTracer(http, _config()))

    with pytest.raises(ProviderNotConfiguredError):
        await client.complete(TIER_CLOUD_DEFAULT, prompt="x")

    assert http.trace_requests == []
    assert http.ollama_requests == []


async def test_a_client_without_a_tracer_posts_only_to_ollama() -> None:
    http = RoutingHTTP(_ollama_ok(), FakeResponse(207))
    client = TierLLMClient(TierRegistry({}), http)

    await client.complete(TIER_LOCAL_CLASSIFICATION, prompt="x")

    assert http.trace_requests == []
    assert len(http.ollama_requests) == 1
