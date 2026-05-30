"""Thin async wrapper around redis.asyncio for Streams (ADR-0001)."""

from __future__ import annotations

from typing import Any, cast

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from shrap.common.envelope import Envelope


class RedisStreamClient:
    """Minimal Streams wrapper. One client per service is fine; it's async."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._redis: Redis = Redis.from_url(url, decode_responses=True)
        self._known_groups: set[tuple[str, str]] = set()

    async def close(self) -> None:
        await self._redis.aclose()

    async def xadd(self, stream: str, envelope: Envelope) -> str:
        """XADD an envelope; returns the Redis-generated stream ID."""
        fields = envelope.to_redis_fields()
        stream_id = await self._redis.xadd(stream, cast(dict[Any, Any], fields))
        return cast(str, stream_id)

    async def _ensure_group(self, stream: str, group: str) -> None:
        key = (stream, group)
        if key in self._known_groups:
            return
        try:
            await self._redis.xgroup_create(name=stream, groupname=group, id="$", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
        self._known_groups.add(key)

    async def xread_group(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int = 10,
        block_ms: int = 5000,
    ) -> list[tuple[str, Envelope]]:
        """XREADGROUP; auto-creates the group on first call. Returns [(stream_id, Envelope)]."""
        await self._ensure_group(stream, group)
        resp = await self._redis.xreadgroup(
            groupname=group,
            consumername=consumer,
            streams={stream: ">"},
            count=count,
            block=block_ms,
        )
        results: list[tuple[str, Envelope]] = []
        if not resp:
            return results
        for _stream_name, entries in cast(list[tuple[str, list[tuple[str, dict[str, str]]]]], resp):
            for stream_id, fields in entries:
                results.append((stream_id, Envelope.from_redis_fields(fields)))
        return results
