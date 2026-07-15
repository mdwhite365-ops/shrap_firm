"""Decision Maker stub as a deployable service loop.

Wraps the Card 2 wire-stub logic (``trading.strategy.signal`` →
``trading.decision.intent``) in the same poll-loop discipline as the other
deployed consumers: malformed signals skip with the offset advanced,
systemic errors retry, low-confidence signals are skipped explicitly.

Reads through a Redis consumer group (KI-006), so offsets survive restarts.
Default ``start_id='$'``: the first time the group is created it only sees
NEW signals. Replaying historical signals into fresh intents would re-trade
them; the rate guardrails would catch it, but not creating the duplicates at
all is better.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any, Protocol, cast

import structlog
from redis.asyncio import Redis

from shrap.common.logging import configure_logging
from shrap.events import EventPublisher
from shrap.events.groups import GroupEventSubscriber, RedisGroupClient
from shrap.trading_floor.decision_maker_stub import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    PRODUCED_BY,
    SCHEMA_VERSION,
    STREAM_DECISION_INTENT,
    STREAM_STRATEGY_SIGNAL,
    build_stub_intent,
    should_emit_stub_intent,
)

log = structlog.get_logger(__name__)

CONSUMER_GROUP = "decision-maker"


class RedisStreamClient(Protocol):
    async def xadd(self, stream: str, fields: dict[str, str]) -> str: ...

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",
        mkstream: bool = False,
    ) -> Any: ...

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[Any, Any],
        count: int | None = None,
        block: int | None = None,
    ) -> Any: ...

    async def xack(self, name: str, groupname: str, *ids: str) -> Any: ...


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass


async def poll_once(
    redis: RedisStreamClient,
    subscriber: GroupEventSubscriber,
    count: int,
    block_ms: int,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    produced_by: str = PRODUCED_BY,
    retry_delay_seconds: float = 0.0,
) -> int:
    """Convert one batch of strategy signals into decision intents."""

    try:
        events = await subscriber.read(
            streams=[STREAM_STRATEGY_SIGNAL], count=count, block_ms=block_ms
        )
    except Exception:
        log.exception("decision_maker.read_failed", group=subscriber.group)
        await asyncio.sleep(retry_delay_seconds)
        return 0

    emitted = 0
    for event in events:
        try:
            signal_payload = event.envelope.payload
            if signal_payload is None or not should_emit_stub_intent(signal_payload, threshold):
                log.info(
                    "decision_maker.signal_skipped",
                    signal_event_id=event.envelope.event_id,
                    reason="no payload" if signal_payload is None else "below threshold",
                )
                await subscriber.ack(event)
                continue
            intent_payload = build_stub_intent(signal_payload, threshold)
            result = await EventPublisher(redis).publish(
                stream=STREAM_DECISION_INTENT,
                produced_by=produced_by,
                schema_version=SCHEMA_VERSION,
                payload=intent_payload,
                correlation_id=event.envelope.event_id,
            )
            await subscriber.ack(event)
            emitted += 1
            log.info(
                "decision_maker.intent_published",
                signal_event_id=event.envelope.event_id,
                intent_event_id=result.envelope.event_id,
            )
        except ValueError:
            log.exception(
                "decision_maker.signal_invalid_skipped",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                signal_event_id=event.envelope.event_id,
            )
            await subscriber.ack(event)
            continue
        except Exception:
            # Systemic error: no ack, so the same signal is redelivered next cycle.
            log.exception(
                "decision_maker.signal_failed",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                signal_event_id=event.envelope.event_id,
            )
            await asyncio.sleep(retry_delay_seconds)
            break
    return emitted


async def run_loop(
    redis: RedisStreamClient,
    stop: asyncio.Event,
    start_id: str = "$",
    count: int = 100,
    block_ms: int = 5000,
    retry_delay_seconds: float = 1.0,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    group: str = CONSUMER_GROUP,
    consumer: str | None = None,
) -> None:
    """Run the Decision Maker stub loop until ``stop`` is set."""

    subscriber = GroupEventSubscriber(
        cast(RedisGroupClient, redis),
        group=group,
        consumer=consumer,
        start_id=start_id,
    )
    while not stop.is_set():
        try:
            emitted = await poll_once(
                redis=redis,
                subscriber=subscriber,
                count=count,
                block_ms=block_ms,
                threshold=threshold,
                retry_delay_seconds=retry_delay_seconds,
            )
            if emitted:
                log.info("decision_maker.batch", emitted=emitted, group=group)
            else:
                await asyncio.sleep(0)
        except Exception:
            log.exception("decision_maker.poll_failed")
            await asyncio.sleep(retry_delay_seconds)


async def run(
    redis_url: str,
    service_name: str = "decision-maker",
    log_level: str = "INFO",
    start_id: str = "$",
    count: int = 100,
    block_ms: int = 5000,
    retry_delay_seconds: float = 1.0,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    group: str = CONSUMER_GROUP,
    consumer: str | None = None,
) -> None:
    """Run the Decision Maker stub service until SIGINT/SIGTERM."""

    configure_logging(service_name, log_level)
    log.info(
        "decision_maker.starting",
        redis_url=redis_url,
        start_id=start_id,
        threshold=threshold,
        group=group,
        consumer=consumer or group,
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    redis: Redis = Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_timeout=(block_ms / 1000) + 10,
    )
    try:
        await run_loop(
            cast(RedisStreamClient, redis),
            stop=stop,
            start_id=start_id,
            count=count,
            block_ms=block_ms,
            retry_delay_seconds=retry_delay_seconds,
            threshold=threshold,
            group=group,
            consumer=consumer,
        )
    finally:
        await redis.aclose()
        log.info("decision_maker.stopped")


__all__ = ["CONSUMER_GROUP", "poll_once", "run", "run_loop"]
