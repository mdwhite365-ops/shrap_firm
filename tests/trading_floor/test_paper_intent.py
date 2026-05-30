"""Tests for the first paper-only inner-loop slice."""

from __future__ import annotations

import pytest


def test_alpaca_settings_require_paper_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from shrap.trading_floor.alpaca import AlpacaPaperSettings

    monkeypatch.setenv("ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "paper-secret")
    monkeypatch.setenv("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets")

    settings = AlpacaPaperSettings()

    assert settings.api_key == "paper-key"
    assert settings.secret_key.get_secret_value() == "paper-secret"
    assert str(settings.endpoint) == "https://paper-api.alpaca.markets/"


def test_alpaca_settings_reject_live_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    from shrap.trading_floor.alpaca import AlpacaPaperSettings

    monkeypatch.setenv("ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "paper-secret")
    monkeypatch.setenv("ALPACA_ENDPOINT", "https://api.alpaca.markets")

    with pytest.raises(ValidationError, match=r"paper-api\.alpaca\.markets"):
        AlpacaPaperSettings()


def test_handcrafted_intent_payload_is_paper_only_and_auditable() -> None:
    from shrap.trading_floor.intent import build_handcrafted_intent

    intent = build_handcrafted_intent(
        ticker="aapl",
        side="buy",
        quantity=3,
        strategy_id="manual-smoke",
        justification="manual paper smoke test; why this might be wrong: no live edge asserted",
    )

    assert intent["ticker"] == "AAPL"
    assert intent["side"] == "buy"
    assert intent["quantity"] == 3
    assert intent["mode"] == "paper"
    assert intent["source"] == "handcrafted"
    assert intent["strategy_ids"] == ["manual-smoke"]
    assert "why this might be wrong" in intent["justification_text"]


@pytest.mark.parametrize("bad_side", ["hold", "long", "sell_short"])
def test_handcrafted_intent_rejects_unknown_side(bad_side: str) -> None:
    from shrap.trading_floor.intent import build_handcrafted_intent

    with pytest.raises(ValueError, match="side"):
        build_handcrafted_intent(
            ticker="AAPL",
            side=bad_side,
            quantity=1,
            strategy_id="manual-smoke",
            justification="why this might be wrong: invalid side test",
        )


def test_pretrade_checker_rejects_real_money_intent() -> None:
    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    checker = PreTradeChecker(RiskPolicy(allowed_universe={"AAPL"}))

    decision = checker.check(
        {
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 1,
            "mode": "live",
            "strategy_ids": ["manual-smoke"],
        }
    )

    assert decision.approved is False
    assert decision.reason_code == "REAL_MONEY_FORBIDDEN_DURING_SPRINT"
    assert decision.to_event_payload()["approved"] is False


def test_pretrade_checker_approves_paper_intent_in_universe() -> None:
    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    checker = PreTradeChecker(RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=5))

    decision = checker.check(
        {
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 3,
            "mode": "paper",
            "strategy_ids": ["manual-smoke"],
        }
    )

    assert decision.approved is True
    assert decision.approved_quantity == 3
    assert decision.reason_code == "APPROVED"


def test_pretrade_checker_scales_down_to_max_quantity() -> None:
    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    checker = PreTradeChecker(RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=5))

    decision = checker.check(
        {
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 12,
            "mode": "paper",
            "strategy_ids": ["manual-smoke"],
        }
    )

    assert decision.approved is True
    assert decision.approved_quantity == 5
    assert decision.reason_code == "SCALED_DOWN_MAX_QUANTITY"


def test_pretrade_checker_rejects_ticker_outside_universe() -> None:
    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    checker = PreTradeChecker(RiskPolicy(allowed_universe={"AAPL"}))

    decision = checker.check(
        {
            "ticker": "TSLA",
            "side": "buy",
            "quantity": 1,
            "mode": "paper",
            "strategy_ids": ["manual-smoke"],
        }
    )

    assert decision.approved is False
    assert decision.reason_code == "TICKER_NOT_IN_UNIVERSE"
