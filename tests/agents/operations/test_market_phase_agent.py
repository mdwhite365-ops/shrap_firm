"""Tests for the Market Phase Scheduler service loop."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time
from typing import Any

from shrap.agents.operations.market_phase.agent import (
    SCHEMA_VERSION,
    STREAM_MARKET_PHASE,
    _publish_with_retry,
    compute_schedule,
    run_loop,
)
from shrap.agents.operations.market_phase.config import Settings
from shrap.common.envelope import Envelope
from shrap.events import EventPublisher, RedisPublisher


class FakeRedis:
    """Records xadd calls; optionally fires a callback per call."""

    def __init__(self, on_call: Any = None) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self._on_call = on_call

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        if self._on_call is not None:
            self._on_call()
        return f"{len(self.calls)}-0"


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


async def test_startup_publish_mid_session() -> None:
    stop = asyncio.Event()
    fake = FakeRedis(on_call=stop.set)
    settings = Settings()
    now = datetime(2026, 7, 15, 15, 0, tzinfo=UTC)  # Wednesday, mid-session

    redis: RedisPublisher = fake
    await run_loop(EventPublisher(redis), settings, stop, lambda: now)

    assert len(fake.calls) == 1
    stream, fields = fake.calls[0]
    assert stream == STREAM_MARKET_PHASE
    env = Envelope.from_redis_fields(fields)
    assert env.schema_version == SCHEMA_VERSION
    assert env.produced_by == settings.produced_by()
    assert env.payload is not None
    assert env.payload["phase"] == "open"
    assert env.payload["reason"] == "startup"
    assert env.payload["session_date"] == "2026-07-15"
    assert env.payload["effective_at"] == "2026-07-15T13:30:00+00:00"
    assert env.payload["next_phase"] == "after-hours"


async def test_startup_publish_mid_overnight() -> None:
    stop = asyncio.Event()
    fake = FakeRedis(on_call=stop.set)
    now = datetime(2026, 7, 16, 2, 0, tzinfo=UTC)

    redis: RedisPublisher = fake
    await run_loop(EventPublisher(redis), Settings(), stop, lambda: now)

    env = Envelope.from_redis_fields(fake.calls[0][1])
    assert env.payload is not None
    assert env.payload["phase"] == "overnight"
    assert env.payload["session_date"] == "2026-07-16"


async def test_catchup_publishes_missed_transitions_in_order() -> None:
    stop = asyncio.Event()
    clock = Clock(datetime(2026, 7, 15, 8, 30, tzinfo=UTC))  # mid pre-open
    published: list[dict[str, Any]] = []

    class RecordingPublisher:
        async def publish(
            self,
            stream: str,
            produced_by: str,
            schema_version: str,
            payload: dict[str, Any],
            correlation_id: str | None = None,
        ) -> object:
            published.append(payload)
            if payload["reason"] == "startup":
                # Jump past the open and the close while the service "slept".
                clock.value = datetime(2026, 7, 15, 21, 0, tzinfo=UTC)
            if len(published) == 3:
                stop.set()
            return object()

    await run_loop(RecordingPublisher(), Settings(), stop, clock)

    assert [p["phase"] for p in published] == ["pre-open", "open", "after-hours"]
    assert [p["reason"] for p in published] == ["startup", "transition", "transition"]
    # Late publishes carry the true boundary time.
    assert published[1]["effective_at"] == "2026-07-15T13:30:00+00:00"
    assert published[2]["effective_at"] == "2026-07-15T20:00:00+00:00"


async def test_publish_retries_until_success() -> None:
    stop = asyncio.Event()
    attempts: list[str] = []

    class FlakyPublisher:
        async def publish(
            self,
            stream: str,
            produced_by: str,
            schema_version: str,
            payload: dict[str, Any],
            correlation_id: str | None = None,
        ) -> object:
            attempts.append(payload["phase"])
            if len(attempts) == 1:
                raise RuntimeError("redis unreachable")
            return object()

    settings = Settings(publish_retry_initial_seconds=0.01)
    now = datetime(2026, 7, 15, 15, 0, tzinfo=UTC)
    schedule = compute_schedule(settings, now)
    transition = schedule.current(now)

    ok = await _publish_with_retry(
        FlakyPublisher(), settings, schedule, transition, "transition", stop
    )
    assert ok
    assert len(attempts) == 2


async def test_publish_gives_up_only_on_stop() -> None:
    stop = asyncio.Event()
    attempts: list[int] = []

    class DownPublisher:
        async def publish(
            self,
            stream: str,
            produced_by: str,
            schema_version: str,
            payload: dict[str, Any],
            correlation_id: str | None = None,
        ) -> object:
            attempts.append(1)
            if len(attempts) >= 2:
                stop.set()
            raise RuntimeError("redis unreachable")

    settings = Settings(publish_retry_initial_seconds=0.01)
    now = datetime(2026, 7, 15, 15, 0, tzinfo=UTC)
    schedule = compute_schedule(settings, now)
    transition = schedule.current(now)

    ok = await _publish_with_retry(
        DownPublisher(), settings, schedule, transition, "transition", stop
    )
    assert not ok
    assert len(attempts) == 2


def test_settings_parse_and_redact() -> None:
    settings = Settings()
    assert settings.pre_open() == time(4, 0)
    assert settings.extended_end() == time(20, 0)
    assert settings.produced_by().startswith("market-phase@")
    redacted = settings.redacted()
    assert redacted["calendar_name"] == "XNYS"
    assert redacted["timezone_name"] == "America/New_York"
