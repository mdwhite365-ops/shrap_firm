"""Kill switches, and the limits that set them.

The monitor is the part of this card that answers the observation which prompted
it: the firm's first evaluation reported a **53.88% max drawdown** and nothing in
the running system would have noticed. The only automatic control on the order
path was a 100-share per-order cap, and the only kill switch was an environment
variable a human had to set.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from shrap.risk_compliance.risk_officer.limits import PortfolioLimits
from shrap.risk_compliance.risk_officer.monitor import (
    SEVERITY_BREACH,
    SEVERITY_INFO,
    SEVERITY_WARN,
    EquityPoint,
    check_daily_loss,
    check_strategy_drawdown,
    peak_drawdown,
    session_loss,
)
from shrap.risk_compliance.risk_officer.switches import (
    ACTOR_HUMAN,
    SWITCH_DAILY_LOSS,
    SWITCH_MANUAL,
    SwitchBoard,
    blocks_intent,
    reduces_position,
    strategy_switch,
)

NOW = datetime(2026, 7, 29, 15, 0, tzinfo=UTC)
SESSION = date(2026, 7, 29)
LIMITS = PortfolioLimits()


def _curve(*equities: float, start: datetime = NOW) -> list[EquityPoint]:
    return [
        EquityPoint(at=start + timedelta(minutes=5 * i), equity=e) for i, e in enumerate(equities)
    ]


# --- switch mechanics ---------------------------------------------------------


def test_setting_is_idempotent_so_a_repeating_breach_writes_one_transition() -> None:
    """The monitor observes the same breach every heartbeat. Without this it
    would write one audit row per pass and publish one event per pass."""

    board = SwitchBoard()

    first = board.set(SWITCH_DAILY_LOSS, actor="m", reason="r", at=NOW)
    second = board.set(SWITCH_DAILY_LOSS, actor="m", reason="r", at=NOW)

    assert first is not None
    assert second is None
    assert board.is_active(SWITCH_DAILY_LOSS)


def test_clearing_an_inactive_switch_is_a_no_op() -> None:
    board = SwitchBoard()

    assert board.clear(SWITCH_MANUAL, actor=ACTOR_HUMAN, reason="r", at=NOW) is None


def test_a_firm_wide_switch_halts_every_strategy() -> None:
    board = SwitchBoard()
    board.set(SWITCH_DAILY_LOSS, actor="m", reason="r", at=NOW)

    assert board.blocking_switch(["any-strategy"]) == SWITCH_DAILY_LOSS


def test_a_strategy_switch_halts_only_that_strategy() -> None:
    board = SwitchBoard()
    board.set(strategy_switch("S1"), actor="m", reason="r", at=NOW)

    assert board.blocking_switch(["S1"]) == strategy_switch("S1")
    assert board.blocking_switch(["S2"]) is None


def test_the_broadest_cause_is_reported_first() -> None:
    board = SwitchBoard()
    board.set(strategy_switch("S1"), actor="m", reason="r", at=NOW)
    board.set(SWITCH_MANUAL, actor=ACTOR_HUMAN, reason="r", at=NOW)

    assert board.blocking_switch(["S1"]) == SWITCH_MANUAL


# --- a halt must not trap the book -------------------------------------------


def test_a_halted_book_may_still_reduce_a_position() -> None:
    """The spec: "existing positions follow each strategy's exit logic". A kill
    switch that also blocked sells would trap the firm in the position that
    triggered it, which is the opposite of containment."""

    board = SwitchBoard()
    board.set(SWITCH_DAILY_LOSS, actor="m", reason="r", at=NOW)

    blocked = blocks_intent(
        board, strategy_ids=["S1"], current_market_value=1_000.0, delta_market_value=-400.0
    )

    assert blocked is None


def test_a_halted_book_may_not_open_a_new_position() -> None:
    board = SwitchBoard()
    board.set(SWITCH_DAILY_LOSS, actor="m", reason="r", at=NOW)

    blocked = blocks_intent(
        board, strategy_ids=["S1"], current_market_value=0.0, delta_market_value=1_000.0
    )

    assert blocked == SWITCH_DAILY_LOSS


def test_selling_through_flat_into_a_short_is_not_a_reduction() -> None:
    """Crossing zero opens a new position in the other direction, and a halted
    book may not open one."""

    assert reduces_position(1_000.0, -400.0) is True
    assert reduces_position(1_000.0, -1_000.0) is True
    assert reduces_position(1_000.0, -1_500.0) is False
    assert reduces_position(0.0, -500.0) is False


# --- the drawdown that started this ------------------------------------------


def test_a_fifty_four_percent_drawdown_is_a_breach() -> None:
    """The number from the firm's first evaluation, against the limit chosen to
    catch it."""

    points = _curve(10_000.0, 12_000.0, 5_536.0)  # -53.88% from the peak

    observation = check_strategy_drawdown(points, LIMITS, strategy_id="S1")

    assert observation is not None
    assert observation.observed > 0.53
    assert observation.severity == SEVERITY_BREACH


def test_drawdown_is_measured_from_the_peak_not_the_opening_balance() -> None:
    """An account that doubles and halves is flat on deposits and in a 50%
    drawdown. Only the second describes the risk being run."""

    assert peak_drawdown(_curve(10_000.0, 20_000.0, 10_000.0)) == 0.5


def test_a_recovering_account_keeps_its_worst_drawdown() -> None:
    assert peak_drawdown(_curve(10_000.0, 6_000.0, 11_000.0)) == 0.4


def test_a_warning_fires_before_the_halt() -> None:
    """So an account approaching a halt is visible before it halts."""

    points = _curve(10_000.0, 8_000.0)  # -20%, against a 25% limit

    observation = check_strategy_drawdown(points, LIMITS, strategy_id="S1")

    assert observation is not None
    assert observation.severity == SEVERITY_WARN


def test_an_ordinary_dip_is_neither() -> None:
    observation = check_strategy_drawdown(_curve(10_000.0, 9_800.0), LIMITS, strategy_id="S1")

    assert observation is not None
    assert observation.severity == SEVERITY_INFO


def test_a_single_observation_is_not_a_drawdown() -> None:
    assert peak_drawdown(_curve(10_000.0)) is None


# --- daily loss ---------------------------------------------------------------


def test_the_session_loss_is_measured_from_the_first_snapshot_of_the_day() -> None:
    points = _curve(10_000.0, 9_700.0)

    assert session_loss(points, SESSION) == 0.03


def test_a_session_gain_reports_zero_loss_rather_than_a_negative_one() -> None:
    assert session_loss(_curve(10_000.0, 10_500.0), SESSION) == 0.0


def test_a_two_percent_session_loss_breaches() -> None:
    observation = check_daily_loss(_curve(10_000.0, 9_800.0), SESSION, LIMITS, account_id="A1")

    assert observation is not None
    assert observation.severity == SEVERITY_BREACH
    assert observation.account_id == "A1"


def test_only_the_current_session_counts() -> None:
    """Yesterday's equity is not today's opening balance."""

    yesterday = _curve(10_000.0, start=NOW - timedelta(days=1))
    today = _curve(9_000.0, 8_950.0)

    assert session_loss([*yesterday, *today], SESSION) == pytest.approx(0.00555, abs=1e-4)


def test_one_observation_in_a_session_is_not_a_change() -> None:
    """Treating a single reading as a 0% move would report "no loss" for an
    account nobody has measured twice."""

    assert session_loss(_curve(10_000.0), SESSION) is None
