"""The exits the firm could not take.

Grounded in what the live accounts actually did: every fill between 09:30:03 and
09:30:07 ET, $74 and $55 earned over weeks, and no mechanism anywhere that could
sell a position that spiked. These tests pin the rule's safety properties first
and its arithmetic second, because the dangerous failure here is not a missed
exit — it is an exit that fires when it should not, or twice.
"""

from __future__ import annotations

import re

import pytest

from shrap.risk_compliance.risk_officer.exits import (
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TAKE_PROFIT_PCT,
    REASON_INTRADAY_STOP_LOSS,
    REASON_INTRADAY_TAKE_PROFIT,
    REASON_STOP_LOSS,
    REASON_TAKE_PROFIT,
    ExitRule,
    PositionPnL,
    evaluate_exits,
)


def _pos(
    ticker: str = "AAPL",
    quantity: float = 10.0,
    *,
    plpc: float | None = 0.0,
    intraday: float | None = 0.0,
) -> PositionPnL:
    return PositionPnL(
        ticker=ticker,
        quantity=quantity,
        market_value=quantity * 100.0,
        unrealized_plpc=plpc,
        unrealized_intraday_plpc=intraday,
    )


# --- it ships dark -------------------------------------------------------------


def test_the_firm_has_not_calibrated_the_thresholds() -> None:
    """Picking these is a ruling, not a default. They must not be guessed."""

    assert DEFAULT_TAKE_PROFIT_PCT is None
    assert DEFAULT_STOP_LOSS_PCT is None


def test_an_unarmed_rule_never_exits_anything() -> None:
    """Merging this module must not sell a single share.

    The position below is up 500% and down 90% intraday at once — impossible,
    and deliberately so: nothing about the position should matter when no
    threshold is set.
    """

    rule = ExitRule()

    assert not rule.is_armed
    assert evaluate_exits([_pos(plpc=5.0, intraday=-0.9)], rule) == []


def test_arming_one_threshold_arms_the_rule() -> None:
    assert ExitRule(take_profit_pct=0.15).is_armed
    assert ExitRule(stop_loss_pct=0.08).is_armed
    assert ExitRule(intraday_take_profit_pct=0.10).is_armed
    assert ExitRule(intraday_stop_loss_pct=0.05).is_armed


@pytest.mark.parametrize(
    "field",
    ["take_profit_pct", "stop_loss_pct", "intraday_take_profit_pct", "intraday_stop_loss_pct"],
)
def test_a_negative_threshold_is_refused(field: str) -> None:
    """A stop of -0.08 would fire when the position is UP.

    Thresholds are positive magnitudes and the sign is applied by the rule. An
    operator who writes the stop the way they say it out loud must get an error,
    not an inverted rule that sells every winner.
    """

    with pytest.raises(ValueError, match="invert the rule"):
        ExitRule(**{field: -0.08})


@pytest.mark.parametrize(
    "field",
    ["take_profit_pct", "stop_loss_pct", "intraday_take_profit_pct", "intraday_stop_loss_pct"],
)
def test_a_zero_threshold_is_refused(field: str) -> None:
    """Zero would exit every position that is not exactly flat."""

    with pytest.raises(ValueError, match=re.escape("must be > 0.0")):
        ExitRule(**{field: 0.0})


# --- the safety properties -----------------------------------------------------


def test_unknown_pnl_is_skipped_rather_than_read_as_flat() -> None:
    """The difference between "checked and fine" and "could not check".

    Treating a missing broker field as 0.0 makes every threshold look
    un-breached, which is the firm's recurring failure shape: a silent no-op
    that looks like a clean pass.
    """

    rule = ExitRule(take_profit_pct=0.10, stop_loss_pct=0.05)

    assert evaluate_exits([_pos(plpc=None, intraday=None)], rule) == []


def test_a_missing_since_entry_pnl_still_allows_an_intraday_exit() -> None:
    """Partial data is not no data."""

    decisions = evaluate_exits(
        [_pos(plpc=None, intraday=0.12)],
        ExitRule(take_profit_pct=0.10, intraday_take_profit_pct=0.10),
    )

    assert [d.reason for d in decisions] == [REASON_INTRADAY_TAKE_PROFIT]


def test_a_ticker_already_exiting_is_not_sold_twice() -> None:
    """Without this the rule re-fires every tick until the fill lands.

    Position snapshots refresh only every ~300s, so the position still reads as
    open for minutes after the sell is on the wire. Re-emitting would oversell
    into a short — the shape of KI-030, where the Runner sold what the account
    did not hold.
    """

    rule = ExitRule(take_profit_pct=0.10)
    positions = [_pos("AAPL", plpc=0.20), _pos("NVDA", plpc=0.20)]

    decisions = evaluate_exits(positions, rule, suppressed={"AAPL"})

    assert [d.ticker for d in decisions] == ["NVDA"]


def test_shorts_are_left_alone() -> None:
    """The broker's plpc sign convention for a short is unverified here.

    Every strategy in the registry is long-only, so acting on an unverified sign
    could double a losing short instead of closing it. Failing to act is
    recoverable; inverting a stop is not.
    """

    rule = ExitRule(take_profit_pct=0.10, stop_loss_pct=0.05)

    assert evaluate_exits([_pos(quantity=-10.0, plpc=0.5)], rule) == []
    assert evaluate_exits([_pos(quantity=-10.0, plpc=-0.5)], rule) == []


def test_a_closed_position_is_not_sold() -> None:
    assert evaluate_exits([_pos(quantity=0.0, plpc=0.9)], ExitRule(take_profit_pct=0.1)) == []


