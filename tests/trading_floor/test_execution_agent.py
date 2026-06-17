"""Tests for the paper Execution Agent slice."""

from __future__ import annotations

from typing import Any

import pytest

from shrap.events import EventPublisher


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.reads: list[dict[str, str]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        return f"178012830000{len(self.calls)}-0"

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
                redis_id = f"178012830000{index}-0"
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


class FakeBroker:
    def __init__(self) -> None:
        self.orders: list[dict[str, Any]] = []
        self.statuses: dict[str, dict[str, Any]] = {}
        self.status_requests: list[str] = []

    async def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        self.orders.append(order)
        return {
            "id": "paper-order-123",
            "client_order_id": order["client_order_id"],
            "symbol": order["symbol"],
            "qty": order["qty"],
            "side": order["side"],
            "status": "accepted",
        }

    async def get_order(self, order_id: str) -> dict[str, Any]:
        self.status_requests.append(order_id)
        return self.statuses[order_id]


def approved_risk_payload(**overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "approved": True,
        "reason_code": "APPROVED",
        "ticker": "AAPL",
        "requested_quantity": 2,
        "approved_quantity": 2,
        "reasons": [],
        "intent_event_id": "01KINTENT000000000000000",
        "intent_stream": "trading.decision.intent",
        "intent_redis_stream_id": "1780128200001-0",
        "intent_payload": {
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 2,
            "mode": "paper",
            "strategy_ids": ["manual-smoke"],
        },
        "reason": "APPROVED",
        "strategy_ids": ["manual-smoke"],
        "approved_intent_payload": {
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 2,
            "mode": "paper",
            "strategy_ids": ["manual-smoke"],
        },
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_poll_once_submits_approved_paper_order_and_publishes_submitted_event() -> None:
    from shrap.trading_floor.execution_agent import (
        STREAM_EXECUTION_ORDER_SUBMITTED,
        STREAM_RISK_APPROVED,
        poll_once,
    )

    redis = FakeRedis()
    risk_event = await EventPublisher(redis).publish(
        stream=STREAM_RISK_APPROVED,
        produced_by="risk/pre-trade-checker",
        schema_version="1.0.0",
        payload=approved_risk_payload(),
        correlation_id="01KINTENT000000000000000",
    )
    broker = FakeBroker()
    last_ids: dict[str, str] = {}

    processed = await poll_once(
        redis=redis,  # type: ignore[arg-type]
        broker=broker,
        last_ids=last_ids,
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert processed == 1
    assert last_ids == {STREAM_RISK_APPROVED: "1780128300001-0"}
    assert broker.orders == [
        {
            "symbol": "AAPL",
            "qty": "2",
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "client_order_id": risk_event.envelope.event_id,
        }
    ]
    assert redis.calls[-1][0] == STREAM_EXECUTION_ORDER_SUBMITTED
    submitted = redis.calls[-1][1]
    assert submitted["h_correlation_id"] == risk_event.envelope.event_id
    assert '"broker_order_id":"paper-order-123"' in submitted["payload"]
    assert '"status":"accepted"' in submitted["payload"]


@pytest.mark.asyncio
async def test_poll_once_refuses_non_paper_approved_intent_without_advancing_offset() -> None:
    from shrap.trading_floor.execution_agent import STREAM_RISK_APPROVED, poll_once

    redis = FakeRedis()
    await EventPublisher(redis).publish(
        stream=STREAM_RISK_APPROVED,
        produced_by="risk/pre-trade-checker",
        schema_version="1.0.0",
        payload=approved_risk_payload(
            approved_intent_payload={
                "ticker": "AAPL",
                "side": "buy",
                "quantity": 2,
                "mode": "live",
            }
        ),
    )
    broker = FakeBroker()
    last_ids: dict[str, str] = {}

    processed = await poll_once(
        redis=redis,  # type: ignore[arg-type]
        broker=broker,
        last_ids=last_ids,
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert processed == 0
    assert broker.orders == []
    assert last_ids == {STREAM_RISK_APPROVED: "0-0"}
    assert [stream for stream, _ in redis.calls] == [STREAM_RISK_APPROVED]


@pytest.mark.asyncio
async def test_poll_order_status_once_publishes_status_update_for_open_order() -> None:
    from shrap.trading_floor.execution_agent import (
        STREAM_EXECUTION_ORDER_STATUS_UPDATED,
        STREAM_EXECUTION_ORDER_SUBMITTED,
        poll_order_status_once,
    )

    redis = FakeRedis()
    submitted_event = await EventPublisher(redis).publish(
        stream=STREAM_EXECUTION_ORDER_SUBMITTED,
        produced_by="trading-floor/execution-agent",
        schema_version="1.0.0",
        payload={
            "risk_event_id": "01KRISK0000000000000000",
            "broker": "alpaca-paper",
            "broker_order_id": "paper-order-123",
            "status": "accepted",
            "submitted_order": {"symbol": "AAPL", "qty": "2", "side": "buy"},
        },
    )
    broker = FakeBroker()
    broker.statuses["paper-order-123"] = {
        "id": "paper-order-123",
        "symbol": "AAPL",
        "qty": "2",
        "filled_qty": "0",
        "side": "buy",
        "status": "accepted",
    }
    last_ids: dict[str, str] = {}

    processed = await poll_order_status_once(
        redis=redis,  # type: ignore[arg-type]
        broker=broker,
        last_ids=last_ids,
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert processed == 1
    assert broker.status_requests == ["paper-order-123"]
    assert last_ids == {STREAM_EXECUTION_ORDER_SUBMITTED: "1780128300001-0"}
    assert redis.calls[-1][0] == STREAM_EXECUTION_ORDER_STATUS_UPDATED
    status_event = redis.calls[-1][1]
    assert status_event["h_correlation_id"] == submitted_event.envelope.event_id
    assert '"broker_order_id":"paper-order-123"' in status_event["payload"]
    assert '"status":"accepted"' in status_event["payload"]


@pytest.mark.asyncio
async def test_poll_order_status_once_publishes_filled_event_for_filled_order() -> None:
    from shrap.trading_floor.execution_agent import (
        STREAM_EXECUTION_ORDER_FILLED,
        STREAM_EXECUTION_ORDER_SUBMITTED,
        poll_order_status_once,
    )

    redis = FakeRedis()
    submitted_event = await EventPublisher(redis).publish(
        stream=STREAM_EXECUTION_ORDER_SUBMITTED,
        produced_by="trading-floor/execution-agent",
        schema_version="1.0.0",
        payload={
            "risk_event_id": "01KRISK0000000000000000",
            "broker": "alpaca-paper",
            "broker_order_id": "paper-order-123",
            "status": "accepted",
            "submitted_order": {"symbol": "AAPL", "qty": "2", "side": "buy"},
        },
    )
    broker = FakeBroker()
    broker.statuses["paper-order-123"] = {
        "id": "paper-order-123",
        "symbol": "AAPL",
        "qty": "2",
        "filled_qty": "2",
        "side": "buy",
        "status": "filled",
        "filled_avg_price": "185.25",
        "filled_at": "2026-06-17T18:45:00Z",
    }
    last_ids: dict[str, str] = {}

    processed = await poll_order_status_once(
        redis=redis,  # type: ignore[arg-type]
        broker=broker,
        last_ids=last_ids,
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert processed == 1
    assert last_ids == {STREAM_EXECUTION_ORDER_SUBMITTED: "1780128300001-0"}
    assert redis.calls[-1][0] == STREAM_EXECUTION_ORDER_FILLED
    filled_event = redis.calls[-1][1]
    assert filled_event["h_correlation_id"] == submitted_event.envelope.event_id
    assert '"filled_qty":"2"' in filled_event["payload"]
    assert '"filled_avg_price":"185.25"' in filled_event["payload"]


@pytest.mark.asyncio
async def test_alpaca_paper_client_get_order_uses_paper_order_endpoint() -> None:
    from shrap.trading_floor.alpaca import AlpacaPaperClient, AlpacaPaperSettings

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"id": "order-1", "status": "filled"}

    class FakeHttpClient:
        def __init__(self) -> None:
            self.gets: list[tuple[str, dict[str, str]]] = []

        async def get(self, url: str, headers: dict[str, str]) -> FakeResponse:
            self.gets.append((url, headers))
            return FakeResponse()

    settings = AlpacaPaperSettings(
        api_key="paper-key",
        secret_key="paper-secret",
        endpoint="https://paper-api.alpaca.markets",
    )
    http_client = FakeHttpClient()

    response = await AlpacaPaperClient(settings).get_order(
        http_client,  # type: ignore[arg-type]
        "order-1",
    )

    assert response == {"id": "order-1", "status": "filled"}
    assert http_client.gets == [
        (
            "https://paper-api.alpaca.markets/v2/orders/order-1",
            {
                "APCA-API-KEY-ID": "paper-key",
                "APCA-API-SECRET-KEY": "paper-secret",
            },
        )
    ]


@pytest.mark.asyncio
async def test_alpaca_paper_client_submit_order_posts_to_paper_orders_endpoint() -> None:
    from shrap.trading_floor.alpaca import AlpacaPaperClient, AlpacaPaperSettings

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"id": "order-1", "status": "accepted"}

    class FakeHttpClient:
        def __init__(self) -> None:
            self.posts: list[tuple[str, dict[str, str], dict[str, Any]]] = []

        async def post(
            self,
            url: str,
            headers: dict[str, str],
            json: dict[str, Any],
        ) -> FakeResponse:
            self.posts.append((url, headers, json))
            return FakeResponse()

    settings = AlpacaPaperSettings(
        api_key="paper-key",
        secret_key="paper-secret",
        endpoint="https://paper-api.alpaca.markets",
    )
    http_client = FakeHttpClient()
    order = {
        "symbol": "AAPL",
        "qty": "1",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "client_order_id": "risk-event-id",
    }

    response = await AlpacaPaperClient(settings).submit_order(
        http_client,  # type: ignore[arg-type]
        order,
    )

    assert response == {"id": "order-1", "status": "accepted"}
    assert http_client.posts == [
        (
            "https://paper-api.alpaca.markets/v2/orders",
            {
                "APCA-API-KEY-ID": "paper-key",
                "APCA-API-SECRET-KEY": "paper-secret",
            },
            order,
        )
    ]
