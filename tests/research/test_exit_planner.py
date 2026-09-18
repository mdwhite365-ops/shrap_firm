"""Exits must be indistinguishable from entries by the time they leave here.

An exit that takes its own path is an exit with its own set of gates to be
missing. KI-030 was the Runner selling positions the account never held, and
#192-#199 were five further defects on the order path — so this rides
`trading.strategy.signal` and is built by the same payload function an entry is.
"""

from __future__ import annotations

import pytest

from shrap.research.strategy_runner.engine import RunnerSignalConfig
from shrap.research.strategy_runner.exit_planner import (
    SIDE_SELL,
    justification,
    plan_exits,
    strategy_by_account,
    suppressed_tickers,
)
from shrap.risk_compliance.risk_officer.exits import (
    REASON_INTRADAY_TAKE_PROFIT,
    REASON_STOP_LOSS,
    ExitRule,
    PositionPnL,
)

ACCOUNT = "PA3KQN57WVXY"
STRATEGY = "01KYNH9VKXVQXJ48T4MF306PHE"
CONFIG = RunnerSignalConfig()


def _pos(
    ticker: str, qty: float, *, plpc: float | None = 0.0, intraday: float | None = 0.0
) -> PositionPnL:
    return PositionPnL(ticker, qty, qty * 100.0, plpc, intraday)


class _Record:
    def __init__(self, strategy_id: str, account_id: str | None) -> None:
        self.strategy_id = strategy_id
        self.account_id = account_id


# --- it ships dark -------------------------------------------------------------


def test_an_unarmed_rule_plans_nothing() -> None:
    plan = plan_exits(
        account_id=ACCOUNT,
        strategy_id=STRATEGY,
        positions=[_pos("AAPL", 10.0, plpc=0.9)],
        rule=ExitRule(),
        config=CONFIG,
    )

    assert plan.is_empty
    assert plan.decisions == ()


# --- attribution ---------------------------------------------------------------


def test_an_unattributed_account_plans_nothing() -> None:
    """A signal that names no strategy cannot be audited to one.

    Better to leave a position open than to file the order against a strategy
    that did not ask for it.
    """

    for strategy_id in (None, "", "   "):
        plan = plan_exits(
            account_id=ACCOUNT,
            strategy_id=strategy_id,
            positions=[_pos("AAPL", 10.0, plpc=-0.5)],
            rule=ExitRule(stop_loss_pct=0.08),
            config=CONFIG,
        )
        assert plan.is_empty


def test_one_strategy_per_account_under_adr_0017() -> None:
    mapping = strategy_by_account(
        [_Record("s-1", "ACC-A"), _Record("s-2", "ACC-B"), _Record("s-3", None)]
    )

    assert mapping == {"ACC-A": "s-1", "ACC-B": "s-2"}


def test_a_double_booked_account_resolves_deterministically() -> None:
    """Row order must not decide which strategy owns an exit.

    An exit attributed to a different strategy on each pass makes the audit
    trail unreadable, which is worse than picking arbitrarily but consistently.
    """

    forward = strategy_by_account([_Record("s-b", "ACC"), _Record("s-a", "ACC")])
    reverse = strategy_by_account([_Record("s-a", "ACC"), _Record("s-b", "ACC")])

    assert forward == reverse == {"ACC": "s-a"}


# --- the payload ---------------------------------------------------------------


def test_the_exit_payload_is_shaped_exactly_like_an_entry() -> None:
    """Downstream must not be able to tell the two producers apart."""

    plan = plan_exits(
        account_id=ACCOUNT,
        strategy_id=STRATEGY,
        positions=[_pos("NVDA", 7.0, plpc=0.03, intraday=0.18)],
        rule=ExitRule(intraday_take_profit_pct=0.15),
        config=CONFIG,
    )

    assert len(plan.signals) == 1
    signal = plan.signals[0]
    assert signal.side == SIDE_SELL
    assert signal.ticker == "NVDA"
    assert signal.strategy_id == STRATEGY
    payload = signal.payload
    assert payload["account_id"] == ACCOUNT
    assert payload["side"] == "sell"
    assert payload["ticker"] == "NVDA"
    assert payload["quantity"] == 7.0
    assert payload["size_hint"] == 7.0
    assert set(payload) == {
        "strategy_id",
        "account_id",
        "ticker",
        "side",
        "size_hint",
        "quantity",
        "confidence",
        "urgency",
        "regime_label",
        "justification_text",
    }


def test_the_justification_says_how_far_past_the_threshold_it_went() -> None:
    """The first question anyone asks when an exit fires."""

    plan = plan_exits(
        account_id=ACCOUNT,
        strategy_id=STRATEGY,
        positions=[_pos("AMD", 5.0, plpc=-0.11)],
        rule=ExitRule(stop_loss_pct=0.08),
        config=CONFIG,
    )

    text = plan.signals[0].payload["justification_text"]
    assert REASON_STOP_LOSS in text
    assert "-11.00%" in text
    assert "8.00%" in text


def test_it_sells_the_whole_position() -> None:
    """A partial exit would leave a position no rule is tracking."""

    plan = plan_exits(
        account_id=ACCOUNT,
        strategy_id=STRATEGY,
        positions=[_pos("AAPL", 0.023641439, plpc=0.5)],
        rule=ExitRule(take_profit_pct=0.15),
        config=CONFIG,
    )

    assert plan.signals[0].payload["quantity"] == pytest.approx(0.023641439)


def test_signals_and_decisions_stay_aligned() -> None:
    """The caller zips them to log why each sell went out."""

    positions = [_pos("AAPL", 1.0, plpc=-0.5), _pos("NVDA", 2.0, plpc=0.0, intraday=0.9)]
    plan = plan_exits(
        account_id=ACCOUNT,
        strategy_id=STRATEGY,
        positions=positions,
        rule=ExitRule(stop_loss_pct=0.08, intraday_take_profit_pct=0.15),
        config=CONFIG,
    )

    assert len(plan.signals) == len(plan.decisions) == 2
    assert [s.ticker for s in plan.signals] == [d.ticker for d in plan.decisions]
    assert [d.reason for d in plan.decisions] == [
        REASON_STOP_LOSS,
        REASON_INTRADAY_TAKE_PROFIT,
    ]


def test_justification_formats_a_gain_with_a_sign() -> None:
    from shrap.risk_compliance.risk_officer.exits import ExitDecision

    text = justification(ExitDecision("NVDA", 7.0, REASON_INTRADAY_TAKE_PROFIT, 0.183, 0.15))

    assert "+18.30%" in text


# --- suppression ---------------------------------------------------------------


def test_a_recent_exit_suppresses_the_ticker() -> None:
    """Snapshots lag ~300s, so a sold position still reads as open."""

    assert suppressed_tickers({"AAPL": 100.0}, now=200.0, window_seconds=900.0) == {"AAPL"}


def test_suppression_expires() -> None:
    assert suppressed_tickers({"AAPL": 100.0}, now=1100.0, window_seconds=900.0) == set()


def test_suppression_is_per_ticker() -> None:
    """One name inside the window, one outside it, judged independently."""

    emitted = {"AAPL": 1_000.0, "NVDA": 10.0}

    assert suppressed_tickers(emitted, now=1_100.0, window_seconds=900.0) == {"AAPL"}


def test_nothing_emitted_suppresses_nothing() -> None:
    assert suppressed_tickers({}, now=1.0, window_seconds=900.0) == set()
