"""Market Phase Scheduler — service loop.

On startup: compute the current phase from the exchange calendar and publish
it immediately (``reason: "startup"``) so late-joining consumers have current
state without waiting for the next boundary. Loop: sleep until the next
boundary (capped at ``max_sleep_seconds``, so the schedule is recomputed at
least that often — half-day close times enter the schedule well before they
matter), then publish each transition that has come due, oldest first.

Failure semantics: if Redis is unreachable at a boundary, the publish retries
with capped exponential backoff and goes out late with the true boundary time
in the payload's ``effective_at``. Transitions are never skipped or
reordered.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, cast
from zoneinfo import ZoneInfo

import structlog
from redis.asyncio import Redis

from shrap.common.logging import configure_logging
from shrap.events import EventPublisher, RedisPublisher
from shrap.operations.market_phase import PhaseSchedule, Transition, build_schedule

if TYPE_CHECKING:
    from shrap.agents.operations.market_phase.config import Settings

log = structlog.get_logger(__name__)

STREAM_MARKET_PHASE = "operations.market-phase"
SCHEMA_VERSION = "1.0.0"

NowFn = Callable[[], datetime]


class Publisher(Protocol):
    async def publish(
        self,
        stream: str,
        produced_by: str,
        schema_version: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> object: ...


def compute_schedule(settings: Settings, now: datetime) -> PhaseSchedule:
    """Build the transition schedule around ``now`` (exchange-local window)."""
    local_today = now.astimezone(ZoneInfo(settings.timezone_name)).date()
    return build_schedule(
        local_today - timedelta(days=settings.lookbehind_days),
        local_today + timedelta(days=settings.lookahead_days),
        calendar_name=settings.calendar_name,
        timezone_name=settings.timezone_name,
        pre_open=settings.pre_open(),
        extended_end=settings.extended_end(),
    )


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass


async def _interruptible_sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def _publish_with_retry(
    publisher: Publisher,
    settings: Settings,
    schedule: PhaseSchedule,
    transition: Transition,
    reason: str,
    stop: asyncio.Event,
) -> bool:
    """Publish one phase event; retry with capped backoff until it lands.

    Returns False only if ``stop`` was set before the publish succeeded.
    """
    payload = schedule.payload_for(transition, reason)
    delay = settings.publish_retry_initial_seconds
    while not stop.is_set():
        try:
            if settings.dry_run:
                log.info("market_phase.publish.dry_run", **payload)
                return True
            await publisher.publish(
                stream=STREAM_MARKET_PHASE,
                produced_by=settings.produced_by(),
                schema_version=SCHEMA_VERSION,
                payload=payload,
            )
            log.info("market_phase.published", **payload)
            return True
        except Exception:
            log.exception("market_phase.publish_failed", retry_in_seconds=delay, **payload)
            await _interruptible_sleep(stop, delay)
            delay = min(delay * 2, settings.publish_retry_max_seconds)
    return False


async def run_loop(
    publisher: Publisher,
    settings: Settings,
    stop: asyncio.Event,
    now_fn: NowFn,
) -> None:
    """Service loop, factored so tests can inject publisher, clock, and stop."""

    now = now_fn()
    schedule = compute_schedule(settings, now)
    current = schedule.current(now)
    if not await _publish_with_retry(publisher, settings, schedule, current, "startup", stop):
        return
    last_at = current.at

    while not stop.is_set():
        now = now_fn()
        schedule = compute_schedule(settings, now)
        for transition in schedule.due(last_at, now):
            if not await _publish_with_retry(
                publisher, settings, schedule, transition, "transition", stop
            ):
                return
            last_at = transition.at
        upcoming = schedule.next_after(last_at)
        seconds = settings.max_sleep_seconds
        if upcoming is not None:
            seconds = min(seconds, max(0.0, (upcoming.at - now_fn()).total_seconds()))
        await _interruptible_sleep(stop, seconds)


async def run(settings: Settings) -> None:
    """Run the Market Phase Scheduler until SIGINT/SIGTERM."""

    configure_logging(settings.service_name, settings.log_level)
    log.info("market_phase.starting", **settings.redacted())
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=30)
    try:
        await run_loop(
            EventPublisher(cast(RedisPublisher, redis)),
            settings,
            stop,
            lambda: datetime.now(UTC),
        )
    finally:
        await redis.aclose()
        log.info("market_phase.stopped")
