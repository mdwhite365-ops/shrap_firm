"""A cross-sectional universe that grows as names list.

The panel used to align on the intersection of session dates, so one
recently-listed member truncated everyone's history: ETHA (listed 2024-07) cut
the 50-name launch panel from about five years to about two, discarding bars
SPY had sitting in the same table. Nothing was missing — ETHA cannot have bars
before it listed, and the intersection propagated that one fact to all fifty.

The property these tests pin is that a strategy never asks how old a name is.
It asks whether it can compute its signal, and a name that has not traded
enough times answers no by itself.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, timedelta

from shrap.research.strategy_evaluator.benchmark import EqualWeightBuyAndHold
from shrap.research.strategy_evaluator.costs import CostModel
from shrap.research.strategy_evaluator.cross_sectional import (
    CrossSectionalMomentumStrategy,
    CrossSectionalTrendStrategy,
)
from shrap.research.strategy_evaluator.engine import run_backtest
from shrap.research.strategy_evaluator.strategy import BarSample, PanelWindow, PricePanel

_START = date(2020, 1, 6)
_ZERO_COST = CostModel(
    commission_bps=0.0,
    half_spread_bps=0.0,
    slippage_bps_per_adv=0.0,
    borrow_rate_annual=0.0,
)


def _bars(closes: list[float], *, offset: int = 0, volume: float = 1.0e9) -> list[BarSample]:
    return [
        BarSample(
            session_date=_START + timedelta(days=offset + i),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=volume,
        )
        for i, c in enumerate(closes)
    ]


# --- the panel ---------------------------------------------------------------


def test_a_late_listing_no_longer_truncates_everyone_else() -> None:
    """The ETHA case. `OLD` keeps all 300 of its bars; `NEW` contributes 50."""

    panel = PricePanel.from_bars(
        {
            "OLD": _bars([100.0] * 300),
            "NEW": _bars([50.0] * 50, offset=250),
        }
    )

    assert panel.n_bars == 300
    assert panel.dates[0] == _START
    # NEW is absent for the first 250 dates and present thereafter.
    assert not panel.is_live("NEW", 0)
    assert not panel.is_live("NEW", 249)
    assert panel.is_live("NEW", 250)
    assert panel.is_live("OLD", 0)


def test_absent_bars_are_nan_not_zero() -> None:
    """A zero close would book a -100% return the day before a name lists, and
    a forward-fill would invent a flat price series. `nan` propagates loudly
    instead, so a consumer that forgets to check `is_live` fails visibly."""

    panel = PricePanel.from_bars({"OLD": _bars([100.0] * 10), "NEW": _bars([50.0] * 5, offset=5)})

    assert math.isnan(panel.closes["NEW"][0])
    assert math.isnan(panel.volumes["NEW"][0])
    assert panel.closes["NEW"][5] == 50.0


def test_the_window_hands_a_strategy_only_real_bars() -> None:
    """This is the whole mechanism. At bar 259 the panel is 260 bars deep, but
    NEW has traded 10 times — so a rule needing 20 bars skips it without ever
    consulting a listing date."""

    panel = PricePanel.from_bars(
        {"OLD": _bars([100.0] * 300), "NEW": _bars([50.0] * 50, offset=250)}
    )
    window = panel.window(259)

    assert len(window.dates()) == 260
    assert len(window.closes("OLD")) == 260
    assert len(window.closes("NEW")) == 10
    assert all(not math.isnan(c) for c in window.closes("NEW"))


def test_live_tickers_tracks_the_investable_universe() -> None:
    panel = PricePanel.from_bars(
        {"OLD": _bars([100.0] * 300), "NEW": _bars([50.0] * 50, offset=250)}
    )

    assert panel.window(249).live_tickers == ("OLD",)
    assert set(panel.window(250).live_tickers) == {"OLD", "NEW"}
    # `tickers` stays complete so a strategy can express "flat in NEW".
    assert set(panel.window(0).tickers) == {"OLD", "NEW"}


def test_a_single_ticker_panel_is_unchanged_by_raggedness() -> None:
    """Every strategy already on record ran on one ticker. Union == intersection
    there, so their results must be bit-identical."""

    panel = PricePanel.from_bars({"AAA": _bars([100.0 + i for i in range(50)])})

    assert panel.n_bars == 50
    assert all(panel.live["AAA"])
    assert panel.window(49).closes("AAA") == tuple(100.0 + i for i in range(50))


# --- the strategies, unmodified ----------------------------------------------


def test_momentum_ignores_a_name_it_cannot_rank_yet() -> None:
    """`CrossSectionalMomentumStrategy` is unchanged by this card. It needs
    lookback+1 bars, and a young name simply does not have them."""

    panel = PricePanel.from_bars(
        {
            "OLD": _bars([100.0 * (1.01**i) for i in range(300)]),
            "NEW": _bars([50.0 * (1.05**i) for i in range(20)], offset=280),
        }
    )
    strategy = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=2)

    # NEW has the strongest recent run by far, and is still not selected: it has
    # 20 bars against a 126-bar formation window.
    weights = strategy.target_weights(panel.window(299))
    assert weights["NEW"] == 0.0
    assert weights["OLD"] > 0.0


def test_a_name_becomes_rankable_once_it_has_the_history() -> None:
    """The other half: nothing has to be un-excluded by hand."""

    rising = [10.0 * (1.02**i) for i in range(400)]
    panel = PricePanel.from_bars(
        {
            "OLD": _bars([100.0] * 400),
            "NEW": _bars(rising[:200], offset=200),
        }
    )
    strategy = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=1)

    assert strategy.target_weights(panel.window(250))["NEW"] == 0.0  # 51 bars
    assert strategy.target_weights(panel.window(399))["NEW"] > 0.0  # 200 bars


def test_the_trend_rule_is_equally_unmodified() -> None:
    panel = PricePanel.from_bars(
        {
            "OLD": _bars([100.0 * (1.01**i) for i in range(200)]),
            "NEW": _bars([50.0 * (1.01**i) for i in range(10)], offset=190),
        }
    )
    weights = CrossSectionalTrendStrategy(fast=5, slow=30).target_weights(panel.window(199))

    assert weights["NEW"] == 0.0  # 10 bars < slow=30
    assert weights["OLD"] > 0.0


# --- the engine --------------------------------------------------------------


class HoldEverything:
    """Asks for an equal weight in every panel ticker, listed or not."""

    @property
    def name(self) -> str:
        return "hold-everything"

    @property
    def warmup(self) -> int:
        return 1

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        n = len(window.tickers)
        return dict.fromkeys(window.tickers, 1.0 / n)


def test_the_engine_refuses_to_hold_a_name_that_has_no_price() -> None:
    """A strategy asking for an unlisted name is overruled, not obeyed.

    Without this the engine would read `nan` closes and every metric downstream
    would become `nan` — or worse, a 0.0 close would book a -100% return on the
    bar before a name listed.
    """

    panel = PricePanel.from_bars(
        {
            "OLD": _bars([100.0 * (1.001**i) for i in range(120)]),
            "NEW": _bars([50.0 * (1.001**i) for i in range(40)], offset=80),
        }
    )
    segment = run_backtest(
        panel,
        HoldEverything(),
        _ZERO_COST,
        first_period=1,
        last_period=panel.n_bars - 2,
        execution_lag=0,
    )

    assert all(math.isfinite(r) for r in segment.daily_returns)
    assert all(math.isfinite(e) for e in segment.equity)


def test_a_name_entering_the_universe_registers_as_a_buy() -> None:
    """Not as a position that was somehow already open.

    The previous period's weights are passed through the same liveness filter,
    so the weight delta on the listing bar is a real trade.
    """

    panel = PricePanel.from_bars(
        {
            "OLD": _bars([100.0] * 60),
            "NEW": _bars([50.0] * 20, offset=40),
        }
    )
    segment = run_backtest(
        panel,
        HoldEverything(),
        _ZERO_COST,
        first_period=1,
        last_period=panel.n_bars - 2,
        execution_lag=0,
    )

    # `trades_per_period[i]` is period `first_period + i`. NEW has bars at
    # indices 40..59, so it is first holdable over period 40 -> index 39.
    assert segment.trades_per_period[0] == 1  # OLD's own opening buy
    assert sum(segment.trades_per_period[1:39]) == 0  # nothing while NEW is unlisted
    assert segment.trades_per_period[39] == 1  # NEW enters, exactly once
    assert sum(segment.trades_per_period) == 2


# --- the benchmark, which is the promote gate --------------------------------


def test_the_benchmark_weights_only_listed_names() -> None:
    """`1/N` over the panel roster would hold a fraction of nothing before a
    name lists, leaving the benchmark under-invested and inflating every
    information ratio measured against it."""

    panel = PricePanel.from_bars(
        {"A": _bars([10.0] * 100), "B": _bars([20.0] * 100), "C": _bars([30.0] * 40, offset=60)}
    )
    benchmark = EqualWeightBuyAndHold()

    before = benchmark.target_weights(panel.window(59))
    assert before == {"A": 0.5, "B": 0.5, "C": 0.0}
    assert sum(before.values()) == 1.0

    after = benchmark.target_weights(panel.window(60))
    assert after["C"] > 0.0
    assert sum(after.values()) == 1.0


def test_the_benchmark_stays_fully_invested_throughout() -> None:
    """The property that matters: whatever the universe is doing, the thing a
    strategy has to beat is always 100% invested."""

    panel = PricePanel.from_bars(
        {
            "A": _bars([10.0] * 200),
            "B": _bars([20.0] * 150, offset=50),
            "C": _bars([30.0] * 100, offset=100),
        }
    )
    benchmark = EqualWeightBuyAndHold()

    for i in range(panel.n_bars):
        total = sum(benchmark.target_weights(panel.window(i)).values())
        assert abs(total - 1.0) < 1e-12, f"bar {i} was {total:.6f} invested"


def test_the_benchmark_names_every_ticker_including_unlisted_ones() -> None:
    """The engine recovers trades by diffing weights per ticker, so an omitted
    name reads as 'unchanged' rather than 'not held'."""

    panel = PricePanel.from_bars({"A": _bars([10.0] * 50), "B": _bars([20.0] * 10, offset=40)})
    weights = EqualWeightBuyAndHold().target_weights(panel.window(0))

    assert set(weights) == {"A", "B"}
    assert weights["B"] == 0.0


# --- no-peek, still structural ----------------------------------------------


def test_the_window_still_cannot_see_the_future() -> None:
    """Raggedness must not have opened a look-ahead: `closes` returns real bars
    up to the current index and nothing beyond it."""

    panel = PricePanel.from_bars(
        {"OLD": _bars([float(i) for i in range(100)]), "NEW": _bars([1.0] * 30, offset=70)}
    )

    for i in (0, 50, 69, 70, 99):
        window = panel.window(i)
        assert len(window.dates()) == i + 1
        assert window.closes("OLD") == tuple(float(j) for j in range(i + 1))
        expected_new = max(0, i - 70 + 1)
        assert len(window.closes("NEW")) == expected_new
