from __future__ import annotations


def test_check_vetoes_non_numeric_quantity() -> None:
    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    decision = PreTradeChecker(RiskPolicy(allowed_universe={"AAPL"})).check(
        {"ticker": "AAPL", "quantity": "abc", "mode": "paper"}
    )

    assert decision.approved is False
    assert decision.reason_code == "INVALID_QUANTITY"
    assert decision.requested_quantity == 0
    assert "quantity is not a number" in decision.reasons[0]


def test_check_vetoes_none_quantity() -> None:
    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    decision = PreTradeChecker(RiskPolicy(allowed_universe={"AAPL"})).check(
        {"ticker": "AAPL", "quantity": None, "mode": "paper"}
    )

    assert decision.approved is False
    assert decision.reason_code == "INVALID_QUANTITY"
    assert decision.requested_quantity == 0
    assert "got None" in decision.reasons[0]


def test_fractional_quantity_is_approved_not_vetoed() -> None:
    """The reversal. This test asserted the opposite until KI-033.

    Rejecting a fraction was correct when every order was a whole number of
    shares. #195 made fractional orders the normal case — a one-share intent
    scales to 0.1875 — and this gate went on refusing them, upstream of all the
    fractional arithmetic that was supposed to handle them.
    """

    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    policy = RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=100)
    decision = PreTradeChecker(policy).check({"ticker": "AAPL", "quantity": 3.5, "mode": "paper"})

    assert decision.approved is True
    assert decision.reason_code == "APPROVED"
    assert decision.requested_quantity == 3.5
    assert decision.approved_quantity == 3.5


def test_a_sub_share_exit_survives_the_gate() -> None:
    """The exact quantity that could not be sold: U, stranded 2026-08-18.

    `int(0.012648483)` is 0, and 0 failed the positivity check, so the exit was
    refused as INVALID_QUANTITY with a recorded quantity of 0 — 52 times before
    anyone looked at `risk.decisions`.
    """

    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    policy = RiskPolicy(allowed_universe={"U"}, max_quantity_per_order=100)
    decision = PreTradeChecker(policy).check(
        {"ticker": "U", "quantity": 0.012648483, "mode": "paper"}
    )

    assert decision.approved is True
    assert decision.approved_quantity == 0.012648483


def test_a_bool_is_not_a_quantity() -> None:
    """`float(True)` is 1.0, which would make a malformed intent a live order."""

    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    decision = PreTradeChecker(RiskPolicy(allowed_universe={"AAPL"})).check(
        {"ticker": "AAPL", "quantity": True, "mode": "paper"}
    )

    assert decision.approved is False
    assert decision.reason_code == "INVALID_QUANTITY"


def test_nan_and_infinity_are_refused() -> None:
    """Both pass `float()` and then answer False to every comparison below."""

    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    for raw in ("nan", "inf", float("nan"), float("inf")):
        decision = PreTradeChecker(RiskPolicy(allowed_universe={"AAPL"})).check(
            {"ticker": "AAPL", "quantity": raw, "mode": "paper"}
        )
        assert decision.approved is False, raw
        assert decision.reason_code == "INVALID_QUANTITY", raw


def test_a_numeric_string_quantity_is_parsed() -> None:
    """Redis payloads arrive as strings; 0.1875 must not become 0."""

    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    policy = RiskPolicy(allowed_universe={"RIOT"}, max_quantity_per_order=100)
    decision = PreTradeChecker(policy).check(
        {"ticker": "RIOT", "quantity": "0.1875", "mode": "paper"}
    )

    assert decision.approved is True
    assert decision.approved_quantity == 0.1875


def test_risk_decision_payload_omits_top_level_mode() -> None:
    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    decision = PreTradeChecker(RiskPolicy(allowed_universe={"AAPL"})).check(
        {"ticker": "AAPL", "quantity": 1, "mode": "paper"}
    )

    assert "mode" not in decision.to_event_payload()


def test_default_policy_vetoes_ticker_not_in_universe() -> None:
    # Regression: the default policy (universe_check_enabled=True) keeps the
    # static allowlist as the binding universe gate.
    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    decision = PreTradeChecker(RiskPolicy(allowed_universe={"AAPL"})).check(
        {"ticker": "TSLA", "quantity": 1, "mode": "paper"}
    )

    assert decision.approved is False
    assert decision.reason_code == "TICKER_NOT_IN_UNIVERSE"


def test_disabled_universe_allows_ticker_outside_allowlist() -> None:
    # With universe_check_enabled=False the static allowlist stops binding; the
    # downstream Tier 3 gate becomes the authoritative universe check.
    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    policy = RiskPolicy(allowed_universe={"AAPL"}, universe_check_enabled=False)
    decision = PreTradeChecker(policy).check({"ticker": "RKLB", "quantity": 1, "mode": "paper"})

    assert decision.approved is True
    assert decision.reason_code == "APPROVED"
    assert decision.ticker == "RKLB"


def test_disabled_universe_still_vetoes_non_paper_mode() -> None:
    from shrap.risk_compliance.pre_trade import (
        REAL_MONEY_FORBIDDEN_REASON,
        PreTradeChecker,
        RiskPolicy,
    )

    policy = RiskPolicy(allowed_universe={"AAPL"}, universe_check_enabled=False)
    decision = PreTradeChecker(policy).check({"ticker": "RKLB", "quantity": 1, "mode": "live"})

    assert decision.approved is False
    assert decision.reason_code == REAL_MONEY_FORBIDDEN_REASON


def test_disabled_universe_still_vetoes_when_kill_switch_active() -> None:
    # The most safety-critical case: disabling the allowlist must never disable
    # the kill switch.
    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    policy = RiskPolicy(
        allowed_universe={"AAPL"},
        universe_check_enabled=False,
        kill_switch_active=True,
    )
    decision = PreTradeChecker(policy).check({"ticker": "RKLB", "quantity": 1, "mode": "paper"})

    assert decision.approved is False
    assert decision.reason_code == "KILL_SWITCH_ACTIVE"


def test_disabled_universe_still_vetoes_non_positive_quantity() -> None:
    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    policy = RiskPolicy(allowed_universe={"AAPL"}, universe_check_enabled=False)
    decision = PreTradeChecker(policy).check({"ticker": "RKLB", "quantity": 0, "mode": "paper"})

    assert decision.approved is False
    assert decision.reason_code == "INVALID_QUANTITY"


def test_disabled_universe_still_scales_to_max_quantity() -> None:
    from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

    policy = RiskPolicy(
        allowed_universe={"AAPL"},
        universe_check_enabled=False,
        max_quantity_per_order=1,
    )
    decision = PreTradeChecker(policy).check({"ticker": "RKLB", "quantity": 5, "mode": "paper"})

    assert decision.approved is True
    assert decision.reason_code == "SCALED_DOWN_MAX_QUANTITY"
    assert decision.requested_quantity == 5
    assert decision.approved_quantity == 1
