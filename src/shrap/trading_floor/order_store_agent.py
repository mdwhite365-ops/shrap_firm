"""Consumer for persisting paper execution order events."""

from __future__ import annotations

import asyncio
import signal
from typing import Any, Protocol, cast

import structlog
from redis.asyncio import Redis

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.events import ReceivedEvent
from shrap.events.groups import GroupEventSubscriber
from shrap.trading_floor.order_store import (
    PaperOrderRecord,
    PostgresPaperOrderSink,
    record_from_execution_event,
)

log = structlog.get_logger(__name__)

EXECUTION_STREAMS = (
    "execution.order.submitted",
    "execution.order.status-updated",
    "execution.order.filled",
)
CONSUMER_GROUP = "paper-order-store"


class RedisStreamClient(Protocol):
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


class PaperOrderSink(Protocol):
    async def upsert(self, record: PaperOrderRecord) -> None: ...


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass


async def process_received_event(sink: PaperOrderSink, event: ReceivedEvent) -> None:
    """Map and persist one execution order event."""

    record = record_from_execution_event(event)
    await sink.upsert(record)


async def poll_once(
    sink: PaperOrderSink,
    subscriber: GroupEventSubscriber,
    count: int,
    block_ms: int,
    retry_delay_seconds: float = 0.0,
) -> int:
    """Read execution order streams and persist one batch of events."""

    try:
        events = await subscriber.read(
            streams=list(EXECUTION_STREAMS), count=count, block_ms=block_ms
        )
    except Exception:
        log.exception("paper_order_store.read_failed", group=subscriber.group)
        await asyncio.sleep(retry_delay_seconds)
        return 0

    written = 0
    for event in events:
        try:
            await process_received_event(sink, event)
            await subscriber.ack(event)
            written += 1
            log.info(
                "paper_order_store.event_persisted",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                event_id=event.envelope.event_id,
            )
        except ValueError:
            # Malformed event: permanent for this event. Ack and skip it or
            # the consumer stalls forever on a poison message (same pattern
            # as the Execution Agent fix, 2026-07-06).
            log.exception(
                "paper_order_store.event_invalid_skipped",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                event_id=event.envelope.event_id,
            )
            await subscriber.ack(event)
            continue
        except Exception:
            # Systemic error (database down): no ack, so the same event is
            # redelivered next cycle.
            log.exception(
                "paper_order_store.event_failed",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                event_id=event.envelope.event_id,
            )
            await asyncio.sleep(retry_delay_seconds)
            break
    return written


async def run_loop(
    redis: RedisStreamClient,
    sink: PaperOrderSink,
    stop: asyncio.Event,
    start_id: str = "0-0",
    count: int = 100,
    block_ms: int = 5000,
    retry_delay_seconds: float = 1.0,
    group: str = CONSUMER_GROUP,
    consumer: str | None = None,
) -> None:
    """Run the order-store consumer loop until ``stop`` is set.

    Offsets persist in the ``group`` consumer group (KI-006); ``start_id``
    only positions the group the first time it is created on each stream.
    """

    subscriber = GroupEventSubscriber(
        redis,
        group=group,
        consumer=consumer,
        start_id=start_id,
    )
    while not stop.is_set():
        try:
            written = await poll_once(
                sink=sink,
                subscriber=subscriber,
                count=count,
                block_ms=block_ms,
                retry_delay_seconds=retry_delay_seconds,
            )
            if written:
                log.info("paper_order_store.batch", written=written, group=group)
            else:
                await asyncio.sleep(0)
        except Exception:
            log.exception("paper_order_store.poll_failed")
            await asyncio.sleep(retry_delay_seconds)


async def run(
    redis_url: str,
    postgres_dsn: str,
    service_name: str = "paper-order-store",
    log_level: str = "INFO",
    start_id: str = "0-0",
    count: int = 100,
    block_ms: int = 5000,
    retry_delay_seconds: float = 1.0,
    group: str = CONSUMER_GROUP,
    consumer: str | None = None,
) -> None:
    """Run the paper order-store consumer service until SIGINT/SIGTERM."""

    configure_logging(service_name, log_level)
    log.info(
        "paper_order_store.starting",
        redis_url=redis_url,
        postgres_dsn="***",
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
    pool = await create_asyncpg_pool(postgres_dsn)
    sink = PostgresPaperOrderSink(pool)
    await sink.ensure_schema()
    try:
        await run_loop(
            cast(RedisStreamClient, redis),
            sink=sink,
            stop=stop,
            start_id=start_id,
            count=count,
            block_ms=block_ms,
            retry_delay_seconds=retry_delay_seconds,
            group=group,
            consumer=consumer,
        )
    finally:
        await redis.aclose()
        await pool.close()
        log.info("paper_order_store.stopped")


__all__ = [
    "CONSUMER_GROUP",
    "EXECUTION_STREAMS",
    "poll_once",
    "process_received_event",
    "run",
    "run_loop",
]
