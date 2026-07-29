"""Did the edge show up across year-sets, or in a couple of them?

Mike, 2026-07-29: *"tests should be done on multiple year sets so as to not
overfit."* The walk-forward already does — it runs six separate periods. The
verdict then pools them into one number and discards the rest.

On the firm's first real evaluation that concealed a great deal:

    aggregate sharpe the gate used : 0.782
    six fold sharpes               : +1.286 -1.036 +0.451 +1.655 +1.516 +0.374
    mean 0.708, stdev 1.010        -> consistency 0.70

The variation between year-sets exceeded the average. That is an edge you cannot
distinguish from zero across periods, reported as one respectable-looking figure.

The measure here is the per-fold **information ratio**, not the per-fold return.
Absolute fold return says little: +9% in a year the basket did +30% is a loss.
"""

from __future__ import annotations

from datetime import date

from shrap.research.strategy_evaluator.engine import ConsistencyMetrics, FoldMetrics


def _fold(index: int, ir: float, *, sharpe: float = 0.0) -> FoldMetrics:
    return FoldMetrics(
        index=index,
        start_date=date(2021, 1, 1),
        end_date=date(2021, 12, 31),
        n_periods=230,
        total_return=0.0,
        sharpe=sharpe,
        max_drawdown=0.0,
        trade_count=100,
        information_ratio=ir,
        active_return=0.0,
    )


# --- the counting -------------------------------------------------------------


def test_it_counts_the_folds_that_actually_beat_the_benchmark() -> None:
    """Not the folds that made money. A fold can be up 9% and still be a loss
    against a basket that was up 30%."""

    folds = [_fold(0, 1.2), _fold(1, -0.8), _fold(2, 0.3), _fold(3, -0.1)]

    metrics = ConsistencyMetrics.from_folds(folds)

    assert metrics.n_folds == 4
    assert metrics.folds_with_active_edge == 2
    assert metrics.summary() == "folds=2/4"


def test_a_fold_exactly_at_the_benchmark_is_not_an_edge() -> None:
    """Matching the thing you are measured against is not beating it."""

    metrics = ConsistencyMetrics.from_folds([_fold(0, 0.0), _fold(1, 0.5)])

    assert metrics.folds_with_active_edge == 1


def test_the_worst_fold_is_reported_so_it_cannot_be_averaged_away() -> None:
    """The strategy doc's own kill criterion #3 says a single large negative
    fold is evidence rather than noise. The aggregate treats it as noise."""

    metrics = ConsistencyMetrics.from_folds([_fold(0, 1.9), _fold(1, -1.4), _fold(2, 1.7)])

    assert metrics.worst_fold_ir == -1.4


# --- the consistency ratio ----------------------------------------------------


def test_dispersion_larger_than_the_average_scores_below_one() -> None:
    """The signature of an edge that cannot be told apart from zero across
    periods — and the shape the first real evaluation actually had."""

    # The parent strategy's six folds, as measured on the Dell.
    metrics = ConsistencyMetrics.from_folds(
        [_fold(i, ir) for i, ir in enumerate([1.286, -1.036, 0.451, 1.655, 1.516, 0.374])]
    )

    assert round(metrics.fold_ir_mean, 3) == 0.708
    assert round(metrics.fold_ir_stdev, 3) == 1.010
    assert metrics.consistency < 1.0
    assert round(metrics.consistency, 2) == 0.70
    # 5 of 6 folds beat the benchmark, and the aggregate still cannot be
    # distinguished from zero across periods. Both facts are true at once, which
    # is exactly why one number was never enough.
    assert metrics.folds_with_active_edge == 5


def test_a_steady_edge_scores_well_above_one() -> None:
    """The contrast case. Same mean, a fraction of the spread."""

    metrics = ConsistencyMetrics.from_folds(
        [_fold(i, ir) for i, ir in enumerate([0.70, 0.72, 0.68, 0.75, 0.69, 0.71])]
    )

    assert round(metrics.fold_ir_mean, 2) == 0.71
    assert metrics.consistency > 10.0
    assert metrics.folds_with_active_edge == 6


def test_identical_folds_report_zero_rather_than_dividing_by_zero() -> None:
    """Zero dispersion is not infinite consistency. Six identical folds is a
    fixture, not a strategy, and reporting `inf` would put it at the top of any
    ranking on no evidence."""

    metrics = ConsistencyMetrics.from_folds([_fold(i, 0.5) for i in range(6)])

    assert metrics.fold_ir_stdev == 0.0
    assert metrics.consistency == 0.0


def test_a_single_fold_has_no_dispersion_to_report() -> None:
    metrics = ConsistencyMetrics.from_folds([_fold(0, 1.4)])

    assert metrics.n_folds == 1
    assert metrics.fold_ir_stdev == 0.0
    assert metrics.consistency == 0.0
    assert metrics.worst_fold_ir == 1.4


def test_no_folds_is_empty_rather_than_an_error() -> None:
    metrics = ConsistencyMetrics.from_folds([])

    assert metrics.n_folds == 0
    assert metrics.folds_with_active_edge == 0
    assert metrics.consistency == 0.0


# --- a good aggregate can hide a bad distribution -----------------------------


def test_two_strategies_with_the_same_average_are_told_apart() -> None:
    """The whole point of the card.

    Both average an IR of 0.5. One earns it every year; the other earns it twice
    and gives it back four times. The aggregate cannot distinguish them.
    """

    steady = ConsistencyMetrics.from_folds(
        [_fold(i, ir) for i, ir in enumerate([0.5, 0.5, 0.5, 0.5, 0.5, 0.5])]
    )
    lumpy = ConsistencyMetrics.from_folds(
        [_fold(i, ir) for i, ir in enumerate([2.6, 2.4, -0.4, -0.5, -0.6, -0.5])]
    )

    assert round(steady.fold_ir_mean, 3) == round(lumpy.fold_ir_mean, 3) == 0.5
    assert steady.folds_with_active_edge == 6
    assert lumpy.folds_with_active_edge == 2
    assert lumpy.consistency < steady.consistency or steady.consistency == 0.0
    assert lumpy.worst_fold_ir < 0.0
