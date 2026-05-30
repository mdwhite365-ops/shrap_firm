"""Audit Logger main loop."""

from __future__ import annotations

import asyncio
import signal
from typing import cast

import structlog
from redis.asyncio import Redis

from shrap.agents.operations.audit_logger.config import Settings
from shrap.agents.operations.audit_logger.postgres import AsyncPool, PostgresAuditSink
from shrap.agents.operations.audit_logger.records import record_from_envelope
from shrap.common.logging import configure_logging
from shrap.events import EventSubscriber, ReceivedEvent, RedisSubscriber, normalize_redis_fields

log = structlog.get_logger(__name__)


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass


async def process_redis_entry(
    sink: PostgresAuditSink,
    stream_name: str,
    redis_stream_id: str,
    fields: dict[str, str],
) -> None:
    """Validate and persist a single Redis Stream entry."""
    from shrap.events import Envelope

    envelope = Envelope.from_redis_fields(
        normalize_redis_fields(cast(dict[str | bytes, str | bytes], fields))
    )
    record = record_from_envelope(stream_name, redis_stream_id, envelope)
    await sink.insert(record)


async def process_received_event(sink: PostgresAuditSink, event: ReceivedEvent) -> None:
    """Persist one event already validated by ``shrap.events``."""
    record = record_from_envelope(event.stream, event.redis_stream_id, event.envelope)
    await sink.insert(record)


async def poll_once(
    redis: Redis,
    sink: PostgresAuditSink,
    last_ids: dict[str, str],
    count: int,
    block_ms: int,
) -> int:
    """Read and persist one batch from configured streams.

    ``last_ids`` is updated after each entry so a long-running process tails forward.
    PostgreSQL uniqueness on ``event_id`` makes replay safe if the process restarts from
    the beginning during the early sprint.
    """
    subscriber = EventSubscriber(cast(RedisSubscriber, redis))
    events = await subscriber.read(streams=last_ids, count=count, block_ms=block_ms)
    if not events:
        return 0

    written = 0
    for event in events:
        try:
            await process_received_event(sink, event)
            written += 1
        except Exception:
            log.exception(
                "audit_logger.entry_failed",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
            )
        finally:
            last_ids[event.stream] = event.redis_stream_id
    return written


async def run(settings: Settings) -> None:
    """Run the Audit Logger forever until signalled."""
    configure_logging(settings.service_name, settings.log_level)
    log.info("audit_logger.starting", **settings.redacted())

    try:
        from psycopg_pool import AsyncConnectionPool
    except ImportError as e:  # pragma: no cover - runtime packaging guard
        raise RuntimeError(
            "Audit Logger requires the 'audit-logger' optional dependency group"
        ) from e

    stop = asyncio.Event()
    _install_signal_handlers(stop)
    redis: Redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=(settings.block_ms / 1000) + 10,
    )
    pool = AsyncConnectionPool(settings.postgres_dsn, open=False)
    await pool.open()
    sink = PostgresAuditSink(cast(AsyncPool, pool))
    await sink.ensure_schema()

    last_ids = dict.fromkeys(settings.stream_names(), "0-0")
    try:
        while not stop.is_set():
            written = await poll_once(
                redis,
                sink,
                last_ids,
                count=settings.read_count,
                block_ms=settings.block_ms,
            )
            if written:
                log.info("audit_logger.batch", written=written, last_ids=dict(last_ids))
    finally:
        await redis.aclose()
        await pool.close()
        log.info("audit_logger.stopped")
