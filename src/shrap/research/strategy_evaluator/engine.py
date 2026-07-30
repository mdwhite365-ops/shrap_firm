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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from shrap.research.strategy_evaluator.benchmark import (
    EqualWeightBuyAndHold,
    active_returns,
)
from shrap.research.strategy_evaluator.costs import (
    TRADING_DAYS_PER_YEAR,
    CostModel,
)
from shrap.research.strategy_evaluator.strategy import PricePanel, StrategySignal

# Bumped whenever the test protocol changes in a way that makes prior
# evaluations non-comparable. Stamped onto every persisted evaluation.
#
# 0.1 -> 0.2 (2026-07-29). Three changes landed together and every one of them
# alters what a number means, so a 0.1 row and a 0.2 row are not the same
# measurement even for an identical spec:
#
#   #138  The panel aligns on the UNION of session dates, not the intersection.
#         A universe now grows as names list instead of being truncated to its
#         youngest member. The first momentum evaluation ran on 506 bars; the
#         same spec now runs on 1,510 of the same data.
#   #138  The benchmark weights 1/N over names trading THAT DAY rather than the
#         full roster, so equal-weight buy-and-hold rebalances as names list.
#         That is the promote gate: every information ratio on record was
#         measured against a different benchmark.
#   #139  `window_years` became a cap rather than a 5-year default, so a run
#         reads every bar the store holds.
#
# This was missed when #138 shipped and caught on the first live dry-run, where
# a 1,510-bar result printed `protocol=0.1` beside a stored 506-bar one. Nothing
# had gone wrong yet — the run was a dry run — but committing it would have made
# the two indistinguishable in `research.evaluations`, which is the single thing
# this constant exists to prevent.
#
# Bumping also resets the trigger's re-evaluation floor: `latest_evaluation_at`
# keys on (strategy_id, spec_hash, protocol_version), so every strategy is
# re-asked at once rather than waiting out a cooldown earned under the old
# protocol. That is intended.
PROTOCOL_VERSION = "0.2"

DEFAULT_FOLDS = 6
# A CAP, not a default (Mike's ruling 2026-07-29). `window_years=None` — the
# default — requests every bar the store holds and lets the panel be as long as
# the data allows.
#
# It used to be a default of 5, which silently discarded a deeper backfill: the
# momentum runbook instructs `--since 2018-01-01` and justifies it as buying
# folds the 127-bar warmup would otherwise eat, and `_build_panel` then asked
# for five years and never read the rest. The doc promised something the code
# did not do.
#
# Kept as a named constant because it is still the value to pass when a caller
# deliberately wants a fixed recent window — e.g. re-testing a strategy on the
# last five years only, to compare against a run that used them.
DEFAULT_WINDOW_YEARS = 5
DEFAULT_MIN_TRADES = 150
# Sharpe promote floor — Mike-owned calibration, shipped as a documented v0.1
# default (see the protocol doc's "pending Mike" note). Conservative: a net
# annualized OOS Sharpe of 1.0 is a modest but economically meaningful bar.
DEFAULT_SHARPE_FLOOR = 1.0

# DECISION-CARRYING (Mike's calibration). The information ratio is active return
# over tracking error against equal-weight buy-and-hold. 0.5 is a genuinely good
# active manager sustained out of sample; 1.0 is exceptional and rare. Setting it
# equal to the Sharpe floor would mean the firm essentially never promotes, which
# is a defensible position but should be chosen rather than inherited.
DEFAULT_INFORMATION_RATIO_FLOOR = 0.5
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
    window_years: int | None = None
    """Lookback cap in years. ``None`` requests all available history."""
    min_trades: int = DEFAULT_MIN_TRADES
    sharpe_floor: float = DEFAULT_SHARPE_FLOOR
    information_ratio_floor: float = DEFAULT_INFORMATION_RATIO_FLOOR
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
            "information_ratio_floor": self.information_ratio_floor,
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
    information_ratio: float = 0.0
    """The promote gate applied to THIS year-set alone.

    Absolute fold return says little: +9% in a year the basket did +30% is a
    loss. The active series is already computed per period against the identical
    benchmark, so slicing it by fold costs nothing and answers the question the
    aggregate cannot — did the edge show up in each period, or in a couple?
    """

    active_return: float = 0.0

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
            "information_ratio": self.information_ratio,
            "active_return": self.active_return,
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
class ActiveMetrics:
    """The strategy measured against equal-weight buy-and-hold of its own panel.

    ``information_ratio`` is the annualised Sharpe of the ACTIVE return series
    (strategy minus benchmark, per period), i.e. active return over tracking
    error. It is the only number here that answers "did the strategy add
    anything," because absolute Sharpe is dominated by market drift — see
    ``docs/research/eval-protocol.md`` 6b.
    """

    information_ratio: float
    active_total_return: float
    benchmark_sharpe: float
    benchmark_total_return: float
    n_periods: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "information_ratio": self.information_ratio,
            "active_total_return": self.active_total_return,
            "benchmark_sharpe": self.benchmark_sharpe,
            "benchmark_total_return": self.benchmark_total_return,
            "n_periods": self.n_periods,
        }


