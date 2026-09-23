"""Held positions are resized toward target, using the Risk Officer's recorded scale.

The live case (2026-09-23): momentum on PA3HEG2CLXLU held six names at ~$190 —
bought at 0.25 x 0.75 of a $1,000 slot before the 2026-09-18 stage ruling — and
three at ~$600 bought after it. The engine only acted on flat <-> invested, so
the old six would have stayed small until they left the top ten.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest

from shrap.research.strategy_evaluator.strategy import BarSample, PanelWindow
from shrap.research.strategy_registry import STATUS_PAPER, StrategyRecord
from shrap.research.strategy_runner.engine import (
    SIDE_BUY,
    SIDE_SELL,
    RunnerSignalConfig,
    StrategyInput,
    TargetState,
    plan_resize,
    plan_session,
)

SESSION = date(2026, 9, 23)
NOW = datetime(2026, 9, 23, 13, 30, tzinfo=UTC)
PRICE = 10.0
EQUITY = 1_000.0  # one $1,000 slot, so weight 1.0 buys 100 full-scale shares
CONFIG = RunnerSignalConfig(max_quantity=1_000_000)


# --- the pure decision --------------------------------------------------------


def test_a_position_bought_under_the_old_stage_is_topped_up() -> None:
    """18.75 held against a 60-share target: request 68.75 so the 0.6 fill is 41.25."""

    resize = plan_resize(
        full_scale_quantity=100.0,
        held=18.75,
        price=PRICE,
        buy_scale=0.6,
        band=0.25,
        min_notional=25.0,
    )

    assert resize is not None
    assert resize.side == SIDE_BUY
    assert resize.quantity == pytest.approx(68.75)
    assert resize.quantity * 0.6 + 18.75 == pytest.approx(60.0)


def test_drift_inside_the_band_is_left_alone() -> None:
    """55 against 60 is 8% off — price noise, not a mis-sized position."""

    assert (
        plan_resize(
            full_scale_quantity=100.0,
            held=55.0,
            price=PRICE,
            buy_scale=0.6,
            band=0.25,
            min_notional=25.0,
        )
        is None
    )


def test_a_gap_worth_less_than_the_floor_is_left_alone() -> None:
    assert (
        plan_resize(
            full_scale_quantity=1.0,
            held=0.1,
            price=PRICE,
            buy_scale=0.6,
            band=0.25,
            min_notional=25.0,
        )
        is None
    )


def test_an_oversized_position_is_trimmed_by_the_excess_unscaled() -> None:
    """Sells are not scaled by the Officer, so the trim is the excess itself."""

    resize = plan_resize(
        full_scale_quantity=100.0,
        held=90.0,
        price=PRICE,
        buy_scale=0.6,
        band=0.25,
        min_notional=25.0,
    )

    assert resize is not None
    assert resize.side == SIDE_SELL
    assert resize.quantity == pytest.approx(30.0)


def test_a_top_up_never_requests_more_than_a_full_entry() -> None:
    """A scale that dropped sharply must not turn a top-up into a huge order."""

    resize = plan_resize(
        full_scale_quantity=100.0,
        held=1.0,
        price=PRICE,
        buy_scale=0.05,
        band=0.25,
        min_notional=1.0,
    )

    assert resize is not None
    assert resize.quantity <= 100.0


@pytest.mark.parametrize("scale", [0.0, -1.0])
def test_a_meaningless_scale_resizes_nothing(scale: float) -> None:
    assert (
        plan_resize(
            full_scale_quantity=100.0,
            held=10.0,
            price=PRICE,
            buy_scale=scale,
            band=0.25,
            min_notional=1.0,
        )
        is None
    )


# --- wired into the planner ---------------------------------------------------


@dataclass(frozen=True)
class _Hold:
    name: str = "hold"
    warmup: int = 3

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        return {"MU": 1.0}


def _item() -> StrategyInput:
    record = StrategyRecord(
        strategy_id="mom",
        name="momentum",
        version=1,
        archetype="technical-catalyst",
        status=STATUS_PAPER,
        source="test",
        thesis="test",
        anchor=None,
        tickers={"long": ["MU"]},
        spec={},
        spec_hash="h",
        regime_sizing_modifier=None,
        kill_criteria=["x"],
        code_ref=None,
        created_at=None,
        updated_at=None,
    )
    bars = [
        BarSample(
            session_date=SESSION - timedelta(days=5 - i),
            open=PRICE,
            high=PRICE,
            low=PRICE,
            close=PRICE,
            volume=1_000.0,
        )
        for i in range(5)
    ]
    return StrategyInput(record=record, tickers=["MU"], bars_by_ticker={"MU": bars})


def _plan(*, held: float, buy_scale: float | None, config: RunnerSignalConfig = CONFIG):
    (plan,) = plan_session(
        session_date=SESSION,
        now=NOW,
        strategies=[_item()],
        stored_state={("mom", "MU"): TargetState(1.0, SIDE_BUY, SESSION - timedelta(days=1))},
        held={"MU": held},
        factory=lambda record, tickers: _Hold(),
        config=config,
        regime_label="late-cycle-melt-up",
        equity=EQUITY,
        account_id="PA3HEG2CLXLU",
        buy_scale=buy_scale,
    )
    return plan


def test_the_planner_tops_up_a_held_position_and_says_why() -> None:
    plan = _plan(held=18.75, buy_scale=0.6)

    (signal,) = plan.signals
    assert signal.side == SIDE_BUY
    assert signal.payload["quantity"] == pytest.approx(68.75)
    assert "resized" in signal.payload["justification_text"]
    # The idempotency stamp still lands, so a second pass this session is a no-op.
    (write,) = plan.state_writes
    assert write.last_session_date == SESSION


def test_without_a_recorded_scale_nothing_is_resized_and_it_says_so() -> None:
    plan = _plan(held=18.75, buy_scale=None)

    assert plan.signals == ()
    assert any("no recorded buy scale" in note for note in plan.sizing_notes)


def test_resizing_can_be_turned_off() -> None:
    plan = _plan(
        held=18.75,
        buy_scale=0.6,
        config=RunnerSignalConfig(max_quantity=1_000_000, resize_band=None),
    )

    assert plan.signals == ()
