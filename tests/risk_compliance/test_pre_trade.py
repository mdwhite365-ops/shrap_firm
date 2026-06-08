from __future__ import annotations


def test_check_vetoes_non_numeric_quantity() -> None:
    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    decision = PreTradeChecker(RiskPolicy(allowed_universe={"AAPL"})).check(
        {"ticker": "AAPL", "quantity": "abc", "mode": "paper"}
    )

    assert decision.approved is False
    assert decision.reason_code == "INVALID_QUANTITY"
    assert decision.requested_quantity == 0
    assert "quantity is not a parseable integer" in decision.reasons[0]


def test_check_vetoes_none_quantity() -> None:
    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    decision = PreTradeChecker(RiskPolicy(allowed_universe={"AAPL"})).check(
        {"ticker": "AAPL", "quantity": None, "mode": "paper"}
    )

    assert decision.approved is False
    assert decision.reason_code == "INVALID_QUANTITY"
    assert decision.requested_quantity == 0
    assert "got None" in decision.reasons[0]


def test_check_vetoes_float_quantity_with_fractional_part() -> None:
    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    decision = PreTradeChecker(RiskPolicy(allowed_universe={"AAPL"})).check(
        {"ticker": "AAPL", "quantity": 3.5, "mode": "paper"}
    )

    assert decision.approved is False
    assert decision.reason_code == "INVALID_QUANTITY"
    assert decision.requested_quantity == 0
    assert "quantity is not a parseable integer" in decision.reasons[0]


def test_risk_decision_payload_omits_top_level_mode() -> None:
    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    decision = PreTradeChecker(RiskPolicy(allowed_universe={"AAPL"})).check(
        {"ticker": "AAPL", "quantity": 1, "mode": "paper"}
    )

    assert "mode" not in decision.to_event_payload()
