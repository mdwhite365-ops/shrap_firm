"""Tests for the paper-signal smoke path before broker order submission."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_publish_handcrafted_signal_emits_intent_and_risk_decision() -> None:
    from shrap.risk_compliance.pre_trade import RiskPolicy
    from shrap.trading_floor.paper_smoke import publish_handcrafted_signal

    class FakeRedis:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, str]]] = []

        async def xadd(self, stream: str, fields: dict[str, str]) -> str:
            self.calls.append((stream, fields))
            return f"178012800000{len(self.calls)}-0"

    redis = FakeRedis()

    result = await publish_handcrafted_signal(
        redis=redis,  # type: ignore[arg-type]
        produced_by="trading-floor/paper-smoke",
        ticker="AAPL",
        side="buy",
        quantity=2,
        strategy_id="manual-smoke",
        justification="manual paper smoke; why this might be wrong: no edge asserted",
        policy=RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=5),
    )

    assert result.intent.envelope.payload is not None
    assert result.risk_decision.envelope.payload is not None
    assert result.intent.envelope.payload["mode"] == "paper"
    assert result.risk_decision.envelope.payload["approved"] is True
    assert result.risk_decision.envelope.payload["approved_quantity"] == 2
    assert [call[0] for call in redis.calls] == [
        "trading.decision.intent",
        "risk.intent.approved",
    ]


@pytest.mark.asyncio
async def test_publish_handcrafted_signal_emits_veto_for_out_of_universe() -> None:
    from shrap.risk_compliance.pre_trade import RiskPolicy
    from shrap.trading_floor.paper_smoke import publish_handcrafted_signal

    class FakeRedis:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, str]]] = []

        async def xadd(self, stream: str, fields: dict[str, str]) -> str:
            self.calls.append((stream, fields))
            return f"178012800000{len(self.calls)}-0"

    redis = FakeRedis()

    result = await publish_handcrafted_signal(
        redis=redis,  # type: ignore[arg-type]
        produced_by="trading-floor/paper-smoke",
        ticker="TSLA",
        side="buy",
        quantity=2,
        strategy_id="manual-smoke",
        justification="manual paper smoke; why this might be wrong: no edge asserted",
        policy=RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=5),
    )

    assert result.risk_decision.envelope.payload is not None
    assert result.risk_decision.envelope.payload["approved"] is False
    assert result.risk_decision.envelope.payload["reason_code"] == "TICKER_NOT_IN_UNIVERSE"
    assert [call[0] for call in redis.calls] == [
        "trading.decision.intent",
        "risk.intent.vetoed",
    ]