def test_at_most_one_exit_per_position() -> None:
    """Two sells for one holding would oversell into a short.

    This position breaches all four thresholds at once.
    """

    decisions = evaluate_exits(
        [_pos(plpc=-0.99, intraday=-0.99)],
        ExitRule(
            take_profit_pct=0.01,
            stop_loss_pct=0.01,
            intraday_take_profit_pct=0.01,
            intraday_stop_loss_pct=0.01,
        ),
    )

    assert len(decisions) == 1


# --- the arithmetic ------------------------------------------------------------


def test_a_spike_is_taken() -> None:
    """The case Mike named: it spiked during the day and nothing happened."""

    decisions = evaluate_exits(
        [_pos("NVDA", quantity=7.0, plpc=0.03, intraday=0.18)],
        ExitRule(intraday_take_profit_pct=0.15),
    )

    assert len(decisions) == 1
    exit_ = decisions[0]
    assert exit_.ticker == "NVDA"
    assert exit_.reason == REASON_INTRADAY_TAKE_PROFIT
    assert exit_.quantity == 7.0
    assert exit_.measured_pct == pytest.approx(0.18)
    assert exit_.threshold_pct == pytest.approx(0.15)


def test_a_loss_is_cut() -> None:
    decisions = evaluate_exits([_pos(plpc=-0.09)], ExitRule(stop_loss_pct=0.08))

    assert [d.reason for d in decisions] == [REASON_STOP_LOSS]
    assert decisions[0].measured_pct == pytest.approx(-0.09)


def test_the_threshold_is_inclusive() -> None:
    """Exactly at the stop is a breach. An exit that needs one more tick of
    adverse move to fire is a stop that is not where it says it is."""

    assert evaluate_exits([_pos(plpc=-0.08)], ExitRule(stop_loss_pct=0.08))
    assert evaluate_exits([_pos(plpc=0.15)], ExitRule(take_profit_pct=0.15))


def test_a_position_inside_both_thresholds_is_held() -> None:
    rule = ExitRule(take_profit_pct=0.15, stop_loss_pct=0.08)

    assert evaluate_exits([_pos(plpc=0.14, intraday=0.02)], rule) == []
    assert evaluate_exits([_pos(plpc=-0.07, intraday=-0.01)], rule) == []


def test_the_stop_wins_when_both_could_fire() -> None:
    """Cutting a loss beats taking a profit on the same holding.

    A position can be up since entry and collapsing today. Which rule fires
    decides whether the firm sells into strength or into weakness, so the
    precedence is pinned rather than incidental.
    """

    decisions = evaluate_exits(
        [_pos(plpc=-0.20, intraday=0.30)],
        ExitRule(take_profit_pct=0.10, stop_loss_pct=0.10, intraday_take_profit_pct=0.10),
    )

    assert [d.reason for d in decisions] == [REASON_STOP_LOSS]


def test_since_entry_outranks_intraday() -> None:
    decisions = evaluate_exits(
        [_pos(plpc=0.40, intraday=0.20)],
        ExitRule(take_profit_pct=0.15, intraday_take_profit_pct=0.15),
    )

    assert [d.reason for d in decisions] == [REASON_TAKE_PROFIT]


def test_an_intraday_collapse_fires_when_the_position_is_still_green() -> None:
    """Flat-to-up since entry, down hard today. Nothing else in the firm sees
    this: the daily re-rank scores the trailing window, not today."""

    decisions = evaluate_exits(
        [_pos(plpc=0.05, intraday=-0.11)],
        ExitRule(stop_loss_pct=0.08, intraday_stop_loss_pct=0.10),
    )

    assert [d.reason for d in decisions] == [REASON_INTRADAY_STOP_LOSS]


def test_every_breaching_position_exits_in_one_pass() -> None:
    """The property the module docstring warns about, pinned so it is a
    decision rather than a surprise: a market-wide drop liquidates the book."""

    positions = [_pos(t, plpc=-0.30) for t in ("AAPL", "NVDA", "MSFT", "AMD")]

    decisions = evaluate_exits(positions, ExitRule(stop_loss_pct=0.08))

    assert len(decisions) == 4
    assert all(d.reason == REASON_STOP_LOSS for d in decisions)


def test_an_empty_book_is_not_an_error() -> None:
    assert evaluate_exits([], ExitRule(stop_loss_pct=0.08)) == []


def test_dust_is_not_worth_an_order() -> None:
    """Measured on the live book, 2026-09-17.

    Four of fifteen positions sit at 1e-09 shares with a market value of zero,
    and `U` holds 0.012648483 shares worth $0.53 — the residue KI-033 was
    about. The Pre-Trade Checker refuses anything under $1, so emitting these
    would add roughly 1,500 refused orders a day to the log and bury a real
    exit. The threshold is the Checker's own, not a second one.
    """

    from shrap.risk_compliance.risk_officer.exits import PositionPnL
    from shrap.risk_compliance.risk_officer.sizing import MIN_TRADEABLE_NOTIONAL

    rule = ExitRule(stop_loss_pct=0.08, take_profit_pct=0.10)
    dust = [
        PositionPnL("AVGO", 1e-09, 0.0, -0.5, -0.5),
        PositionPnL("U", 0.012648483, 0.528368, -0.5, -0.5),
    ]

    assert MIN_TRADEABLE_NOTIONAL == 1.0
    assert evaluate_exits(dust, rule) == []


def test_a_real_position_just_over_the_floor_still_exits() -> None:
    """The filter must not swallow a genuine holding."""

    from shrap.risk_compliance.risk_officer.exits import PositionPnL

    real = [PositionPnL("COIN", 0.986644654, 171.034851, -0.20, -0.02)]

    assert len(evaluate_exits(real, ExitRule(stop_loss_pct=0.08))) == 1
