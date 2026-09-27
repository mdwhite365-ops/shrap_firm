"""The live Runner's panel carries what the Evaluator's panel carries.

Until 2026-09-24 the Runner built its panel from bars alone, so a strategy that
ranked on market cap was measured with market caps and traded without them.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta

from shrap.research.strategy_evaluator.strategy import BarSample, PanelWindow
from shrap.research.strategy_registry import STATUS_PAPER, StrategyRecord
from shrap.research.strategy_runner.engine import RunnerSignalConfig, StrategyInput, plan_session

SESSION = date(2026, 9, 24)


class NeedsCapAndRevenue:
    name = "needs-both"
    warmup = 2

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        cap = window.market_caps("A")[-1]
        revenue = window.fundamental("A", "revenue")
        return {"A": 1.0 if not math.isnan(cap) and revenue is not None else 0.0}


def _input(**extras: object) -> StrategyInput:
    bars = [
        BarSample(SESSION - timedelta(days=5 - i), 10.0, 10.0, 10.0, 10.0, 1e6) for i in range(5)
    ]
    record = StrategyRecord(
        strategy_id="s",
        name="s",
        version=1,
        archetype="technical-catalyst",
        status=STATUS_PAPER,
        source="t",
        thesis="t",
        anchor=None,
        tickers={"long": ["A"]},
        spec={},
        spec_hash="h",
        regime_sizing_modifier=None,
        kill_criteria=["x"],
        code_ref=None,
        created_at=None,
        updated_at=None,
    )
    return StrategyInput(record=record, tickers=["A"], bars_by_ticker={"A": bars}, **extras)  # type: ignore[arg-type]


def _signals(item: StrategyInput) -> int:
    (plan,) = plan_session(
        session_date=SESSION,
        now=datetime(2026, 9, 24, 13, 30, tzinfo=UTC),
        strategies=[item],
        stored_state={},
        held={},
        factory=lambda record, tickers: NeedsCapAndRevenue(),
        config=RunnerSignalConfig(max_quantity=1_000_000),
        regime_label=None,
        equity=10_000.0,
        account_id="PA3TEST",
    )
    return len(plan.signals)


def test_without_shares_or_fundamentals_the_strategy_sees_nothing() -> None:
    assert _signals(_input()) == 0


def test_with_them_the_live_panel_matches_the_backtest_panel() -> None:
    item = _input(
        shares_by_ticker={"A": [(date(2026, 1, 1), 1e9)]},
        fundamentals_by_ticker={"A": {"revenue": [(date(2026, 1, 1), date(2025, 9, 27), 4e11)]}},
    )
    assert _signals(item) == 1
