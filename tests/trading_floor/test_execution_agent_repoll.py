"""Tests for pending-order re-polling in the Execution Agent (Card 16 / KI-003)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from shrap.events import Envelope, ReceivedEvent
from shrap.trading_floor.execution_agent import (
    PendingOrder,
    is_terminal_order_status,
    poll_order_status_once,
    repoll_pending_once,
)


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        return f"178012860000{len(self.calls)}-0"

    async def xread(
        self,
        streams: dict[Any, Any],
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        response: list[tuple[str, list[tuple[str, dict[str, str]]]]] = []
        for stream, last_id in streams.items():
            entries = [
                (f"178012860000{index}-0", fields)
                for index, (written_stream, fields) in enumerate(self.calls, start=1)
                if written_stream == stream and f"178012860000{index}-0" > str(last_id)
            ]
            if entries:
                response.append((str(stream), entries[: count or len(entries)]))
        return response


class SequencedBroker:
    """Broker whose get_order returns scripted responses in order."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = responses
        self.get_order_calls: list[str] = []

    async def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("not used")

    async def get_order(self, order_id: str) -> dict[str, Any]:
        self.get_order_calls.append(order_id)
        if not self._responses:
            raise AssertionError("no scripted responses left")
        return self._responses.pop(0)


def _submitted_event(broker_order_id: str = "order-1") -> ReceivedEvent:
    return ReceivedEvent(
        stream="execution.order.submitted",
        redis_stream_id="1780128600001-0",
        envelope=Envelope(
            event_id="01SUBMITTED",
            schema_version="1.0.0",
            produced_at=datetime.now(UTC),
            produced_by="trading-floor/execution-agent",
            payload={
                "broker": "alpaca-paper",
                "broker_order_id": broker_order_id,
                "status": "accepted",
            },
        ),
    )


def test_terminal_status_classification() -> None:
    assert is_terminal_order_status("filled")
    assert is_terminal_order_status("FILLED")
    assert is_terminal_order_status("canceled")
    assert is_terminal_order_status("rejected")
    assert not is_terminal_order_status("accepted")
    assert not is_terminal_order_status("new")
    assert not is_terminal_order_status("partially_filled")


@pytest.mark.asyncio
async def test_repoll_publishes_fill_once_and_clears_pending() -> None:
    redis = FakeRedis()
    broker = SequencedBroker(
        [
            {"id": "order-1", "status": "accepted"},
            {
                "id": "order-1",
                "status": "filled",
                "filled_qty": "1",
                "filled_avg_price": "185.25",
            },
        ]
    )
    pending = {
        "order-1": PendingOrder(
            submitted_event=_submitted_event(),
            broker_order_id="order-1",
            last_status="accepted",
            next_check_at=0.0,
        )
    }

    # First re-poll: still accepted — no event published (no status change).
    checked = await repoll_pending_once(redis, broker, pending, now=10.0, poll_interval_seconds=5.0)
    assert checked == 1
    assert redis.calls == []
    assert pending["order-1"].next_check_at == 15.0

    # Second re-poll: fill observed — one event to execution.order.filled, cleared.
    checked = await repoll_pending_once(redis, broker, pending, now=20.0, poll_interval_seconds=5.0)
    assert checked == 1
    assert [stream for stream, _ in redis.calls] == ["execution.order.filled"]
    assert pending == {}


@pytest.mark.asyncio
async def test_repoll_respects_next_check_time() -> None:
    redis = FakeRedis()
    broker = SequencedBroker([])
    pending = {
        "order-1": PendingOrder(
            submitted_event=_submitted_event(),
            broker_order_id="order-1",
            last_status="accepted",
            next_check_at=100.0,
        )
    }

    checked = await repoll_pending_once(redis, broker, pending, now=99.0, poll_interval_seconds=5.0)

    assert checked == 0
    assert broker.get_order_calls == []
    assert "order-1" in pending


@pytest.mark.asyncio
async def test_repoll_terminal_cancel_publishes_status_update_and_clears() -> None:
    redis = FakeRedis()
    broker = SequencedBroker([{"id": "order-1", "status": "canceled"}])
    pending = {
        "order-1": PendingOrder(
            submitted_event=_submitted_event(),
            broker_order_id="order-1",
            last_status="accepted",
            next_check_at=0.0,
        )
    }

    await repoll_pending_once(redis, broker, pending, now=10.0, poll_interval_seconds=5.0)

    assert [stream for stream, _ in redis.calls] == ["execution.order.status-updated"]
    assert pending == {}


@pytest.mark.asyncio
async def test_repoll_survives_broker_error_and_retries_later() -> None:
    class FailingBroker:
        async def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("not used")

        async def get_order(self, order_id: str) -> dict[str, Any]:
            raise RuntimeError("broker unreachable")

    redis = FakeRedis()
    pending = {
        "order-1": PendingOrder(
            submitted_event=_submitted_event(),
            broker_order_id="order-1",
            last_status="accepted",
            next_check_at=0.0,
        )
    }

    checked = await repoll_pending_once(
        redis, FailingBroker(), pending, now=10.0, poll_interval_seconds=5.0
    )

    assert checked == 0
    assert "order-1" in pending
    assert pending["order-1"].next_check_at == 15.0
    assert redis.calls == []


@pytest.mark.asyncio
async def test_poll_order_status_once_tracks_non_terminal_orders_as_pending() -> None:
    from shrap.events import EventPublisher

    redis = FakeRedis()
    await EventPublisher(redis).publish(
        stream="execution.order.submitted",
        produced_by="trading-floor/execution-agent",
        schema_version="1.0.0",
        payload={
            "broker": "alpaca-paper",
            "broker_order_id": "order-1",
            "status": "accepted",
        },
    )
    broker = SequencedBroker([{"id": "order-1", "status": "accepted"}])
    pending: dict[str, PendingOrder] = {}
    last_ids: dict[str, str] = {}

    processed = await poll_order_status_once(
        redis=redis,  # type: ignore[arg-type]
        broker=broker,
        last_ids=last_ids,
        start_id="0-0",
        count=10,
        block_ms=1,
        pending=pending,
        poll_interval_seconds=5.0,
        now=100.0,
    )

    assert processed == 1
    assert "order-1" in pending
    assert pending["order-1"].last_status == "accepted"
    assert pending["order-1"].next_check_at == 105.0


@pytest.mark.asyncio
async def test_poll_order_status_once_does_not_track_immediately_filled_orders() -> None:
    from shrap.events import EventPublisher

    redis = FakeRedis()
    await EventPublisher(redis).publish(
        stream="execution.order.submitted",
        produced_by="trading-floor/execution-agent",
        schema_version="1.0.0",
        payload={
            "broker": "alpaca-paper",
            "broker_order_id": "order-1",
            "status": "accepted",
        },
    )
    broker = SequencedBroker([{"id": "order-1", "status": "filled", "filled_qty": "1"}])
    pending: dict[str, PendingOrder] = {}

    await poll_order_status_once(
        redis=redis,  # type: ignore[arg-type]
        broker=broker,
        last_ids={},
        start_id="0-0",
        count=10,
        block_ms=1,
        pending=pending,
        poll_interval_seconds=5.0,
        now=100.0,
    )

    assert pending == {}
