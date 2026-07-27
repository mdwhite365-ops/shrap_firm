"""Engine correctness on synthetic series with known outcomes.

Covers the metric primitives (Sharpe/drawdown/return), the backtest loop
(returns, trade counting, borrow), the no-peek guarantee, the walk-forward
fold partition, and the cost + friction-stress effect.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping
from datetime import date, timedelta

import pytest

from shrap.research.strategy_evaluator.costs import CostModel
from shrap.research.strategy_evaluator.engine import (
    PROTOCOL_VERSION,
    EvalConfig,
    InsufficientDataError,
    _fold_bounds,
    max_drawdown,
    run_backtest,
    sharpe,
    total_return,
    walk_forward,
)
from shrap.research.strategy_evaluator.strategy import BarSample, PanelWindow, PricePanel

_ZERO_COST = CostModel(
    commission_bps=0.0,
    half_spread_bps=0.0,
    slippage_bps_per_adv=0.0,
    borrow_rate_annual=0.0,
)
_TICKER = "AAA"
_START = date(2020, 1, 1)


def _panel(closes: list[float], volume: float = 1.0e9) -> PricePanel:
    bars = [
        BarSample(
            session_date=_START + timedelta(days=i),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=volume,
        )
        for i, c in enumerate(closes)
    ]
    return PricePanel.from_bars({_TICKER: bars})


class AlwaysLong:
    @property
    def name(self) -> str:
        return "always-long"

    @property
    def warmup(self) -> int:
        return 0

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        return {_TICKER: 1.0}


class AlwaysShort:
    @property
    def name(self) -> str:
        return "always-short"

    @property
    def warmup(self) -> int:
        return 0

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        return {_TICKER: -1.0}


class SquareWave:
    """Long for ``period`` bars, then flat for ``period`` bars, by bar index."""

    def __init__(self, period: int) -> None:
        self._period = period

    @property
    def name(self) -> str:
        return "square-wave"

    @property
    def warmup(self) -> int:
        return 1

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        long = (window.current_index // self._period) % 2 == 0
        return {_TICKER: 1.0 if long else 0.0}


class IndexSpy:
    """Records the max window index it is ever shown (no-peek probe)."""

    def __init__(self) -> None:
        self.max_index_seen = -1
        self.max_len_seen = 0

    @property
    def name(self) -> str:
        return "index-spy"

    @property
    def warmup(self) -> int:
        return 3

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        self.max_index_seen = max(self.max_index_seen, window.current_index)
        self.max_len_seen = max(self.max_len_seen, len(window.closes(_TICKER)))
        return {_TICKER: 0.0}


# --- metric primitives -------------------------------------------------------


def test_sharpe_zero_variance_is_zero() -> None:
    assert sharpe([0.01, 0.01, 0.01], 252) == 0.0


def test_sharpe_annualization_and_ddof() -> None:
    # returns [3, 1]: mean 2, sample std sqrt(2); sharpe = 2/sqrt(2)*sqrt(252).
    expected = 2.0 / math.sqrt(2.0) * math.sqrt(252)
    assert sharpe([3.0, 1.0], 252) == pytest.approx(expected)


def test_sharpe_sign_follows_mean() -> None:
    assert sharpe([0.02, -0.01, 0.015, 0.005], 252) > 0
    assert sharpe([-0.02, 0.01, -0.015, -0.005], 252) < 0


def test_max_drawdown_known_curve() -> None:
    assert max_drawdown([1.0, 1.2, 0.9, 1.0]) == pytest.approx(0.25)


def test_total_return_known_curve() -> None:
    assert total_return([1.0, 1.1, 1.21]) == pytest.approx(0.21)


# --- backtest loop -----------------------------------------------------------


def test_run_backtest_returns_trades_and_equity() -> None:
    panel = _panel([100.0, 110.0, 121.0])
    seg = run_backtest(
        panel, AlwaysLong(), _ZERO_COST, first_period=0, last_period=1, execution_lag=0
    )
    assert seg.daily_returns == pytest.approx((0.1, 0.1))
    assert seg.equity == pytest.approx((1.0, 1.1, 1.21))
    # One entry from flat; no further rebalances (target never changes).
    assert sum(seg.trades_per_period) == 1


def test_run_backtest_charges_borrow_on_shorts() -> None:
    panel = _panel([100.0, 100.0, 100.0])  # flat price -> only borrow bites
    cost = CostModel(
        commission_bps=0.0,
        half_spread_bps=0.0,
        slippage_bps_per_adv=0.0,
        borrow_rate_annual=0.0252,  # 0.0001/day
    )
    seg = run_backtest(panel, AlwaysShort(), cost, first_period=0, last_period=1, execution_lag=0)
    daily_borrow = 0.0252 / 252
    for r in seg.daily_returns:
        assert r == pytest.approx(-daily_borrow)


# --- no peek + fold geometry -------------------------------------------------


def test_panel_window_exposes_only_up_to_index() -> None:
    panel = _panel([1.0, 2.0, 3.0, 4.0, 5.0])
    window = panel.window(2)
    assert window.current_index == 2
    assert window.current_date == panel.dates[2]
    assert window.closes(_TICKER) == (1.0, 2.0, 3.0)
    assert len(window.dates()) == 3


def test_walk_forward_never_peeks_past_current_bar() -> None:
    panel = _panel([100.0 + i for i in range(80)])
    spy = IndexSpy()
    config = EvalConfig()
    walk_forward(panel, spy, config)
    # Last holding period is n-2; its return uses close[n-1]. The strategy must
    # never be shown a bar >= n-1 (that would be look-ahead).
    assert spy.max_index_seen <= panel.n_bars - 2
    assert spy.max_index_seen < panel.n_bars - 1


def test_fold_bounds_partition_is_contiguous_and_complete() -> None:
    bounds = _fold_bounds(20, 6)
    assert len(bounds) == 6
    assert bounds[0][0] == 0
    assert bounds[-1][1] == 19
    for (_, end), (start, _) in itertools.pairwise(bounds):
        assert start == end + 1
    assert sum(e - s + 1 for s, e in bounds) == 20


def test_walk_forward_folds_cover_oos_without_overlap() -> None:
    panel = _panel([100.0 + i * 0.5 for i in range(120)])
    result = walk_forward(panel, AlwaysLong(), EvalConfig())
    assert result.protocol_version == PROTOCOL_VERSION
    assert len(result.folds) == 6
    total_fold_periods = sum(f.n_periods for f in result.folds)
    assert total_fold_periods == result.aggregate.n_periods
    for earlier, later in itertools.pairwise(result.folds):
        assert earlier.end_date < later.start_date


def test_insufficient_data_raises() -> None:
    panel = _panel([100.0 + i for i in range(20)])  # too few bars for 6 folds
    with pytest.raises(InsufficientDataError):
        walk_forward(panel, AlwaysLong(), EvalConfig())


# --- cost + friction stress --------------------------------------------------


def test_costs_reduce_return_and_friction_is_harsher() -> None:
    # Square-wave signal on a series that rises only while the signal is long.
    period = 8
    closes = [100.0]
    for i in range(1, 400):
        long_phase = ((i - 1) // period) % 2 == 0
        closes.append(closes[-1] * (1.01 if long_phase else 1.0))
    panel = _panel(closes)
    strategy = SquareWave(period)

    free = EvalConfig(cost_model=_ZERO_COST)
    costed = EvalConfig()  # default (non-zero) cost model

    free_result = walk_forward(panel, strategy, free)
    costed_result = walk_forward(panel, strategy, costed)

    # Enough trades to be a meaningful comparison.
    assert costed_result.aggregate.trade_count > 20
    # Costs strictly reduce realized return when the strategy trades.
    assert costed_result.aggregate.total_return < free_result.aggregate.total_return
    # The realistic-friction stress (+50% costs, +1 lag) is no better than base.
    assert costed_result.stress.total_return <= costed_result.aggregate.total_return + 1e-9
