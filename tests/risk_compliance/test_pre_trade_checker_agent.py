from __future__ import annotations

import asyncio
from typing import Any

import pytest

from shrap.events import EventPublisher, PublishedEvent
from shrap.risk_compliance.pre_trade import RiskPolicy


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.reads: list[dict[str, str]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        return f"178012810000{len(self.calls)}-0"

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
                redis_id = f"178012810000{index}-0"
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


async def publish_intent(redis: FakeRedis, payload: dict[str, Any]) -> PublishedEvent:
    return await EventPublisher(redis).publish(
        stream="trading.decision.intent",
        produced_by="trading-floor/decision-maker-card-2-stub",
        schema_version="1.0.0",
        payload=payload,
        correlation_id="01KTESTSIGNAL0000000000000",
    )


def paper_intent(**overrides: object) -> dict[str, Any]:
    intent: dict[str, Any] = {
        "ticker": "AAPL",
        "side": "buy",
        "quantity": 3,
        "size_hint": 3,
        "urgency": "normal",
        "justification_text": "test intent",
        "expiry": "2026-06-08T21:00:00Z",
        "mode": "paper",
        "strategy_ids": [],
        "source": "decision-maker-card-2-stub",
    }
    intent.update(overrides)
    return intent


@pytest.mark.asyncio
async def test_poll_once_approves_intent_with_correlation_and_scaled_quantity() -> None:
    from shrap.risk_compliance.pre_trade_checker_agent import (
        STREAM_RISK_APPROVED,
        poll_once,
    )

    redis = FakeRedis()
    intent = await publish_intent(redis, paper_intent(quantity=3, size_hint=3))
    last_ids: dict[str, str] = {}

    processed = await poll_once(
        redis,  # type: ignore[arg-type]
        RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=2),
        last_ids,
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert processed == 1
    assert last_ids == {"trading.decision.intent": "1780128100001-0"}
    assert redis.calls[-1][0] == STREAM_RISK_APPROVED
    risk = redis.calls[-1][1]
    assert risk["h_correlation_id"] == intent.envelope.event_id
    assert '"approved":true' in risk["payload"]
    assert '"approved_quantity":2' in risk["payload"]
    assert '"requested_quantity":3' in risk["payload"]
    assert '"intent_payload"' in risk["payload"]


@pytest.mark.asyncio
async def test_poll_once_vetoes_non_paper_mode_with_reason() -> None:
    from shrap.risk_compliance.pre_trade import REAL_MONEY_FORBIDDEN_REASON
    from shrap.risk_compliance.pre_trade_checker_agent import STREAM_RISK_VETOED, poll_once

    redis = FakeRedis()
    await publish_intent(redis, paper_intent(mode="live"))

    processed = await poll_once(
        redis,  # type: ignore[arg-type]
        RiskPolicy(allowed_universe={"AAPL"}),
        {},
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert processed == 1
    assert redis.calls[-1][0] == STREAM_RISK_VETOED
    assert f'"reason":"{REAL_MONEY_FORBIDDEN_REASON}"' in redis.calls[-1][1]["payload"]


@pytest.mark.asyncio
async def test_poll_once_vetoes_universe_ineligible_with_reason() -> None:
    from shrap.risk_compliance.pre_trade_checker_agent import STREAM_RISK_VETOED, poll_once

    redis = FakeRedis()
    await publish_intent(redis, paper_intent(ticker="TSLA"))

    processed = await poll_once(
        redis,  # type: ignore[arg-type]
        RiskPolicy(allowed_universe={"AAPL"}),
        {},
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert processed == 1
    assert redis.calls[-1][0] == STREAM_RISK_VETOED
    assert '"reason":"TICKER_NOT_IN_UNIVERSE"' in redis.calls[-1][1]["payload"]


@pytest.mark.asyncio
async def test_poll_once_vetoes_non_positive_quantity_with_reason() -> None:
    from shrap.risk_compliance.pre_trade_checker_agent import STREAM_RISK_VETOED, poll_once

    redis = FakeRedis()
    await publish_intent(redis, paper_intent(quantity=0, size_hint=0))

    processed = await poll_once(
        redis,  # type: ignore[arg-type]
        RiskPolicy(allowed_universe={"AAPL"}),
        {},
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert processed == 1
    assert redis.calls[-1][0] == STREAM_RISK_VETOED
    assert '"reason":"INVALID_QUANTITY"' in redis.calls[-1][1]["payload"]


@pytest.mark.asyncio
async def test_reprocessing_same_intent_event_emits_same_decision_payload() -> None:
    from shrap.events import Envelope
    from shrap.risk_compliance.pre_trade_checker_agent import poll_once

    redis = FakeRedis()
    await publish_intent(redis, paper_intent(quantity=1))

    await poll_once(
        redis,  # type: ignore[arg-type]
        RiskPolicy(allowed_universe={"AAPL"}),
        {},
        start_id="0-0",
        count=10,
        block_ms=1,
    )
    first_payload = Envelope.from_redis_fields(redis.calls[-1][1]).payload

    await poll_once(
        redis,  # type: ignore[arg-type]
        RiskPolicy(allowed_universe={"AAPL"}),
        {},
        start_id="0-0",
        count=10,
        block_ms=1,
    )
    second_payload = Envelope.from_redis_fields(redis.calls[-1][1]).payload

    assert first_payload == second_payload


@pytest.mark.asyncio
async def test_process_intent_event_preserves_original_intent_payload() -> None:
    from shrap.events import Envelope
    from shrap.risk_compliance.pre_trade_checker_agent import process_intent_event

    redis = FakeRedis()
    intent = await publish_intent(redis, paper_intent(quantity=3, size_hint=3))
    event_fields = redis.calls[0][1]
    received = __import__("shrap.events", fromlist=["ReceivedEvent"]).ReceivedEvent(
        stream="trading.decision.intent",
        redis_stream_id="1780128100001-0",
        envelope=Envelope.from_redis_fields(event_fields),
    )

    published = await process_intent_event(
        redis,  # type: ignore[arg-type]
        received,
        RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=2),
    )

    assert published.envelope.correlation_id == intent.envelope.event_id
    assert published.envelope.payload is not None
    assert published.envelope.payload["intent_payload"]["quantity"] == 3
    assert published.envelope.payload["approved_intent_payload"]["quantity"] == 2


@pytest.mark.asyncio
async def test_run_loop_exits_cleanly_when_stop_signal_is_set() -> None:
    from shrap.risk_compliance.pre_trade_checker_agent import run_loop

    redis = FakeRedis()
    stop = asyncio.Event()

    async def stop_soon() -> None:
        await asyncio.sleep(0)
        stop.set()

    await asyncio.gather(
        run_loop(
            redis,  # type: ignore[arg-type]
            RiskPolicy(allowed_universe={"AAPL"}),
            stop=stop,
            start_id="0-0",
            count=10,
            block_ms=1,
            retry_delay_seconds=0,
        ),
        stop_soon(),
    )

    assert stop.is_set()
