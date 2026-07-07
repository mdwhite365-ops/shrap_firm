"""Public ADR-0006 event bus helpers.

This is the canonical package future agents should import for Redis Streams
publishing/subscribing. Older ``shrap.common`` modules remain compatibility
wrappers while callers migrate here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

import structlog

from shrap.common.envelope import MAX_INLINE_PAYLOAD_BYTES, Envelope, must_use_ref

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PublishedEvent:
    """Result of publishing one event to Redis Streams."""

    stream: str
    redis_stream_id: str
    envelope: Envelope


@dataclass(frozen=True, slots=True)
class ReceivedEvent:
    """Validated event read from Redis Streams."""

    stream: str
    redis_stream_id: str
    envelope: Envelope


class RedisPublisher(Protocol):
    async def xadd(self, stream: str, fields: dict[str, str]) -> str: ...


class RedisSubscriber(Protocol):
    async def xread(
        self,
        streams: dict[Any, Any],
        count: int | None = None,
        block: int | None = None,
    ) -> Any: ...


def _decode_redis_value(value: str | bytes) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def normalize_redis_fields(fields: Mapping[str | bytes, str | bytes]) -> dict[str, str]:
    """Normalize Redis field maps to strings.

    Real clients in this project use ``decode_responses=True``. Test fakes and
    fakeredis may still return bytes. Keep production parsing strict by
    normalizing at the event boundary before constructing an ``Envelope``.
    """

    return {_decode_redis_value(k): _decode_redis_value(v) for k, v in fields.items()}


class EventPublisher:
    """Publish ADR-0006 envelopes to Redis Streams."""

    def __init__(self, redis: RedisPublisher) -> None:
        self._redis = redis

    async def publish(
        self,
        stream: str,
        produced_by: str,
        schema_version: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> PublishedEvent:
        envelope = Envelope.new(
            produced_by=produced_by,
            schema_version=schema_version,
            payload=payload,
            correlation_id=correlation_id,
        )
        redis_stream_id = await self._redis.xadd(stream, envelope.to_redis_fields())
        return PublishedEvent(stream=stream, redis_stream_id=redis_stream_id, envelope=envelope)


class EventSubscriber:
    """Read and validate ADR-0006 envelopes from Redis Streams."""

    def __init__(self, redis: RedisSubscriber) -> None:
        self._redis = redis

    async def read(
        self,
        streams: dict[str, str],
        count: int = 100,
        block_ms: int = 5000,
    ) -> list[ReceivedEvent]:
        response = await self._redis.xread(
            streams=streams,
            count=count,
            block=block_ms,
        )
        events: list[ReceivedEvent] = []
        for stream, entries in response:
            for redis_stream_id, fields in entries:
                normalized = normalize_redis_fields(cast(dict[str | bytes, str | bytes], fields))
                try:
                    envelope = Envelope.from_redis_fields(normalized)
                except Exception:
                    # A malformed entry must not brick every consumer of the
                    # stream. Skip it loudly; ADR-0006 validation still holds
                    # for everything downstream.
                    log.warning(
                        "events.invalid_envelope_skipped",
                        stream=stream,
                        redis_stream_id=redis_stream_id,
                        fields=sorted(normalized),
                    )
                    continue
                events.append(
                    ReceivedEvent(
                        stream=stream,
                        redis_stream_id=redis_stream_id,
                        envelope=envelope,
                    )
                )
        return events


__all__ = [
    "MAX_INLINE_PAYLOAD_BYTES",
    "Envelope",
    "EventPublisher",
    "EventSubscriber",
    "PublishedEvent",
    "ReceivedEvent",
    "RedisSubscriber",
    "must_use_ref",
    "normalize_redis_fields",
]
