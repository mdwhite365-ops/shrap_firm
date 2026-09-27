"""Shadow forward test: decided once, settled from what was stored, accounted like a backtest."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, timedelta

import pytest

from shrap.research.shadow_ledger import (
    LedgerRow,
    PeriodResult,
    decide,
    run_strategy,
    score,
    settle_period,
)
from shrap.research.strategy_evaluator.costs import CostModel
from shrap.research.strategy_evaluator.engine import _adv_dollar_series, run_backtest
from shrap.research.strategy_evaluator.strategy import BarSample, PanelWindow, PricePanel
from shrap.research.strategy_registry import STATUS_KILLED, StrategyRecord

START = date(2026, 1, 5)


def _bars(closes: list[float]) -> list[BarSample]:
    return [
        BarSample(
            session_date=START + timedelta(days=i),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=1_000_000.0,
        )
        for i, c in enumerate(closes)
    ]


def _panel(n: int) -> PricePanel:
    return PricePanel.from_bars(
        {
            "A": _bars([100.0 * 1.01**i for i in range(n)]),
            "B": _bars([100.0 * 0.99**i for i in range(n)]),
        }
    )


@dataclass(frozen=True)
class Fixed:
    weights: Mapping[str, float]
    name: str = "fixed"
    warmup: int = 2

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        return dict(self.weights)


class MemoryLedger:
    def __init__(self) -> None:
        self.data: dict[tuple[str, date], LedgerRow] = {}

    async def rows(self, strategy_id: str) -> list[LedgerRow]:
        return sorted(
            (r for (sid, _), r in self.data.items() if sid == strategy_id),
            key=lambda r: r.session_date,
        )

    async def record_decision(self, row: LedgerRow) -> bool:
        key = (row.strategy_id, row.session_date)
        if key in self.data:
            return False
        self.data[key] = row
        return True

    async def settle(
        self,
        strategy_id: str,
        session_date: date,
        next_date: date,
        result: PeriodResult,
        benchmark_return: float,
    ) -> None:
        key = (strategy_id, session_date)
        self.data[key] = replace(
            self.data[key],
            next_session_date=next_date,
            net_return=result.net,
            gross_return=result.gross,
            cost=result.cost,
            benchmark_return=benchmark_return,
        )


RECORD = StrategyRecord(
    strategy_id="s1",
    name="shadowed",
    version=1,
    archetype="technical-catalyst",
    status=STATUS_KILLED,
    source="test",
    thesis="t",
    anchor={},
    tickers={"long": ["A", "B"]},
    spec={},
    spec_hash="h1",
    regime_sizing_modifier=None,
    kill_criteria=["x"],
    code_ref=None,
    created_at=None,
    updated_at=None,
)


def _factory(weights: Mapping[str, float]):
    return lambda record, tickers: Fixed(weights)


async def test_a_decision_is_recorded_once_per_session() -> None:
    store = MemoryLedger()
    panel = _panel(10)
    for _ in range(2):
        await run_strategy(RECORD, ["A", "B"], panel, store, _factory({"A": 1.0}), CostModel())
    (row,) = await store.rows("s1")
    assert row.session_date == panel.dates[-1]
    assert not row.settled


async def test_settlement_uses_the_stored_weights_not_todays_code() -> None:
    """The out-of-sample guarantee: a strategy changed after deciding cannot rewrite the record."""

    store = MemoryLedger()
    await run_strategy(RECORD, ["A", "B"], _panel(10), store, _factory({"A": 1.0}), CostModel())
    # Next session exists now, and the strategy's code has since changed to hold B.
    result = await run_strategy(
        RECORD, ["A", "B"], _panel(11), store, _factory({"B": 1.0}), CostModel()
    )

    assert result.settled == 1
    first, second = await store.rows("s1")
    assert first.net_return is not None and first.net_return > 0.0  # A rose; B would have lost
    assert second.weights == {"A": 0.0, "B": 1.0}  # today's decision is the new code's


async def test_nothing_is_recorded_inside_warmup() -> None:
    store = MemoryLedger()
    result = await run_strategy(
        RECORD, ["A", "B"], _panel(1), store, _factory({"A": 1.0}), CostModel()
    )
    assert result.skipped is not None
    assert store.data == {}


def test_one_settled_period_equals_the_backtest_engines_period() -> None:
    """Same accounting, by construction — so a shadow return continues a backtest."""

    panel = _panel(30)
    costs = CostModel()
    adv = _adv_dollar_series(panel, costs.adv_window)
    weights = {"A": 0.6, "B": 0.4}
    previous = {"A": 1.0, "B": 0.0}

    class Switch:
        name = "switch"
        warmup = 1

        def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
            return previous if window.current_index == 20 else weights

    engine = run_backtest(panel, Switch(), costs, first_period=20, last_period=21, execution_lag=0)
    shadow = settle_period(panel, 21, weights, previous, costs, adv)

    assert shadow.net == pytest.approx(engine.daily_returns[1])


def test_a_gap_enters_from_flat_and_pays_the_full_entry() -> None:
    panel = _panel(30)
    costs = CostModel()
    adv = _adv_dollar_series(panel, costs.adv_window)
    held = settle_period(panel, 21, {"A": 1.0}, {"A": 1.0}, costs, adv)
    after_gap = settle_period(panel, 21, {"A": 1.0}, {}, costs, adv)

    assert held.cost == 0.0
    assert after_gap.cost > 0.0
    assert after_gap.gross == held.gross


def test_score_reports_an_error_bar_as_wide_as_the_sample_deserves() -> None:
    rows = [(START + timedelta(days=i), 0.002 if i % 2 else -0.001, 0.0) for i in range(60)]
    s = score("s1", rows)

    assert s.days == 60
    assert s.information_ratio is not None and s.information_ratio > 0.0
    # Sixty days: the annualised SE is about 2, so this ranks, it does not prove.
    assert s.ir_standard_error == pytest.approx(2.0, abs=0.6)


def test_decide_names_every_ticker() -> None:
    assert decide(Fixed({"A": 1.0}), _panel(5)) == {"A": 1.0, "B": 0.0}


def test_the_session_in_progress_is_invisible_to_the_ledger() -> None:
    """Found on the first live dry run: a mid-session partial bar is not a close."""

    from datetime import UTC, datetime

    from shrap.research.shadow_ledger_cli import last_complete_session_bound

    # 12:54 PT on 2026-09-24 is 15:54 ET the same day: the 24th is still open.
    assert last_complete_session_bound(datetime(2026, 9, 24, 19, 54, tzinfo=UTC)) == date(
        2026, 9, 23
    )
    # 01:00 UTC on the 25th is still the evening of the 24th in New York.
    assert last_complete_session_bound(datetime(2026, 9, 25, 1, 0, tzinfo=UTC)) == date(2026, 9, 23)
