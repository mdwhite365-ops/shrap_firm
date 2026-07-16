"""Tests for the Strategy Librarian service loop."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from shrap.common.envelope import Envelope
from shrap.events import ReceivedEvent
from shrap.events.groups import GroupEventSubscriber
from shrap.research.librarian_service import (
    STREAM_STRATEGY_VERDICT,
    apply_verdict_event,
    poll_once,
)
from shrap.research.strategy_registry import (
    STATUS_HYPOTHESIS,
    STATUS_KILLED,
    STATUS_PAPER,
    STREAM_STRATEGY_KILLED,
    STREAM_STRATEGY_PROMOTED,
    InvalidTransitionError,
    StrategyTransition,
)


def _verdict_event(payload: dict[str, Any] | None, redis_stream_id: str = "1-0") -> ReceivedEvent:
    envelope = Envelope.new(
        produced_by="strategy-evaluator",
        schema_version="1.0.0",
        payload=payload,
    )
    return ReceivedEvent(
        stream=STREAM_STRATEGY_VERDICT,
        redis_stream_id=redis_stream_id,
        envelope=envelope,
    )


def _promote_payload() -> dict[str, Any]:
    return {
        "strategy_id": "01TESTSTRATEGY",
        "verdict": "promote",
        "from_stage": STATUS_HYPOTHESIS,
        "to_stage": STATUS_PAPER,
        "metrics_ref": "eval-42",
        "reason": "walk-forward passed, PBO 0.31",
    }


def _transition(to_status: str = STATUS_PAPER) -> StrategyTransition:
    return StrategyTransition(
        transition_id="01TR",
        strategy_id="01TESTSTRATEGY",
        from_status=STATUS_HYPOTHESIS,
        to_status=to_status,
        reason="walk-forward passed, PBO 0.31",
        trigger_kind="evaluation",
        trigger_ref="eval-42",
        actor="strategy-librarian",
        occurred_at=datetime(2026, 7, 16, tzinfo=UTC),
    )


class FakeRegistry:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result: StrategyTransition = _transition()
        self.raises: Exception | None = None

    async def transition(
        self,
        strategy_id: str,
        to_status: str,
        *,
        reason: str,
        trigger_kind: str,
        actor: str,
        trigger_ref: str | None = None,
        expected_from: str | None = None,
    ) -> StrategyTransition:
        self.calls.append(
            {
                "strategy_id": strategy_id,
                "to_status": to_status,
                "reason": reason,
                "trigger_kind": trigger_kind,
                "actor": actor,
                "trigger_ref": trigger_ref,
                "expected_from": expected_from,
            }
        )
        if self.raises is not None:
            raise self.raises
        return self.result


class FakeRedis:
    """Group client + publisher fake: preloaded verdict entries, records acks/xadds."""

    def __init__(self, entries: list[tuple[str, dict[str, str]]] | None = None) -> None:
        self.entries = entries or []
        self.acked: list[str] = []
        self.published: list[tuple[str, dict[str, str]]] = []
        self.groups_created: list[str] = []

    async def xgroup_create(
        self, name: str, groupname: str, id: str = "$", mkstream: bool = False
    ) -> Any:
        self.groups_created.append(f"{name}:{groupname}")
        return "OK"

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[Any, Any],
        count: int | None = None,
        block: int | None = None,
    ) -> Any:
        read_id = next(iter(streams.values()))
        if read_id != ">" or not self.entries:
            return []
        batch, self.entries = self.entries, []
        return [(STREAM_STRATEGY_VERDICT, batch)]

    async def xack(self, name: str, groupname: str, *ids: str) -> Any:
        self.acked.extend(ids)
        return len(ids)

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.published.append((stream, fields))
        return f"{len(self.published)}-0"


def _entries(*payloads: dict[str, Any] | None) -> list[tuple[str, dict[str, str]]]:
    entries = []
    for i, payload in enumerate(payloads):
        envelope = Envelope.new(
            produced_by="strategy-evaluator", schema_version="1.0.0", payload=payload
        )
        entries.append((f"{i + 1}-0", envelope.to_redis_fields()))
    return entries


def _subscriber(redis: FakeRedis) -> GroupEventSubscriber:
    return GroupEventSubscriber(redis, group="strategy-librarian", start_id="0")


# --- apply_verdict_event -------------------------------------------------------


async def test_apply_verdict_event_promotes_with_expected_from() -> None:
    registry = FakeRegistry()
    event = _verdict_event(_promote_payload())

    transition = await apply_verdict_event(registry, event)

    assert transition is registry.result
    assert registry.calls == [
        {
            "strategy_id": "01TESTSTRATEGY",
            "to_status": STATUS_PAPER,
            "reason": "walk-forward passed, PBO 0.31",
            "trigger_kind": "evaluation",
            "actor": "strategy-librarian",
            "trigger_ref": "eval-42",
            "expected_from": STATUS_HYPOTHESIS,
        }
    ]


async def test_apply_verdict_event_noop_verdicts_touch_nothing() -> None:
    registry = FakeRegistry()
    for verdict in ("hold-for-data", "accept-refit", "reject-refit-keep-prior"):
        event = _verdict_event({"strategy_id": "01TESTSTRATEGY", "verdict": verdict})
        assert await apply_verdict_event(registry, event) is None
    assert registry.calls == []


async def test_apply_verdict_event_defaults_reason_and_trigger_ref() -> None:
    registry = FakeRegistry()
    payload = {
        "strategy_id": "01TESTSTRATEGY",
        "verdict": "kill",
        "to_stage": STATUS_KILLED,
    }
    event = _verdict_event(payload)

    await apply_verdict_event(registry, event)

    call = registry.calls[0]
    assert call["reason"] == "evaluator verdict: kill"
    assert call["trigger_ref"] == event.envelope.event_id
    assert call["expected_from"] is None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"verdict": "promote", "to_stage": STATUS_PAPER},
        {"strategy_id": "01TESTSTRATEGY", "to_stage": STATUS_PAPER},
        {"strategy_id": "01TESTSTRATEGY", "verdict": "promote"},
        {"strategy_id": "", "verdict": "promote", "to_stage": STATUS_PAPER},
    ],
)
async def test_apply_verdict_event_rejects_malformed_payloads(
    payload: dict[str, Any] | None,
) -> None:
    registry = FakeRegistry()
    with pytest.raises(ValueError):
        await apply_verdict_event(registry, _verdict_event(payload))
    assert registry.calls == []


# --- poll_once -----------------------------------------------------------------


async def test_poll_once_applies_verdict_publishes_lifecycle_event_and_acks() -> None:
    redis = FakeRedis(_entries(_promote_payload()))
    registry = FakeRegistry()

    applied = await poll_once(redis, registry, _subscriber(redis), count=10, block_ms=10)

    assert applied == 1
    assert redis.acked == ["1-0"]
    assert len(redis.published) == 1
    stream, _fields = redis.published[0]
    assert stream == STREAM_STRATEGY_PROMOTED


async def test_poll_once_kill_verdict_publishes_killed_stream() -> None:
    payload = dict(_promote_payload(), verdict="kill", to_stage=STATUS_KILLED)
    redis = FakeRedis(_entries(payload))
    registry = FakeRegistry()
    registry.result = _transition(to_status=STATUS_KILLED)

    applied = await poll_once(redis, registry, _subscriber(redis), count=10, block_ms=10)

    assert applied == 1
    assert redis.published[0][0] == STREAM_STRATEGY_KILLED


async def test_poll_once_acks_and_skips_rejected_transition_without_publishing() -> None:
    redis = FakeRedis(_entries(_promote_payload(), _promote_payload()))
    registry = FakeRegistry()
    registry.raises = InvalidTransitionError("already applied")

    applied = await poll_once(redis, registry, _subscriber(redis), count=10, block_ms=10)

    assert applied == 0
    assert redis.acked == ["1-0", "2-0"]  # both poison-skipped, offsets advanced
    assert redis.published == []


async def test_poll_once_noop_verdict_acks_without_publishing() -> None:
    redis = FakeRedis(_entries({"strategy_id": "01TESTSTRATEGY", "verdict": "hold-for-data"}))
    registry = FakeRegistry()

    applied = await poll_once(redis, registry, _subscriber(redis), count=10, block_ms=10)

    assert applied == 0
    assert redis.acked == ["1-0"]
    assert redis.published == []


async def test_poll_once_systemic_error_leaves_event_unacked() -> None:
    redis = FakeRedis(_entries(_promote_payload(), _promote_payload()))
    registry = FakeRegistry()
    registry.raises = RuntimeError("database down")

    applied = await poll_once(redis, registry, _subscriber(redis), count=10, block_ms=10)

    assert applied == 0
    assert redis.acked == []  # stays pending for redelivery
    assert redis.published == []
    assert len(registry.calls) == 1  # stopped at the first failure


# --- settings ------------------------------------------------------------------


def test_settings_reads_strategy_librarian_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from shrap.agents.research.strategy_librarian.config import Settings

    monkeypatch.setenv("STRATEGY_LIBRARIAN_REDIS_URL", "redis://elsewhere:6379/1")
    monkeypatch.setenv("STRATEGY_LIBRARIAN_START_ID", "$")
    settings = Settings()

    assert settings.redis_url == "redis://elsewhere:6379/1"
    assert settings.start_id == "$"
    assert settings.redacted()["postgres_dsn"] == "***"
