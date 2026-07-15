"""Tests for the paper order event persistence consumer."""

from __future__ import annotations

import asyncio
from typing import Any

import fakeredis.aioredis
import pytest

from shrap.events import EventPublisher
from shrap.events.groups import GroupEventSubscriber
from shrap.trading_floor.order_store import PaperOrderRecord


class FakeRedis:
    """fakeredis transport with recorded ``xadd`` calls for assertions."""

    def __init__(self) -> None:
        self._real = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        return await self._real.xadd(stream, fields)

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",
        mkstream: bool = False,
    ) -> Any:
        return await self._real.xgroup_create(name, groupname, id=id, mkstream=mkstream)

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[Any, Any],
        count: int | None = None,
        block: int | None = None,
    ) -> Any:
        return await self._real.xreadgroup(
            groupname, consumername, streams, count=count, block=block
        )

    async def xack(self, name: str, groupname: str, *ids: str) -> Any:
        return await self._real.xack(name, groupname, *ids)


def subscriber_for(redis: FakeRedis) -> GroupEventSubscriber:
    return GroupEventSubscriber(
        redis,  # type: ignore[arg-type]
        group="paper-order-store",
        start_id="0",
    )


class FakeSink:
    def __init__(self) -> None:
        self.records: list[PaperOrderRecord] = []

    async def upsert(self, record: PaperOrderRecord) -> None:
        self.records.append(record)


@pytest.mark.asyncio
async def test_poll_once_persists_execution_order_streams_and_acks() -> None:
    from shrap.trading_floor.order_store_agent import EXECUTION_STREAMS, poll_once

    redis = FakeRedis()
    publisher = EventPublisher(redis)
    await publisher.publish(
        stream="execution.order.submitted",
        produced_by="trading-floor/execution-agent",
        schema_version="1.0.0",
        payload={
            "broker": "alpaca-paper",
            "broker_order_id": "order-1",
            "status": "accepted",
            "submitted_order": {"symbol": "AAPL", "qty": "1", "side": "buy"},
            "broker_response": {"id": "order-1", "status": "accepted"},
        },
    )
    await publisher.publish(
        stream="execution.order.status-updated",
        produced_by="trading-floor/execution-agent",
        schema_version="1.0.0",
        payload={
            "broker": "alpaca-paper",
            "broker_order_id": "order-1",
            "status": "accepted",
            "filled_qty": "0",
            "broker_response": {"id": "order-1", "status": "accepted"},
        },
    )
    await publisher.publish(
        stream="execution.order.filled",
        produced_by="trading-floor/execution-agent",
        schema_version="1.0.0",
        payload={
            "broker": "alpaca-paper",
            "broker_order_id": "order-1",
            "status": "filled",
            "filled_qty": "1",
            "filled_avg_price": "185.25",
            "broker_response": {
                "id": "order-1",
                "symbol": "AAPL",
                "qty": "1",
                "side": "buy",
                "status": "filled",
            },
        },
    )
    sink = FakeSink()

    written = await poll_once(
        sink=sink,  # type: ignore[arg-type]
        subscriber=subscriber_for(redis),
        count=10,
        block_ms=1,
    )

    assert written == 3
    assert sorted({record.event_topic for record in sink.records}) == sorted(EXECUTION_STREAMS)  # type: ignore[attr-defined]
    # All three acked: a restarted consumer re-persists nothing (KI-006).
    assert await subscriber_for(redis).read(list(EXECUTION_STREAMS), block_ms=1) == []


@pytest.mark.asyncio
async def test_run_loop_exits_cleanly_when_stop_signal_is_set() -> None:
    from shrap.trading_floor.order_store_agent import run_loop

    redis = FakeRedis()
    sink = FakeSink()
    stop = asyncio.Event()

    async def stop_soon() -> None:
        await asyncio.sleep(0)
        stop.set()

    await asyncio.gather(
        run_loop(
            redis,  # type: ignore[arg-type]
            sink,  # type: ignore[arg-type]
            stop=stop,
            start_id="0",
            count=10,
            block_ms=1,
            retry_delay_seconds=0,
        ),
        stop_soon(),
    )

    assert stop.is_set()


@pytest.mark.asyncio
async def test_poll_once_skips_malformed_event_and_persists_the_rest() -> None:
    from shrap.trading_floor.order_store_agent import poll_once

    redis = FakeRedis()
    publisher = EventPublisher(redis)
    await publisher.publish(
        stream="execution.order.submitted",
        produced_by="trading-floor/execution-agent",
        schema_version="1.0.0",
        payload={"broker": "alpaca-paper"},  # missing broker_order_id -> ValueError
    )
    await publisher.publish(
        stream="execution.order.submitted",
        produced_by="trading-floor/execution-agent",
        schema_version="1.0.0",
        payload={"broker": "alpaca-paper", "broker_order_id": "order-2", "status": "accepted"},
    )
    sink = FakeSink()

    written = await poll_once(
        sink=sink,  # type: ignore[arg-type]
        subscriber=subscriber_for(redis),
        count=10,
        block_ms=1,
    )

    assert written == 1
    assert [record.broker_order_id for record in sink.records] == ["order-2"]
    # Both acked — the poison one was skipped, not left pending for retry.
    assert await subscriber_for(redis).read(["execution.order.submitted"], block_ms=1) == []


@pytest.mark.asyncio
async def test_poll_once_retries_on_sink_failure_without_acking() -> None:
    from shrap.trading_floor.order_store_agent import poll_once

    redis = FakeRedis()
    await EventPublisher(redis).publish(
        stream="execution.order.submitted",
        produced_by="trading-floor/execution-agent",
        schema_version="1.0.0",
        payload={"broker": "alpaca-paper", "broker_order_id": "order-1", "status": "accepted"},
    )

    class FailingSink:
        async def upsert(self, record: PaperOrderRecord) -> None:
            raise RuntimeError("database unreachable")

    written = await poll_once(
        sink=FailingSink(),  # type: ignore[arg-type]
        subscriber=subscriber_for(redis),
        count=10,
        block_ms=1,
    )

    assert written == 0
    # Not acked: redelivered next cycle so the outage loses nothing.
    redelivered = await subscriber_for(redis).read(["execution.order.submitted"], block_ms=1)
    assert len(redelivered) == 1
