"""Consumer for persisting paper execution order events."""

from __future__ import annotations

import asyncio
import signal
from typing import Any, Protocol, cast

import structlog
from redis.asyncio import Redis

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.events import EventSubscriber, ReceivedEvent
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


class RedisStreamClient(Protocol):
    async def xread(
        self,
        streams: dict[Any, Any],
        count: int | None = None,
        block: int | None = None,
    ) -> Any: ...


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
    redis: RedisStreamClient,
    sink: PaperOrderSink,
    last_ids: dict[str, str],
    start_id: str,
    count: int,
    block_ms: int,
) -> int:
    """Read execution order streams and persist one batch of events."""

    for stream in EXECUTION_STREAMS:
        last_ids.setdefault(stream, start_id)

    subscriber = EventSubscriber(redis)
    try:
        events = await subscriber.read(streams=last_ids, count=count, block_ms=block_ms)
    except Exception:
        log.exception("paper_order_store.read_failed", streams=dict(last_ids))
        return 0

    written = 0
    for event in events:
        try:
            await process_received_event(sink, event)
            last_ids[event.stream] = event.redis_stream_id
            written += 1
            log.info(
                "paper_order_store.event_persisted",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                event_id=event.envelope.event_id,
            )
        except Exception:
            log.exception(
                "paper_order_store.event_failed",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                event_id=event.envelope.event_id,
            )
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
) -> None:
    """Run the order-store consumer loop until ``stop`` is set."""

    last_ids: dict[str, str] = {}
    while not stop.is_set():
        try:
            written = await poll_once(
                redis=redis,
                sink=sink,
                last_ids=last_ids,
                start_id=start_id,
                count=count,
                block_ms=block_ms,
            )
            if written:
                log.info("paper_order_store.batch", written=written, last_ids=dict(last_ids))
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
) -> None:
    """Run the paper order-store consumer service until SIGINT/SIGTERM."""

    configure_logging(service_name, log_level)
    log.info("paper_order_store.starting", redis_url=redis_url, postgres_dsn="***")
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
        )
    finally:
        await redis.aclose()
        await pool.close()
        log.info("paper_order_store.stopped")


__all__ = [
    "EXECUTION_STREAMS",
    "poll_once",
    "process_received_event",
    "run",
    "run_loop",
]
