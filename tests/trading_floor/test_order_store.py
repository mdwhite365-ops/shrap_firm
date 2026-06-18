"""Tests for paper order/fill persistence records."""

from __future__ import annotations

from typing import Any

import pytest

from shrap.events import EventPublisher, ReceivedEvent


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        return f"178012850000{len(self.calls)}-0"


def as_received(published: Any) -> ReceivedEvent:
    return ReceivedEvent(
        stream=published.stream,
        redis_stream_id=published.redis_stream_id,
        envelope=published.envelope,
    )


@pytest.mark.asyncio
async def test_order_record_maps_execution_order_submitted_event() -> None:
    from shrap.trading_floor.order_store import PaperOrderRecord, record_from_execution_event

    redis = FakeRedis()
    event = await EventPublisher(redis).publish(
        stream="execution.order.submitted",
        produced_by="trading-floor/execution-agent",
        schema_version="1.0.0",
        payload={
            "risk_event_id": "risk-event-1",
            "broker": "alpaca-paper",
            "broker_order_id": "paper-order-1",
            "status": "accepted",
            "submitted_order": {
                "symbol": "AAPL",
                "qty": "1",
                "side": "buy",
                "type": "market",
                "time_in_force": "day",
                "client_order_id": "risk-event-1",
            },
            "broker_response": {"id": "paper-order-1", "status": "accepted"},
        },
        correlation_id="risk-event-1",
    )

    record = record_from_execution_event(as_received(event))

    assert record == PaperOrderRecord(
        event_id=event.envelope.event_id,
        event_topic="execution.order.submitted",
        redis_stream_id="1780128500001-0",
        correlation_id="risk-event-1",
        broker="alpaca-paper",
        broker_order_id="paper-order-1",
        status="accepted",
        symbol="AAPL",
        side="buy",
        quantity="1",
        filled_quantity=None,
        filled_avg_price=None,
        submitted_order={
            "symbol": "AAPL",
            "qty": "1",
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "client_order_id": "risk-event-1",
        },
        broker_response={"id": "paper-order-1", "status": "accepted"},
        payload_json=event.envelope.payload,
        occurred_at=event.envelope.produced_at,
    )


@pytest.mark.asyncio
async def test_order_record_maps_execution_order_filled_event() -> None:
    from shrap.trading_floor.order_store import record_from_execution_event

    redis = FakeRedis()
    event = await EventPublisher(redis).publish(
        stream="execution.order.filled",
        produced_by="trading-floor/execution-agent",
        schema_version="1.0.0",
        payload={
            "submitted_event_id": "submitted-event-1",
            "broker": "alpaca-paper",
            "broker_order_id": "paper-order-1",
            "status": "filled",
            "filled_qty": "1",
            "filled_avg_price": "185.25",
            "filled_at": "2026-06-17T18:45:00Z",
            "submitted_payload": {"submitted_order": {"symbol": "AAPL", "qty": "1", "side": "buy"}},
            "broker_response": {
                "id": "paper-order-1",
                "symbol": "AAPL",
                "qty": "1",
                "side": "buy",
                "status": "filled",
                "filled_qty": "1",
                "filled_avg_price": "185.25",
            },
        },
        correlation_id="submitted-event-1",
    )

    record = record_from_execution_event(as_received(event))

    assert record.event_topic == "execution.order.filled"
    assert record.broker_order_id == "paper-order-1"
    assert record.status == "filled"
    assert record.symbol == "AAPL"
    assert record.side == "buy"
    assert record.quantity == "1"
    assert record.filled_quantity == "1"
    assert record.filled_avg_price == "185.25"


@pytest.mark.asyncio
async def test_postgres_order_sink_ensures_schema_and_upserts_record() -> None:
    from shrap.trading_floor.order_store import PaperOrderRecord, PostgresPaperOrderSink

    class FakeConn:
        def __init__(self) -> None:
            self.executed: list[tuple[str, tuple[object, ...]]] = []

        async def execute(self, sql: str, *args: object) -> object:
            self.executed.append((sql, args))
            return "OK"

    class FakeAcquire:
        def __init__(self, conn: FakeConn) -> None:
            self._conn = conn

        async def __aenter__(self) -> FakeConn:
            return self._conn

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    class FakePool:
        def __init__(self) -> None:
            self.conn = FakeConn()

        def acquire(self) -> FakeAcquire:
            return FakeAcquire(self.conn)

    pool = FakePool()
    sink = PostgresPaperOrderSink(pool)  # type: ignore[arg-type]

    await sink.ensure_schema()
    await sink.upsert(
        PaperOrderRecord(
            event_id="event-1",
            event_topic="execution.order.submitted",
            redis_stream_id="1-0",
            correlation_id="risk-event-1",
            broker="alpaca-paper",
            broker_order_id="paper-order-1",
            status="accepted",
            symbol="AAPL",
            side="buy",
            quantity="1",
            filled_quantity=None,
            filled_avg_price=None,
            submitted_order={"symbol": "AAPL"},
            broker_response={"id": "paper-order-1"},
            payload_json={"broker_order_id": "paper-order-1"},
            occurred_at="2026-06-17T18:45:00Z",
        )
    )

    sql_text = "\n".join(sql for sql, _ in pool.conn.executed)
    assert "CREATE SCHEMA IF NOT EXISTS trading" in sql_text
    assert "CREATE TABLE IF NOT EXISTS trading.paper_order_events" in sql_text
    assert "INSERT INTO trading.paper_order_events" in sql_text
    insert_args = pool.conn.executed[-1][1]
    assert insert_args[:6] == (
        "event-1",
        "execution.order.submitted",
        "1-0",
        "risk-event-1",
        "alpaca-paper",
        "paper-order-1",
    )
