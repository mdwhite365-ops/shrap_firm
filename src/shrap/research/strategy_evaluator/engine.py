"""Deterministic expanding-window walk-forward engine — the load-bearing core.

Given a :class:`~shrap.research.strategy_evaluator.strategy.PricePanel` and a
:class:`~shrap.research.strategy_evaluator.strategy.StrategySignal`, this runs a
walk-forward backtest with a realistic cost model and a realistic-friction
stress test, and returns per-fold and aggregate metrics stamped with
``PROTOCOL_VERSION``. It is a pure function of (panel, strategy, config): no
I/O, no randomness, fully replayable.

Design decisions (see ``docs/research/eval-protocol.md`` for the authoritative
statement):

- **No peeking, structurally.** The target for the holding period ``[close[p],
  close[p+1]]`` is computed at decision bar ``s = p - execution_lag`` from a
  window that exposes only bars ``0..s``. A strategy can never see the bar whose
  return it is about to earn.
- **Fixed parameters this card.** In-sample grid fitting (spec step 4) is
  deferred, so an expanding-window walk-forward over a fixed-parameter strategy
  reduces to one continuous out-of-sample backtest partitioned into ``n_folds``
  contiguous reporting folds; fold ``i``'s "train" is the expanding history
  before its first period. When per-fold refitting lands (later card), each
  fold refits on its train block — the fold geometry here is already that shape.
- **Metrics.** Returns are close-to-close; Sharpe is annualized with
  ``periods_per_year`` (252) and sample std (ddof=1); max drawdown is the
  worst peak-to-trough decline of the equity curve (a non-negative fraction);
  a trade is any rebalance leg that changes a ticker's target weight.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from shrap.research.strategy_evaluator.costs import (
    TRADING_DAYS_PER_YEAR,
    CostModel,
)
from shrap.research.strategy_evaluator.strategy import PricePanel, StrategySignal

# Bumped whenever the test protocol changes in a way that makes prior
# evaluations non-comparable. Stamped onto every persisted evaluation.
PROTOCOL_VERSION = "0.1"

DEFAULT_FOLDS = 6
DEFAULT_WINDOW_YEARS = 5
DEFAULT_MIN_TRADES = 150
# Sharpe promote floor — Mike-owned calibration, shipped as a documented v0.1
# default (see the protocol doc's "pending Mike" note). Conservative: a net
# annualized OOS Sharpe of 1.0 is a modest but economically meaningful bar.
DEFAULT_SHARPE_FLOOR = 1.0
DEFAULT_STRESS_COST_MULTIPLIER = 1.5
DEFAULT_STRESS_EXECUTION_LAG = 1
MIN_FOLD_PERIODS = 5

_TRADE_EPS = 1.0e-9


class InsufficientDataError(Exception):
    """Not enough aligned bars to form the configured walk-forward folds."""


@dataclass(frozen=True, slots=True)
class EvalConfig:
    """Everything the deterministic pipeline needs to reproduce a verdict."""

    n_folds: int = DEFAULT_FOLDS
    window_years: int = DEFAULT_WINDOW_YEARS
    min_trades: int = DEFAULT_MIN_TRADES
    sharpe_floor: float = DEFAULT_SHARPE_FLOOR
    cost_model: CostModel = field(default_factory=CostModel)
    stress_cost_multiplier: float = DEFAULT_STRESS_COST_MULTIPLIER
    stress_execution_lag: int = DEFAULT_STRESS_EXECUTION_LAG
    periods_per_year: int = TRADING_DAYS_PER_YEAR
    adjustment: str = "all"

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_folds": self.n_folds,
            "window_years": self.window_years,
            "min_trades": self.min_trades,
            "sharpe_floor": self.sharpe_floor,
            "stress_cost_multiplier": self.stress_cost_multiplier,
            "stress_execution_lag": self.stress_execution_lag,
            "periods_per_year": self.periods_per_year,
            "adjustment": self.adjustment,
            "cost_model": {
                "commission_bps": self.cost_model.commission_bps,
                "half_spread_bps": self.cost_model.half_spread_bps,
                "slippage_bps_per_adv": self.cost_model.slippage_bps_per_adv,
                "borrow_rate_annual": self.cost_model.borrow_rate_annual,
                "adv_window": self.cost_model.adv_window,
                "capital": self.cost_model.capital,
            },
        }


@dataclass(frozen=True, slots=True)
class BacktestSegment:
    """Raw per-period output of one continuous backtest run."""

    daily_returns: tuple[float, ...]
    equity: tuple[float, ...]
    trades_per_period: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class FoldMetrics:
    """Out-of-sample metrics for one walk-forward fold."""

    index: int
    start_date: date
    end_date: date
    n_periods: int
    total_return: float
    sharpe: float
    max_drawdown: float
    trade_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "n_periods": self.n_periods,
            "total_return": self.total_return,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "trade_count": self.trade_count,
        }


@dataclass(frozen=True, slots=True)
class AggregateMetrics:
    """Metrics over the full out-of-sample period (all folds concatenated)."""

    total_return: float
    sharpe: float
    max_drawdown: float
    trade_count: int
    n_periods: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_return": self.total_return,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "trade_count": self.trade_count,
            "n_periods": self.n_periods,
        }


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    """The full deterministic result of one evaluation run."""

    protocol_version: str
    n_folds: int
    first_date: date
    last_date: date
    folds: tuple[FoldMetrics, ...]
    aggregate: AggregateMetrics
    stress: AggregateMetrics

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "n_folds": self.n_folds,
            "first_date": self.first_date.isoformat(),
            "last_date": self.last_date.isoformat(),
            "folds": [f.as_dict() for f in self.folds],
            "aggregate": self.aggregate.as_dict(),
            "stress": self.stress.as_dict(),
        }


def sharpe(returns: Sequence[float], periods_per_year: int) -> float:
    """Annualized Sharpe of a per-period return series (sample std, ddof=1)."""

    if len(returns) < 2:
        return 0.0
    arr = np.asarray(returns, dtype=float)
    std = float(arr.std(ddof=1))
    if std == 0.0:
        return 0.0
    return float(arr.mean() / std * math.sqrt(periods_per_year))


def max_drawdown(equity: Sequence[float]) -> float:
    """Worst peak-to-trough decline of an equity curve, as a non-negative fraction."""

    if len(equity) == 0:
        return 0.0
    arr = np.asarray(equity, dtype=float)
    running_max = np.maximum.accumulate(arr)
    drawdowns = arr / running_max - 1.0
    return float(-drawdowns.min())


def total_return(equity: Sequence[float]) -> float:
    """Cumulative return of an equity curve that starts at 1.0."""

    if len(equity) == 0:
        return 0.0
    return float(equity[-1] - 1.0)


def _adv_dollar_series(panel: PricePanel, adv_window: int) -> dict[str, list[float]]:
    """Trailing mean dollar-volume per ticker at each bar (no peek)."""

    adv: dict[str, list[float]] = {}
    for ticker in panel.tickers:
        closes = panel.closes[ticker]
        volumes = panel.volumes[ticker]
        dollar = [closes[i] * volumes[i] for i in range(panel.n_bars)]
        series: list[float] = []
        for i in range(panel.n_bars):
            lo = max(0, i - adv_window + 1)
            window = dollar[lo : i + 1]
            series.append(sum(window) / len(window) if window else 0.0)
        adv[ticker] = series
    return adv


def run_backtest(
    panel: PricePanel,
    strategy: StrategySignal,
    cost_model: CostModel,
    *,
    first_period: int,
    last_period: int,
    execution_lag: int,
) -> BacktestSegment:
    """Run one continuous backtest over periods ``[first_period, last_period]``.

    Period ``p`` holds the strategy's target from ``close[p]`` to ``close[p+1]``;
    the target is decided at bar ``p - execution_lag`` from a no-peek window.
    Costs and borrow are charged per period. Returns the per-period return
    series, the equity curve (leading 1.0), and the per-period trade counts.
    """

    tickers = panel.tickers
    adv = _adv_dollar_series(panel, cost_model.adv_window)
    weight_cache: dict[int, dict[str, float]] = {}

    def weights_at(decision_bar: int) -> dict[str, float]:
        if decision_bar < 0:
            return {}
        cached = weight_cache.get(decision_bar)
        if cached is not None:
            return cached
        raw = strategy.target_weights(panel.window(decision_bar))
        resolved = {t: float(raw.get(t, 0.0)) for t in tickers}
        weight_cache[decision_bar] = resolved
        return resolved

    daily_returns: list[float] = []
    trades_per_period: list[int] = []
    equity: list[float] = [1.0]

    for p in range(first_period, last_period + 1):
        decision_bar = p - execution_lag
        w_curr = weights_at(decision_bar)
        # Effective weight held in the prior period; flat when entering the run.
        w_prev = weights_at(decision_bar - 1) if p > first_period else {}

        trade_cost = 0.0
        borrow_cost = 0.0
        gross = 0.0
        trades = 0
        for ticker in tickers:
            curr = w_curr.get(ticker, 0.0)
            prev = w_prev.get(ticker, 0.0)
            delta = curr - prev
            if abs(delta) > _TRADE_EPS:
                trades += 1
                trade_cost += cost_model.trade_cost_fraction(delta, adv[ticker][decision_bar])
            borrow_cost += cost_model.borrow_cost_fraction(curr)
            close_p = panel.closes[ticker][p]
            close_next = panel.closes[ticker][p + 1]
            if close_p != 0.0:
                gross += curr * (close_next / close_p - 1.0)

        net = gross - trade_cost - borrow_cost
        daily_returns.append(net)
        trades_per_period.append(trades)
        equity.append(equity[-1] * (1.0 + net))

    return BacktestSegment(
        daily_returns=tuple(daily_returns),
        equity=tuple(equity),
        trades_per_period=tuple(trades_per_period),
    )


def _fold_bounds(n_periods: int, n_folds: int) -> list[tuple[int, int]]:
    """Split ``n_periods`` offsets into ``n_folds`` contiguous, near-equal blocks.

    Returns inclusive ``(start_offset, end_offset)`` pairs into the OOS arrays.
    """

    base = n_periods // n_folds
    remainder = n_periods % n_folds
    bounds: list[tuple[int, int]] = []
    start = 0
    for i in range(n_folds):
        size = base + (1 if i < remainder else 0)
        end = start + size - 1
        bounds.append((start, end))
        start = end + 1
    return bounds


def _aggregate(segment: BacktestSegment, periods_per_year: int) -> AggregateMetrics:
    return AggregateMetrics(
        total_return=total_return(segment.equity),
        sharpe=sharpe(segment.daily_returns, periods_per_year),
        max_drawdown=max_drawdown(segment.equity),
        trade_count=sum(segment.trades_per_period),
        n_periods=len(segment.daily_returns),
    )


def walk_forward(
    panel: PricePanel,
    strategy: StrategySignal,
    config: EvalConfig,
) -> WalkForwardResult:
    """Expanding-window walk-forward with a realistic-friction stress re-run.

    Raises :class:`InsufficientDataError` if the aligned panel cannot support
    ``n_folds`` folds of at least ``MIN_FOLD_PERIODS`` periods each after warmup
    and lag headroom.
    """

    n = panel.n_bars
    warmup = max(strategy.warmup, 0)
    # Both the base run (lag 0) and the stress run (lag +1) must evaluate the
    # SAME out-of-sample periods for a fair Sharpe comparison, so the first
    # period leaves headroom for the larger lag.
    lag_headroom = max(config.stress_execution_lag, 0)
    first_period = warmup + lag_headroom
    last_period = n - 2  # period p needs close[p+1]
    n_periods = last_period - first_period + 1

    if config.n_folds < 1:
        raise InsufficientDataError("n_folds must be >= 1")
    if n_periods < config.n_folds * MIN_FOLD_PERIODS:
        raise InsufficientDataError(
            f"need >= {config.n_folds * MIN_FOLD_PERIODS} out-of-sample periods "
            f"for {config.n_folds} folds; have {max(n_periods, 0)} "
            f"(bars={n}, warmup={warmup})"
        )

    base = run_backtest(
        panel,
        strategy,
        config.cost_model,
        first_period=first_period,
        last_period=last_period,
        execution_lag=0,
    )
    stress = run_backtest(
        panel,
        strategy,
        config.cost_model.stressed(config.stress_cost_multiplier),
        first_period=first_period,
        last_period=last_period,
        execution_lag=config.stress_execution_lag,
    )

    folds: list[FoldMetrics] = []
    for i, (start_off, end_off) in enumerate(_fold_bounds(n_periods, config.n_folds)):
        returns_slice = base.daily_returns[start_off : end_off + 1]
        trades_slice = base.trades_per_period[start_off : end_off + 1]
        # Fold-local equity curve (resets to 1.0 at the fold's first period).
        eq: list[float] = [1.0]
        for r in returns_slice:
            eq.append(eq[-1] * (1.0 + r))
        folds.append(
            FoldMetrics(
                index=i,
                start_date=panel.dates[first_period + start_off],
                end_date=panel.dates[first_period + end_off],
                n_periods=len(returns_slice),
                total_return=total_return(eq),
                sharpe=sharpe(returns_slice, config.periods_per_year),
                max_drawdown=max_drawdown(eq),
                trade_count=sum(trades_slice),
            )
        )

    return WalkForwardResult(
        protocol_version=PROTOCOL_VERSION,
        n_folds=config.n_folds,
        first_date=panel.dates[first_period],
        last_date=panel.dates[last_period],
        folds=tuple(folds),
        aggregate=_aggregate(base, config.periods_per_year),
        stress=_aggregate(stress, config.periods_per_year),
    )


__all__ = [
    "DEFAULT_FOLDS",
    "DEFAULT_MIN_TRADES",
    "DEFAULT_SHARPE_FLOOR",
    "DEFAULT_STRESS_COST_MULTIPLIER",
    "DEFAULT_STRESS_EXECUTION_LAG",
    "DEFAULT_WINDOW_YEARS",
    "MIN_FOLD_PERIODS",
    "PROTOCOL_VERSION",
    "AggregateMetrics",
    "BacktestSegment",
    "EvalConfig",
    "FoldMetrics",
    "InsufficientDataError",
    "WalkForwardResult",
    "max_drawdown",
    "run_backtest",
    "sharpe",
    "total_return",
    "walk_forward",
]
