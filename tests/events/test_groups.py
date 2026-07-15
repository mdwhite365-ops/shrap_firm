"""Tests for the consumer-group event subscriber (KI-006 proper fix)."""

from __future__ import annotations

from typing import Any, cast

import fakeredis.aioredis
import pytest

from shrap.events import EventPublisher
from shrap.events.groups import GroupEventSubscriber, RedisGroupClient

STREAM = "trading.decision.intent"


@pytest.fixture
def redis() -> Any:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


async def _publish(redis: Any, n: int, stream: str = STREAM) -> list[str]:
    publisher = EventPublisher(redis)
    event_ids = []
    for i in range(n):
        published = await publisher.publish(
            stream=stream,
            produced_by="test-producer",
            schema_version="1.0.0",
            payload={"seq": i},
        )
        event_ids.append(published.envelope.event_id)
    return event_ids


def _subscriber(
    redis: Any, start_id: str = "0", consumer: str | None = None
) -> GroupEventSubscriber:
    return GroupEventSubscriber(
        cast(RedisGroupClient, redis),
        group="test-service",
        consumer=consumer,
        start_id=start_id,
    )


@pytest.mark.asyncio
async def test_reads_new_events_and_defaults_consumer_to_group(redis: Any) -> None:
    event_ids = await _publish(redis, 2)
    subscriber = _subscriber(redis)

    events = await subscriber.read([STREAM], block_ms=1)

    assert [e.envelope.event_id for e in events] == event_ids
    assert subscriber.group == "test-service"
    assert subscriber.consumer == "test-service"


@pytest.mark.asyncio
async def test_acked_events_do_not_replay_after_restart(redis: Any) -> None:
    await _publish(redis, 2)
    subscriber = _subscriber(redis)
    events = await subscriber.read([STREAM], block_ms=1)
    for event in events:
        await subscriber.ack(event)

    # Simulate a restart: a fresh subscriber object, same group and consumer.
    restarted = _subscriber(redis)
    assert await restarted.read([STREAM], block_ms=1) == []


@pytest.mark.asyncio
async def test_unacked_events_are_redelivered_before_new_ones(redis: Any) -> None:
    first_ids = await _publish(redis, 2)
    subscriber = _subscriber(redis)
    events = await subscriber.read([STREAM], block_ms=1)
    # Ack only the first event; the second stays pending (systemic failure).
    await subscriber.ack(events[0])

    later_ids = await _publish(redis, 1)
    redelivered = await subscriber.read([STREAM], block_ms=1)

    # The pending entry retries first; the new event waits for the next cycle.
    assert [e.envelope.event_id for e in redelivered] == [first_ids[1]]

    await subscriber.ack(redelivered[0])
    fresh = await subscriber.read([STREAM], block_ms=1)
    assert [e.envelope.event_id for e in fresh] == later_ids


@pytest.mark.asyncio
async def test_malformed_entries_are_skipped_and_acked(redis: Any) -> None:
    await redis.xadd(STREAM, {"junk": "not-an-envelope"})
    valid_ids = await _publish(redis, 1)
    subscriber = _subscriber(redis)

    events = await subscriber.read([STREAM], block_ms=1)
    assert [e.envelope.event_id for e in events] == valid_ids
    await subscriber.ack(events[0])

    # The malformed entry must not linger in the pending list.
    restarted = _subscriber(redis)
    assert await restarted.read([STREAM], block_ms=1) == []


@pytest.mark.asyncio
async def test_start_id_dollar_skips_existing_history(redis: Any) -> None:
    await _publish(redis, 3)
    subscriber = _subscriber(redis, start_id="$")
    assert await subscriber.read([STREAM], block_ms=1) == []

    new_ids = await _publish(redis, 1)
    events = await subscriber.read([STREAM], block_ms=1)
    assert [e.envelope.event_id for e in events] == new_ids


@pytest.mark.asyncio
async def test_ensure_group_tolerates_existing_group(redis: Any) -> None:
    subscriber = _subscriber(redis)
    await subscriber.ensure_group(STREAM)

    # A second subscriber (fresh process) must not fail on BUSYGROUP.
    other = _subscriber(redis, consumer="other-consumer")
    await other.ensure_group(STREAM)

    await _publish(redis, 1)
    events = await other.read([STREAM], block_ms=1)
    assert len(events) == 1


@pytest.mark.asyncio
async def test_ensure_group_creates_missing_stream(redis: Any) -> None:
    subscriber = _subscriber(redis)
    # Stream does not exist yet; MKSTREAM must create it instead of raising.
    assert await subscriber.read(["operations.not-yet-published"], block_ms=1) == []
