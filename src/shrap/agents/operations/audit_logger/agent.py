"""Audit Logger main loop."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import AsyncIterator
from typing import Any, Protocol, cast

import structlog
from redis.asyncio import Redis

from shrap.agents.operations.audit_logger.config import Settings
from shrap.agents.operations.audit_logger.db import PostgresAuditSink
from shrap.agents.operations.audit_logger.records import record_from_envelope
from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.events import ReceivedEvent
from shrap.events.groups import GroupEventSubscriber, RedisGroupClient

log = structlog.get_logger(__name__)


class StreamRedis(Protocol):
    def scan_iter(self, match: str) -> AsyncIterator[str | bytes]: ...

    async def type(self, key: str) -> str | bytes: ...

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


def _decode(value: str | bytes) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass


async def discover_streams(redis: StreamRedis, pattern: str) -> list[str]:
    """Return Redis keys matching ``pattern`` whose type is ``stream``."""

    streams: list[str] = []
    async for raw_key in redis.scan_iter(match=pattern):
        key = _decode(raw_key)
        key_type = _decode(await redis.type(key))
        if key_type == "stream":
            streams.append(key)
    return sorted(streams)


async def process_received_event(sink: PostgresAuditSink, event: ReceivedEvent) -> None:
    """Persist one event already validated by ``shrap.events``."""
    record = record_from_envelope(event.stream, event.redis_stream_id, event.envelope)
    await sink.insert(record)


async def poll_once(
    redis: StreamRedis,
    sink: PostgresAuditSink,
    subscriber: GroupEventSubscriber,
    stream_pattern: str,
    count: int,
    block_ms: int,
) -> int:
    """Discover streams, read one batch, and persist validated envelopes.

    The audit trail must never stall the bus: every event is acknowledged,
    processed or not. A failed insert is logged and dropped, exactly as the
    pre-group logger advanced its offset regardless of outcome.
    """

    streams = await discover_streams(redis, stream_pattern)
    if not streams:
        return 0

    events = await subscriber.read(streams=streams, count=count, block_ms=block_ms)
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
            await subscriber.ack(event)
    return written


async def run(settings: Settings) -> None:
    """Run the Audit Logger forever until signalled."""
    configure_logging(settings.service_name, settings.log_level)
    log.info("audit_logger.starting", **settings.redacted())

    stop = asyncio.Event()
    _install_signal_handlers(stop)
    redis: Redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=(settings.block_ms / 1000) + 10,
    )
    pool = await create_asyncpg_pool(settings.postgres_dsn)
    sink = PostgresAuditSink(pool)
    await sink.ensure_schema()

    subscriber = GroupEventSubscriber(
        cast(RedisGroupClient, redis),
        group=settings.service_name,
        consumer=settings.instance_id,
        start_id=settings.start_id,
    )
    try:
        while not stop.is_set():
            try:
                written = await poll_once(
                    cast(StreamRedis, redis),
                    sink,
                    subscriber,
                    stream_pattern=settings.stream_pattern,
                    count=settings.read_count,
                    block_ms=settings.block_ms,
                )
                if written:
                    log.info("audit_logger.batch", written=written, group=subscriber.group)
            except Exception:
                log.exception("audit_logger.poll_failed")
                await asyncio.sleep(settings.retry_delay_seconds)
    finally:
        await redis.aclose()
        await pool.close()
        log.info("audit_logger.stopped")
