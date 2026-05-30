"""Tests for the public shrap.events ADR-0006 API."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from shrap.events import Envelope


def test_envelope_is_public_from_shrap_events() -> None:
    env = Envelope.new(
        produced_by="operations/health-monitor",
        schema_version="1.0.0",
        payload={"ok": True},
        correlation_id="01KSW00000000000000000000",
    )

    assert env.event_id
    assert env.produced_at.tzinfo is not None
    assert env.payload == {"ok": True}
    assert env.correlation_id == "01KSW00000000000000000000"


@pytest.mark.asyncio
async def test_publisher_builds_envelope_and_returns_event_ids() -> None:
    from shrap.events import EventPublisher

    class FakeRedis:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, str]]] = []

        async def xadd(self, stream: str, fields: dict[str, str]) -> str:
            self.calls.append((stream, fields))
            return "1780127000000-0"

    redis = FakeRedis()
    publisher = EventPublisher(redis)  # type: ignore[arg-type]

    result = await publisher.publish(
        stream="operations.test-event",
        produced_by="operations/test-agent",
        schema_version="1.0.0",
        payload={"answer": 42},
        correlation_id="01KSW11111111111111111111",
    )

    assert result.redis_stream_id == "1780127000000-0"
    assert result.envelope.produced_by == "operations/test-agent"
    assert result.envelope.payload == {"answer": 42}
    assert result.envelope.correlation_id == "01KSW11111111111111111111"
    assert redis.calls[0][0] == "operations.test-event"
    assert Envelope.from_redis_fields(redis.calls[0][1]) == result.envelope


@pytest.mark.asyncio
async def test_subscriber_reads_and_validates_envelopes() -> None:
    from shrap.events import EventSubscriber

    env = Envelope(
        event_id="01KSW22222222222222222222",
        schema_version="1.0.0",
        produced_at=datetime(2026, 5, 30, 8, 0, tzinfo=UTC),
        produced_by="operations/test-agent",
        payload={"status": "ok"},
    )

    class FakeRedis:
        async def xread(
            self, streams: dict[str, str], count: int, block: int
        ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
            assert streams == {"operations.test-event": "0-0"}
            assert count == 10
            assert block == 1
            return [("operations.test-event", [("1780127000001-0", env.to_redis_fields())])]

    subscriber = EventSubscriber(FakeRedis())  # type: ignore[arg-type]

    events = await subscriber.read(
        streams={"operations.test-event": "0-0"},
        count=10,
        block_ms=1,
    )

    assert len(events) == 1
    assert events[0].stream == "operations.test-event"
    assert events[0].redis_stream_id == "1780127000001-0"
    assert events[0].envelope == env


def test_normalize_redis_fields_decodes_bytes_from_fake_clients() -> None:
    from shrap.events import normalize_redis_fields

    fields = normalize_redis_fields(
        {
            b"h_event_id": b"01KSW33333333333333333333",
            "h_schema_version": "1.0.0",
            b"h_produced_at": b"2026-05-30T08:00:00+00:00",
            b"h_produced_by": b"operations/test-agent",
            b"payload": b'{"ok":true}',
        }
    )

    env = Envelope.from_redis_fields(fields)
    assert env.event_id == "01KSW33333333333333333333"
    assert env.payload == {"ok": True}
