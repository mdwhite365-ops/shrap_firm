"""Tests for the paper order event persistence consumer."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from shrap.events import EventPublisher
from shrap.trading_floor.order_store import PaperOrderRecord


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.reads: list[dict[str, str]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        return f"178012860000{len(self.calls)}-0"

    async def xread(
        self,
        streams: dict[Any, Any],
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        self.reads.append({str(key): str(value) for key, value in streams.items()})
        response: list[tuple[str, list[tuple[str, dict[str, str]]]]] = []
        for stream, last_id in streams.items():
            entries: list[tuple[str, dict[str, str]]] = []
            for index, (written_stream, fields) in enumerate(self.calls, start=1):
                redis_id = f"178012860000{index}-0"
                if written_stream == stream and self._after(redis_id, str(last_id)):
                    entries.append((redis_id, fields))
            if entries:
                response.append((str(stream), entries[: count or len(entries)]))
        return response

    @staticmethod
    def _after(redis_id: str, last_id: str) -> bool:
        if last_id == "$":
            return False
        if last_id == "0" or last_id == "0-0":
            return True
        return redis_id > last_id


class FakeSink:
    def __init__(self) -> None:
        self.records: list[PaperOrderRecord] = []

    async def upsert(self, record: PaperOrderRecord) -> None:
        self.records.append(record)


@pytest.mark.asyncio
async def test_poll_once_persists_execution_order_streams_and_advances_offsets() -> None:
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
    last_ids: dict[str, str] = {}

    written = await poll_once(
        redis=redis,  # type: ignore[arg-type]
        sink=sink,  # type: ignore[arg-type]
        last_ids=last_ids,
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert written == 3
    assert sorted(last_ids) == sorted(EXECUTION_STREAMS)
    assert last_ids == {
        "execution.order.submitted": "1780128600001-0",
        "execution.order.status-updated": "1780128600002-0",
        "execution.order.filled": "1780128600003-0",
    }
    assert [record.event_topic for record in sink.records] == [  # type: ignore[attr-defined]
        "execution.order.submitted",
        "execution.order.status-updated",
        "execution.order.filled",
    ]


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
            start_id="0-0",
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
    last_ids: dict[str, str] = {}

    written = await poll_once(
        redis=redis,  # type: ignore[arg-type]
        sink=sink,  # type: ignore[arg-type]
        last_ids=last_ids,
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert written == 1
    assert [record.broker_order_id for record in sink.records] == ["order-2"]
    # Offset advanced past BOTH events - the poison one was skipped, not retried.
    assert last_ids["execution.order.submitted"] == "1780128600002-0"


@pytest.mark.asyncio
async def test_poll_once_retries_on_sink_failure_without_advancing() -> None:
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

    last_ids: dict[str, str] = {}
    written = await poll_once(
        redis=redis,  # type: ignore[arg-type]
        sink=FailingSink(),  # type: ignore[arg-type]
        last_ids=last_ids,
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert written == 0
    assert last_ids["execution.order.submitted"] == "0-0"