@dataclass(frozen=True, slots=True)
class ConsistencyMetrics:
    """Does the edge show up across year-sets, or in a couple of them?

    The walk-forward already runs the strategy over N separate periods and the
    verdict then pools them into one number and discards the rest. On the first
    real evaluation that hid a great deal: an aggregate Sharpe of 0.782 was six
    folds ranging -1.036 to +1.655, a spread of 2.69 against a mean of 0.708.
    The fold-to-fold variation exceeded the average, which is the signature of
    an edge you cannot distinguish from zero across periods — and no gate saw
    it, because no gate was looking at the folds.

    ``consistency`` is the mean fold information ratio over its standard
    deviation. Below 1.0 means the variation between year-sets is larger than
    the average edge itself. It is deliberately NOT annualised or turned into a
    p-value: with six folds any such number would carry more precision than the
    sample supports, and the point is to make the dispersion visible rather than
    to manufacture a significance test out of it.

    Reported, not gated. What a promote decision should DO about three folds out
    of six is a calibration, and calibrations are Mike's.
    """

    n_folds: int
    folds_with_active_edge: int
    """Folds whose information ratio beat the benchmark, i.e. was above zero."""

    worst_fold_ir: float
    fold_ir_mean: float
    fold_ir_stdev: float
    fold_information_ratios: tuple[float, ...] = ()
    """Every fold's information ratio, in order, oldest first.

    Added 2026-07-30 after discovering the firm computed this sequence on every
    evaluation and kept only its mean and standard deviation. That discarded the
    answer to the question the promote floor most depends on — **does an early
    fold's information ratio predict a late one's?** If it does not, the metric
    has no persistence, the ranking is noise, and no floor is the right floor.

    The loss was irreversible for the twelve strategies already killed: kills are
    terminal and ``evaluate`` refuses any non-hypothesis strategy, so those runs
    cannot be reproduced. Everything evaluated from here carries the sequence.

    Ordered, because order is the whole point — a set of six numbers answers
    "how dispersed" and only a sequence answers "does earlier predict later".
    """

    @property
    def consistency(self) -> float:
        """Mean fold IR over its dispersion; 0.0 when there is nothing to divide."""

        if self.fold_ir_stdev == 0.0:
            return 0.0
        return self.fold_ir_mean / self.fold_ir_stdev

    def summary(self) -> str:
        """One field for the verdict line: ``folds=3/6``."""

        return f"folds={self.folds_with_active_edge}/{self.n_folds}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_folds": self.n_folds,
            "folds_with_active_edge": self.folds_with_active_edge,
            "worst_fold_ir": self.worst_fold_ir,
            "fold_ir_mean": self.fold_ir_mean,
            "fold_ir_stdev": self.fold_ir_stdev,
            "consistency": self.consistency,
            "fold_information_ratios": list(self.fold_information_ratios),
        }

    @classmethod
    def from_folds(cls, folds: Sequence[FoldMetrics]) -> ConsistencyMetrics:
        if not folds:
            return cls(0, 0, 0.0, 0.0, 0.0)
        irs = [f.information_ratio for f in folds]
        mean = sum(irs) / len(irs)
        # Sample stdev (ddof=1) to match the Sharpe convention used everywhere
        # else; a single fold has no dispersion to report.
        if len(irs) > 1:
            var = sum((x - mean) ** 2 for x in irs) / (len(irs) - 1)
            stdev = math.sqrt(var)
        else:
            stdev = 0.0
        return cls(
            n_folds=len(folds),
            folds_with_active_edge=sum(1 for x in irs if x > 0.0),
            worst_fold_ir=min(irs),
            fold_ir_mean=mean,
            fold_ir_stdev=stdev,
            fold_information_ratios=tuple(irs),
        )


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
    active: ActiveMetrics
    consistency: ConsistencyMetrics

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "n_folds": self.n_folds,
            "first_date": self.first_date.isoformat(),
            "last_date": self.last_date.isoformat(),
            "folds": [f.as_dict() for f in self.folds],
            "aggregate": self.aggregate.as_dict(),
            "stress": self.stress.as_dict(),
            "active": self.active.as_dict(),
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
        # Absent bars contribute nothing and are not counted in the mean. Taking
        # them as zero would halve a newly-listed name's ADV for its first
        # `adv_window` bars and inflate the slippage charged against it — a cost
        # penalty for being young, invented by the alignment grid.
        dollar = [
            closes[i] * volumes[i] if panel.is_live(ticker, i) else None
            for i in range(panel.n_bars)
        ]
        series: list[float] = []
        for i in range(panel.n_bars):
            lo = max(0, i - adv_window + 1)
            window = [d for d in dollar[lo : i + 1] if d is not None]
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

    def held_over(weights: Mapping[str, float], period: int) -> dict[str, float]:
        """The weights actually holdable across ``[close[p], close[p+1]]``.

        A position needs a price at both ends: one to buy at and one to mark
        against. A name that has not listed by ``period``, or that stops trading
        before ``period + 1``, is forced flat no matter what the strategy asked
        for — it is not a decision the strategy gets to make.

        Applied to the previous period's weights too, so a name entering the
        universe registers as a buy rather than as a position that was somehow
        already open.
        """

        return {
            t: (w if panel.is_live(t, period) and panel.is_live(t, period + 1) else 0.0)
            for t, w in weights.items()
        }

    daily_returns: list[float] = []
    trades_per_period: list[int] = []
    equity: list[float] = [1.0]

    for p in range(first_period, last_period + 1):
        decision_bar = p - execution_lag
        w_curr = held_over(weights_at(decision_bar), p)
        # Effective weight held in the prior period; flat when entering the run.
        w_prev = held_over(weights_at(decision_bar - 1), p - 1) if p > first_period else {}

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
            if curr == 0.0:
                # Nothing held: no return to earn, and `closes` may be nan here.
                continue
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

    # The benchmark runs over the IDENTICAL periods with the same cost model and
    # no execution lag, so the difference between the two return series is
    # attributable to the strategy's decisions and nothing else.
    bench = run_backtest(
        panel,
        EqualWeightBuyAndHold(),
        config.cost_model,
        first_period=first_period,
        last_period=last_period,
        execution_lag=0,
    )
    active_series = active_returns(base.daily_returns, bench.daily_returns)
    active_equity: list[float] = [1.0]
    for r in active_series:
        active_equity.append(active_equity[-1] * (1.0 + r))

    folds: list[FoldMetrics] = []
    for i, (start_off, end_off) in enumerate(_fold_bounds(n_periods, config.n_folds)):
        returns_slice = base.daily_returns[start_off : end_off + 1]
        trades_slice = base.trades_per_period[start_off : end_off + 1]
        # Sliced from the SAME offsets as the strategy returns, so a fold's
        # active number cannot describe a different span than its own.
        active_slice = active_series[start_off : end_off + 1]
        active_eq: list[float] = [1.0]
        for r in active_slice:
            active_eq.append(active_eq[-1] * (1.0 + r))
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
                information_ratio=sharpe(active_slice, config.periods_per_year),
                active_return=total_return(active_eq),
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
        active=ActiveMetrics(
            information_ratio=sharpe(active_series, config.periods_per_year),
            active_total_return=total_return(active_equity),
            benchmark_sharpe=sharpe(bench.daily_returns, config.periods_per_year),
            benchmark_total_return=total_return(bench.equity),
            n_periods=len(active_series),
        ),
        consistency=ConsistencyMetrics.from_folds(folds),
    )


__all__ = [
    "DEFAULT_FOLDS",
    "DEFAULT_INFORMATION_RATIO_FLOOR",
    "DEFAULT_MIN_TRADES",
    "DEFAULT_SHARPE_FLOOR",
    "DEFAULT_STRESS_COST_MULTIPLIER",
    "DEFAULT_STRESS_EXECUTION_LAG",
    "DEFAULT_WINDOW_YEARS",
    "MIN_FOLD_PERIODS",
    "PROTOCOL_VERSION",
    "ActiveMetrics",
    "AggregateMetrics",
    "BacktestSegment",
    "ConsistencyMetrics",
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
