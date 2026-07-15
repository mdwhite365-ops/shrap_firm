"""Consumer-group subscription for ADR-0006 events (KI-006 proper fix).

``GroupEventSubscriber`` replaces the XREAD + in-memory ``last_ids`` pattern.
Offsets live in Redis itself: the group's last-delivered ID advances as events
are delivered, and each event stays in the consumer's pending list until the
service acknowledges it. A restart therefore resumes where the group left off
instead of replaying the full stream history.

Delivery contract (mirrors the poison-event discipline from PR #27/#32):

- **Success** → the consumer calls :meth:`GroupEventSubscriber.ack`.
- **Malformed entry** (cannot even build an ``Envelope``) → skipped and acked
  inside :meth:`read`; permanent failures must not clog the pending list.
- **Poison event** (valid envelope, permanently unprocessable) → the consumer
  acks and skips, exactly as it advanced ``last_ids`` before.
- **Systemic error** (broker/DB down) → the consumer does NOT ack; the event
  stays pending and is redelivered first on the next cycle, because ``read``
  drains pending entries before asking for new ones.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, cast

import structlog

from shrap.common.envelope import Envelope
from shrap.events import ReceivedEvent, normalize_redis_fields

log = structlog.get_logger(__name__)

NEW_EVENTS_ID = ">"
PENDING_EVENTS_ID = "0"


class RedisGroupClient(Protocol):
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


class GroupEventSubscriber:
    """Read and validate ADR-0006 envelopes through a Redis consumer group.

    One group per service, one consumer per running instance. The deployed
    services are single-instance, so both default to the service name.
    ``start_id`` only matters the first time a group is created on a stream:
    ``"0"`` processes existing history once, ``"$"`` starts from new events.
    """

    def __init__(
        self,
        redis: RedisGroupClient,
        group: str,
        consumer: str | None = None,
        start_id: str = "0",
    ) -> None:
        self._redis = redis
        self._group = group
        self._consumer = consumer or group
        self._start_id = start_id
        self._ensured: set[str] = set()

    @property
    def group(self) -> str:
        return self._group

    @property
    def consumer(self) -> str:
        return self._consumer

    async def ensure_group(self, stream: str) -> None:
        """Create the group on ``stream`` if it does not exist yet."""

        if stream in self._ensured:
            return
        try:
            await self._redis.xgroup_create(
                stream,
                self._group,
                id=self._start_id,
                mkstream=True,
            )
            log.info(
                "events.group_created",
                stream=stream,
                group=self._group,
                start_id=self._start_id,
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._ensured.add(stream)

    async def read(
        self,
        streams: Sequence[str],
        count: int = 100,
        block_ms: int = 5000,
    ) -> list[ReceivedEvent]:
        """Read one batch, pending (unacknowledged) entries first.

        Draining the pending list before asking for new events is what makes
        the no-ack-on-systemic-error contract work: a failed event is retried
        in order on the very next cycle, not only after a restart.
        """

        for stream in streams:
            await self.ensure_group(stream)

        events = await self._read_with_id(streams, PENDING_EVENTS_ID, count=count, block=None)
        if not events:
            events = await self._read_with_id(streams, NEW_EVENTS_ID, count=count, block=block_ms)
        return events

    async def ack(self, event: ReceivedEvent) -> None:
        """Acknowledge one processed (or permanently skipped) event."""

        await self._redis.xack(event.stream, self._group, event.redis_stream_id)

    async def _read_with_id(
        self,
        streams: Sequence[str],
        read_id: str,
        count: int,
        block: int | None,
    ) -> list[ReceivedEvent]:
        response = await self._redis.xreadgroup(
            self._group,
            self._consumer,
            streams=dict.fromkeys(streams, read_id),
            count=count,
            block=block,
        )
        events: list[ReceivedEvent] = []
        for stream_name, entries in response or []:
            stream = _decode(stream_name)
            for entry_id, fields in entries:
                redis_stream_id = _decode(entry_id)
                if fields is None:
                    # A pending entry whose stream entry was trimmed/deleted:
                    # nothing left to process, drop it from the pending list.
                    await self._redis.xack(stream, self._group, redis_stream_id)
                    log.warning(
                        "events.trimmed_pending_acked",
                        stream=stream,
                        group=self._group,
                        redis_stream_id=redis_stream_id,
                    )
                    continue
                normalized = normalize_redis_fields(cast(dict[str | bytes, str | bytes], fields))
                try:
                    envelope = Envelope.from_redis_fields(normalized)
                except Exception:
                    # Malformed entry: permanent, so ack it here or it would
                    # be redelivered from the pending list forever.
                    await self._redis.xack(stream, self._group, redis_stream_id)
                    log.warning(
                        "events.invalid_envelope_skipped",
                        stream=stream,
                        group=self._group,
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


def _decode(value: str | bytes) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


__all__ = [
    "GroupEventSubscriber",
    "RedisGroupClient",
]
