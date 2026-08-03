"""Tests for the Month 1 Card 2 Decision Maker wire-only stub."""

from __future__ import annotations

import pytest


class FakeRedis:
    def __init__(self, responses: list[object] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.reads: list[dict[object, object]] = []
        self._responses = responses or []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        return f"178012800000{len(self.calls)}-0"

    async def xread(
        self,
        streams: dict[object, object],
        count: int | None = None,
        block: int | None = None,
    ) -> object:
        self.reads.append(streams)
        if self._responses:
            return self._responses.pop(0)
        return []


def test_build_stub_intent_locks_card_2_envelope_shape() -> None:
    from shrap.trading_floor.decision_maker_stub import build_stub_intent

    intent = build_stub_intent(
        signal={
            "ticker": " aapl ",
            "account_id": "PA3TESTACCT",
            "side": "buy",
            "confidence": 0.91,
            "size_hint": 2,
            "urgency": "normal",
            "expiry": "2026-06-08T21:00:00Z",
        }
    )

    assert intent == {
        "ticker": "AAPL",
        "account_id": "PA3TESTACCT",
        "side": "buy",
        "size_hint": 2,
        "quantity": 2,
        "urgency": "normal",
        "justification_text": (
            "Card 2 Decision Maker stub passed through trading.strategy.signal "
            "because confidence 0.91 exceeded threshold 0.70. No real confluence "
            "policy, LLM synthesis, or EXTREME-block applied."
        ),
        "expiry": "2026-06-08T21:00:00Z",
        "mode": "paper",
        "strategy_ids": [],
        "regime_label": "unknown",
        "structural_bias": "neutral",
        "intel_refs": [],
        "confluence_score": 0.0,
        "source": "decision-maker-card-2-stub",
        "stub_threshold": 0.7,
    }


def test_build_stub_intent_rejects_low_confidence_signal() -> None:
    from shrap.trading_floor.decision_maker_stub import should_emit_stub_intent

    assert should_emit_stub_intent({"confidence": 0.7}) is False
    assert should_emit_stub_intent({"confidence": 0.7001}) is True


@pytest.mark.asyncio
async def test_process_once_reads_signal_and_publishes_decision_intent() -> None:
    from shrap.events import EventPublisher
    from shrap.trading_floor.decision_maker_stub import (
        SCHEMA_VERSION,
        STREAM_DECISION_INTENT,
        STREAM_STRATEGY_SIGNAL,
        DecisionMakerStub,
    )

    upstream = FakeRedis()
    signal = await EventPublisher(upstream).publish(
        stream=STREAM_STRATEGY_SIGNAL,
        produced_by="strategy/test",
        schema_version=SCHEMA_VERSION,
        payload={
            "ticker": "AAPL",
            "side": "buy",
            "confidence": 0.8,
            "size_hint": 1,
            "urgency": "normal",
            "expiry": "2026-06-08T21:00:00Z",
        },
    )
    redis = FakeRedis(responses=[[(STREAM_STRATEGY_SIGNAL, [("1-0", upstream.calls[0][1])])]])

    result = await DecisionMakerStub(redis).process_once(last_id="0-0")  # type: ignore[arg-type]

    assert result is not None
    assert result.source_signal.redis_stream_id == "1-0"
    assert result.intent.envelope.correlation_id == signal.envelope.event_id
    assert result.intent.envelope.produced_by == "trading-floor/decision-maker-card-2-stub"
    assert result.intent.envelope.payload is not None
    assert result.intent.envelope.payload["ticker"] == "AAPL"
    assert result.intent.envelope.payload["mode"] == "paper"
    assert result.intent.envelope.payload["strategy_ids"] == []
    assert [call[0] for call in redis.calls] == [STREAM_DECISION_INTENT]


@pytest.mark.asyncio
async def test_process_once_skips_low_confidence_without_publishing() -> None:
    from shrap.events import EventPublisher
    from shrap.trading_floor.decision_maker_stub import (
        SCHEMA_VERSION,
        STREAM_STRATEGY_SIGNAL,
        DecisionMakerStub,
    )

    upstream = FakeRedis()
    await EventPublisher(upstream).publish(
        stream=STREAM_STRATEGY_SIGNAL,
        produced_by="strategy/test",
        schema_version=SCHEMA_VERSION,
        payload={"ticker": "AAPL", "side": "buy", "confidence": 0.4, "size_hint": 1},
    )
    redis = FakeRedis(responses=[[(STREAM_STRATEGY_SIGNAL, [("1-0", upstream.calls[0][1])])]])

    result = await DecisionMakerStub(redis).process_once(last_id="0-0")  # type: ignore[arg-type]

    assert result is None
    assert redis.calls == []


def test_pretrade_checker_consumes_card_2_size_hint() -> None:
    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy
    from shrap.trading_floor.decision_maker_stub import build_stub_intent

    intent = build_stub_intent(
        signal={
            "ticker": "AAPL",
            "side": "buy",
            "confidence": 0.8,
            "size_hint": 3,
            "urgency": "normal",
            "expiry": "2026-06-08T21:00:00Z",
        }
    )

    policy = RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=2)
    decision = PreTradeChecker(policy).check(intent)

    assert decision.approved is True
    assert decision.requested_quantity == 3
    assert decision.approved_quantity == 2
    assert decision.reason_code == "SCALED_DOWN_MAX_QUANTITY"


# --- strategy attribution ------------------------------------------------------
#
# `strategy_ids` was a hardcoded `[]` from Card 2, when the only producer was
# the Strategy Fixture and nothing downstream read it. The Risk Officer (#146)
# made it load-bearing and nothing pinned it, so the firm's first forward-test
# session emitted 20 signals and had all 20 vetoed UNKNOWN_STRATEGY
# (2026-08-03). These are the tests that would have caught it.


def test_the_strategy_rides_through_to_the_intent() -> None:
    from shrap.trading_floor.decision_maker_stub import build_stub_intent

    intent = build_stub_intent(
        {
            "ticker": "AAPL",
            "side": "buy",
            "size_hint": 3,
            "confidence": 0.8,
            "strategy_id": "01KYNH9VKXVQXJ48T4MF306PHE",
            "account_id": "PA3HEG2CLXLU",
        }
    )

    # Without this the Risk Officer cannot resolve the intent to an account and
    # vetoes before evaluating a single limit.
    assert intent["strategy_ids"] == ["01KYNH9VKXVQXJ48T4MF306PHE"]
    assert intent["account_id"] == "PA3HEG2CLXLU"


def test_a_signal_with_no_strategy_still_yields_an_empty_list() -> None:
    """The veto is the RIGHT answer for an unattributable order.

    Inventing a placeholder id here would route it to a book nobody chose,
    which is worse than refusing it.
    """

    from shrap.trading_floor.decision_maker_stub import build_stub_intent

    intent = build_stub_intent({"ticker": "AAPL", "side": "buy", "size_hint": 3, "confidence": 0.8})

    assert intent["strategy_ids"] == []


def test_a_blank_strategy_id_is_not_an_attribution() -> None:
    from shrap.trading_floor.decision_maker_stub import build_stub_intent

    intent = build_stub_intent(
        {
            "ticker": "AAPL",
            "side": "buy",
            "size_hint": 3,
            "confidence": 0.8,
            "strategy_id": "   ",
        }
    )

    assert intent["strategy_ids"] == []
