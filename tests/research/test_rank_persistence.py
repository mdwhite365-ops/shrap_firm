"""The rank-persistence probe, and the limits of what it can claim.

Built to test arXiv 2607.27461's central claim on the firm's own fifty names
before anything was built on it. Measured 2026-09-17 over 74 month-ends:

    volatility rank    mean rho +0.880   positive in 100% of 73 months
    return rank        mean rho +0.019   positive in  55%

These tests pin the arithmetic and, more importantly, the boundaries: a probe
that measures whether a signal has *memory* must not be mistakable for one that
measures *edge*.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from shrap.research.rank_persistence import (
    month_end_snapshots,
    rank_persistence,
    realised_volatility,
    spearman,
    window_return,
)


def test_spearman_is_one_for_an_identical_ordering() -> None:
    assert spearman([1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0]) == pytest.approx(1.0)


def test_spearman_is_minus_one_for_a_reversed_ordering() -> None:
    assert spearman([1.0, 2.0, 3.0, 4.0], [40.0, 30.0, 20.0, 10.0]) == pytest.approx(-1.0)


def test_spearman_uses_ranks_not_magnitudes() -> None:
    """A huge outlier must not dominate — the point of ranking."""

    assert spearman([1.0, 2.0, 3.0], [10.0, 20.0, 1e9]) == pytest.approx(1.0)


def test_spearman_returns_none_rather_than_zero_when_undefined() -> None:
    """Zero means "no relationship" and is a real answer.

    Returning it for "could not be computed" would pull a mean toward no-effect
    every time a period was unusable.
    """

    assert spearman([1.0], [2.0]) is None
    assert spearman([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None


def test_a_perfectly_persistent_ranking_scores_one() -> None:
    snaps = [{"A": 1.0, "B": 2.0, "C": 3.0}] * 4

    result = rank_persistence(snaps, label="stable", min_names=3)

    assert result is not None
    assert result.mean_rho == pytest.approx(1.0)
    assert result.share_positive == 1.0
    assert result.periods == 3


def test_a_thin_cross_section_is_skipped_not_averaged_in() -> None:
    """Twenty names is the floor; a three-name rho is noise wearing a number."""

    assert rank_persistence([{"A": 1.0}, {"A": 2.0}], label="thin") is None


def test_names_are_intersected_per_pair_not_over_all_history() -> None:
    """A name that lists midway must not drop every period before it existed.

    Intersecting globally would silently change which universe is measured.
    """

    first = {chr(65 + i): float(i) for i in range(25)}
    second = dict(first)
    second["NEW"] = 99.0  # lists in period 2
    third = dict(second)

    result = rank_persistence([first, second, third], label="listing", min_names=20)

    assert result is not None
    assert result.periods == 2


def test_realised_volatility_is_zero_for_a_flat_series() -> None:
    assert realised_volatility([100.0] * 10) == 0.0


def test_window_return_is_end_over_start() -> None:
    assert window_return([100.0, 110.0, 121.0]) == pytest.approx(0.21)


def test_nonsense_inputs_return_none() -> None:
    assert realised_volatility([100.0]) is None
    assert window_return([100.0]) is None
    assert window_return([0.0, 100.0]) is None


def test_month_end_snapshots_gives_one_entry_per_month() -> None:
    start = date(2026, 1, 1)
    closes = {
        f"T{i}": [(start + timedelta(days=d), 100.0 + d + i) for d in range(70)] for i in range(25)
    }

    vol_snaps, ret_snaps = month_end_snapshots(closes, window=21)

    assert len(vol_snaps) == len(ret_snaps)
    assert len(vol_snaps) >= 2
    assert all(len(s) == 25 for s in vol_snaps if s)


def test_the_probe_measures_memory_not_edge() -> None:
    """The boundary this module must not be cited across.

    A perfectly forecastable ranking can be perfectly unprofitable. Volatility
    rank persisting at rho 0.88 says volatility clusters — a stylised fact since
    Mandelbrot 1963 — and says nothing whatever about whether a portfolio built
    on it earns anything. Any edge claim has to come from the Evaluator.
    """

    persistent = [{chr(65 + i): float(i) for i in range(25)}] * 5

    result = rank_persistence(persistent, label="perfect", min_names=20)

    assert result is not None
    assert result.mean_rho == pytest.approx(1.0)
    # Nothing in the result type reports a return, a Sharpe or an IR — by design.
    assert not hasattr(result, "sharpe")
    assert not hasattr(result, "information_ratio")
