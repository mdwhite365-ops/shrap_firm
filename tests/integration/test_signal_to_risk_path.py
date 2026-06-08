from __future__ import annotations

from typing import Any

import pytest

from shrap.events import EventPublisher
from shrap.risk_compliance.pre_trade import RiskPolicy


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.reads: list[dict[str, str]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        return f"178012820000{len(self.calls)}-0"

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
                redis_id = f"178012820000{index}-0"
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


def signal_payload(**overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ticker": "AAPL",
        "side": "buy",
        "confidence": 0.8,
        "size_hint": 3,
        "urgency": "normal",
        "expiry": "2026-06-08T21:00:00Z",
    }
    payload.update(overrides)
    return payload


def decision_intent_payload(**overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ticker": "AAPL",
        "side": "buy",
        "size_hint": 1,
        "quantity": 1,
        "urgency": "normal",
        "justification_text": "test decision intent",
        "expiry": "2026-06-08T21:00:00Z",
        "mode": "paper",
        "strategy_ids": [],
        "regime_label": "unknown",
        "structural_bias": "neutral",
        "intel_refs": [],
        "confluence_score": 0.0,
        "source": "test-fixture",
        "stub_threshold": 0.7,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_signal_to_approved_intent() -> None:
    from shrap.events import Envelope
    from shrap.risk_compliance.pre_trade_checker_agent import poll_once as risk_poll_once
    from shrap.trading_floor.decision_maker_stub import (
        STREAM_DECISION_INTENT,
        STREAM_STRATEGY_SIGNAL,
        DecisionMakerStub,
    )

    redis = FakeRedis()
    signal = await EventPublisher(redis).publish(
        stream=STREAM_STRATEGY_SIGNAL,
        produced_by="strategy/test",
        schema_version="1.0.0",
        payload=signal_payload(size_hint=3),
    )

    decision_result = await DecisionMakerStub(redis).process_once(last_id="0-0")  # type: ignore[arg-type]
    assert decision_result is not None
    assert redis.calls[-1][0] == STREAM_DECISION_INTENT

    processed = await risk_poll_once(
        redis,  # type: ignore[arg-type]
        RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=2),
        {},
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert processed == 1
    assert redis.calls[-1][0] == "risk.intent.approved"
    intent_envelope = Envelope.from_redis_fields(redis.calls[-2][1])
    risk_envelope = Envelope.from_redis_fields(redis.calls[-1][1])
    assert intent_envelope.correlation_id == signal.envelope.event_id
    assert risk_envelope.correlation_id == intent_envelope.event_id
    assert risk_envelope.payload is not None
    assert risk_envelope.payload["approved_quantity"] == 2
    assert risk_envelope.payload["intent_event_id"] == intent_envelope.event_id


@pytest.mark.asyncio
async def test_signal_to_vetoed_intent() -> None:
    from shrap.events import Envelope
    from shrap.risk_compliance.pre_trade import REAL_MONEY_FORBIDDEN_REASON
    from shrap.risk_compliance.pre_trade_checker_agent import poll_once as risk_poll_once
    from shrap.trading_floor.decision_maker_stub import STREAM_DECISION_INTENT

    redis = FakeRedis()
    intent = await EventPublisher(redis).publish(
        stream=STREAM_DECISION_INTENT,
        produced_by="trading-floor/decision-maker-card-2-stub",
        schema_version="1.0.0",
        payload=decision_intent_payload(mode="live"),
        correlation_id="01KTESTSIGNAL0000000000000",
    )

    processed = await risk_poll_once(
        redis,  # type: ignore[arg-type]
        RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=2),
        {},
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert processed == 1
    assert redis.calls[-1][0] == "risk.intent.vetoed"
    risk_envelope = Envelope.from_redis_fields(redis.calls[-1][1])
    assert risk_envelope.correlation_id == intent.envelope.event_id
    assert risk_envelope.payload is not None
    assert risk_envelope.payload["reason"] == REAL_MONEY_FORBIDDEN_REASON
    assert risk_envelope.payload["intent_payload"]["mode"] == "live"
