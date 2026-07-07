"""Tests for the Decision Maker stub service loop."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from shrap.events import Envelope, EventPublisher, normalize_redis_fields
from shrap.trading_floor.decision_maker_service import poll_once


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
            if str(last_id) == "$":
                continue
            entries = [
                (f"178012860000{index}-0", fields)
                for index, (written_stream, fields) in enumerate(self.calls, start=1)
                if written_stream == stream and f"178012860000{index}-0" > str(last_id)
            ]
            if entries:
                response.append((str(stream), entries[: count or len(entries)]))
        return response


def _signal(confidence: float, ticker: str = "SPY", quantity: int = 1) -> dict[str, Any]:
    return {
        "strategy_id": "fixture-regime-gated-v0",
        "ticker": ticker,
        "side": "buy",
        "size_hint": quantity,
        "quantity": quantity,
        "confidence": confidence,
    }


@pytest.mark.asyncio
async def test_high_confidence_signal_becomes_intent() -> None:
    redis = FakeRedis()
    signal_event = await EventPublisher(redis).publish(
        stream="trading.strategy.signal",
        produced_by="research/strategy-fixture",
        schema_version="1.0.0",
        payload=_signal(0.99),
    )
    last_ids: dict[str, str] = {"trading.strategy.signal": "0-0"}

    emitted = await poll_once(
        redis=redis,  # type: ignore[arg-type]
        last_ids=last_ids,
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert emitted == 1
    intent_calls = [c for c in redis.calls if c[0] == "trading.decision.intent"]
    assert len(intent_calls) == 1
    envelope = Envelope.from_redis_fields(normalize_redis_fields(intent_calls[0][1]))
    assert envelope.correlation_id == signal_event.envelope.event_id
    assert envelope.payload is not None
    assert envelope.payload["ticker"] == "SPY"
    assert envelope.payload["mode"] == "paper"
    assert envelope.payload["quantity"] == 1


@pytest.mark.asyncio
async def test_low_confidence_and_malformed_signals_skip_and_advance() -> None:
    redis = FakeRedis()
    publisher = EventPublisher(redis)
    await publisher.publish(
        stream="trading.strategy.signal",
        produced_by="test",
        schema_version="1.0.0",
        payload=_signal(0.10),  # below threshold
    )
    await publisher.publish(
        stream="trading.strategy.signal",
        produced_by="test",
        schema_version="1.0.0",
        payload={"confidence": 0.99},  # malformed: no ticker -> ValueError
    )
    await publisher.publish(
        stream="trading.strategy.signal",
        produced_by="test",
        schema_version="1.0.0",
        payload=_signal(0.95),
    )
    last_ids: dict[str, str] = {"trading.strategy.signal": "0-0"}

    emitted = await poll_once(
        redis=redis,  # type: ignore[arg-type]
        last_ids=last_ids,
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert emitted == 1
    assert last_ids["trading.strategy.signal"] == "1780128600003-0"
    intent_calls = [c for c in redis.calls if c[0] == "trading.decision.intent"]
    assert len(intent_calls) == 1


def test_service_wiring() -> None:
    from shrap.agents.trading_floor.decision_maker.config import Settings

    settings = Settings()
    assert settings.start_id == "$"  # never re-trade historical signals on restart
    assert settings.confidence_threshold == 0.7

    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    assert pyproject["project"]["scripts"]["shrap-decision-maker"] == (
        "shrap.agents.trading_floor.decision_maker.__main__:main"
    )
    compose = Path("infra/docker-compose.yml").read_text()
    assert "decision-maker:" in compose
    assert 'DECISION_MAKER_START_ID: "$$"' in compose
    assert 'CMD ["shrap-decision-maker"]' in Path("infra/decision-maker.Dockerfile").read_text()
