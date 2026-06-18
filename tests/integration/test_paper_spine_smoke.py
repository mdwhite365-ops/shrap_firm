"""End-to-end paper-trading spine smoke test."""

from __future__ import annotations

from typing import Any

import pytest


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.reads: list[dict[str, str]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        return f"178012840000{len(self.calls)}-0"

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
                redis_id = f"178012840000{index}-0"
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


class FakePaperBroker:
    def __init__(self) -> None:
        self.submitted_orders: list[dict[str, Any]] = []
        self.status_requests: list[str] = []

    async def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        self.submitted_orders.append(order)
        return {
            "id": "paper-order-e2e-1",
            "client_order_id": order["client_order_id"],
            "symbol": order["symbol"],
            "qty": order["qty"],
            "side": order["side"],
            "status": "accepted",
        }

    async def get_order(self, order_id: str) -> dict[str, Any]:
        self.status_requests.append(order_id)
        return {
            "id": order_id,
            "symbol": "AAPL",
            "qty": "1",
            "filled_qty": "1",
            "side": "buy",
            "status": "filled",
            "filled_avg_price": "185.25",
            "filled_at": "2026-06-17T18:45:00Z",
        }


@pytest.mark.asyncio
async def test_handcrafted_signal_traverses_decision_risk_execution_and_fill_status() -> None:
    from shrap.risk_compliance.pre_trade import RiskPolicy
    from shrap.trading_floor.paper_spine_smoke import run_handcrafted_paper_spine_smoke

    redis = FakeRedis()
    broker = FakePaperBroker()

    result = await run_handcrafted_paper_spine_smoke(
        redis=redis,  # type: ignore[arg-type]
        broker=broker,
        signal={
            "ticker": "AAPL",
            "side": "buy",
            "confidence": 0.91,
            "size_hint": 1,
            "urgency": "normal",
            "expiry": "2026-06-17T21:00:00Z",
        },
        risk_policy=RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=5),
        produced_by="integration/paper-spine-smoke",
    )

    assert [stream for stream, _ in redis.calls] == [
        "trading.strategy.signal",
        "trading.decision.intent",
        "risk.intent.approved",
        "execution.order.submitted",
        "execution.order.filled",
    ]
    assert broker.submitted_orders == [
        {
            "symbol": "AAPL",
            "qty": "1",
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "client_order_id": result.risk_decision.envelope.event_id,
        }
    ]
    assert broker.status_requests == ["paper-order-e2e-1"]
    assert result.strategy_signal.stream == "trading.strategy.signal"
    assert result.decision_intent.stream == "trading.decision.intent"
    assert result.risk_decision.stream == "risk.intent.approved"
    assert result.order_submitted.stream == "execution.order.submitted"
    assert result.order_status.stream == "execution.order.filled"
    assert result.order_status.envelope.payload is not None
    assert result.order_status.envelope.payload["filled_qty"] == "1"
    assert result.order_status.envelope.correlation_id == result.order_submitted.envelope.event_id
