"""An information ratio without an error bar is not a measurement.

These tests pin the arithmetic behind the finding of 2026-09-17: the firm's
best-ever strategy (IR 0.448) and the promote floor (0.50) are 0.11 standard
errors apart on a six-year panel, and no achievable amount of history separates
them. The gate has never been failed on evidence — it has been failed on noise.
"""

from __future__ import annotations

import math

import pytest

from shrap.research.ir_precision import (
    SIGNIFICANCE_SIGMAS,
    PrecisionResult,
    ir_standard_error,
    rolling_ir,
    sigmas_from,
    years_to_resolve,
)

TRADING_YEAR = 252


def _series_with_ir(target_ir: float, n: int, *, sd: float = 0.01) -> list[float]:
    """A deterministic series whose annualised IR is exactly ``target_ir``.

    Alternating +/- around a mean gives an exact population stdev, so the ratio
    is constructed rather than sampled. A random series would land near the
    target and make an assertion about the target a test of the sampler.
    """

    mean = target_ir * sd / math.sqrt(TRADING_YEAR)
    return [mean + (sd if i % 2 == 0 else -sd) for i in range(n)]


# --- the standard error -------------------------------------------------------


def test_standard_error_shrinks_with_the_square_root_of_time() -> None:
    """Four times the history halves the error — the whole difficulty in one line."""

    assert ir_standard_error(0.5, 5.0) / ir_standard_error(0.5, 20.0) == pytest.approx(2.0)


def test_the_firms_actual_panel_gives_a_standard_error_near_one_half() -> None:
    """5.1 years of daily data. This is the number that makes the gate unusable."""

    assert ir_standard_error(0.448, 5.1) == pytest.approx(0.463, abs=0.005)


def test_a_larger_ratio_carries_more_uncertainty() -> None:
    """Tracking error is itself estimated, so the denominator adds error too."""

    assert ir_standard_error(2.0, 5.0) > ir_standard_error(0.0, 5.0)


def test_zero_or_negative_years_is_an_error_not_an_infinity() -> None:
    with pytest.raises(ValueError, match="years must be positive"):
        ir_standard_error(0.5, 0.0)


# --- the finding --------------------------------------------------------------


def test_the_firms_best_strategy_is_indistinguishable_from_the_floor() -> None:
    """IR 0.448 vs a 0.50 floor, on the panel the firm actually has.

    Eleven hundredths of a standard error. The strategy was not rejected for
    being worse than the floor; it was rejected by a coin flip.
    """

    sigmas = sigmas_from(0.448, floor=0.50, years=5.1)

    assert sigmas == pytest.approx(0.11, abs=0.02)
    assert sigmas < SIGNIFICANCE_SIGMAS


def test_being_above_the_floor_is_just_as_unresolved() -> None:
    """The asymmetry the firm would otherwise fall for.

    A strategy scoring 0.55 is no more distinguishable from 0.50 than one
    scoring 0.45 — so a *pass* at this sample size is exactly as uninformative
    as a fail, and promoting on it would be acting on nothing.
    """

    assert sigmas_from(0.55, floor=0.50, years=5.1) < SIGNIFICANCE_SIGMAS


def test_separating_the_firms_gap_needs_geological_history() -> None:
    """~1,800 years to tell 0.45 from 0.50 at two sigma."""

    assert years_to_resolve(0.05) == pytest.approx(1800, rel=0.05)


def test_no_realistic_backfill_rescues_the_gate() -> None:
    """Twenty years of daily bars still cannot resolve a gap of 0.05.

    This is why the answer is not "backfill more history". The remaining
    honest move is to size on accumulated evidence instead of gating on a
    verdict — which is what the Kelly posterior already does.
    """

    assert sigmas_from(0.45, floor=0.50, years=20.0) < SIGNIFICANCE_SIGMAS


def test_a_genuinely_large_gap_is_resolvable() -> None:
    """The measure is not vacuous: a real difference does show up."""

    assert sigmas_from(-1.0, floor=0.50, years=5.1) > SIGNIFICANCE_SIGMAS


# --- rolling re-measurement ---------------------------------------------------


def test_rolling_windows_re_measure_the_same_decisions() -> None:
    """A constant-IR series scores the same in every window.

    Any dispersion the real panel shows is therefore the measurement moving,
    not the strategy changing.
    """

    series = _series_with_ir(0.5, 1000)
    values = rolling_ir(series, window=504, step=21)

    assert len(values) > 1
    assert all(v == pytest.approx(0.5, abs=1e-9) for v in values)


def test_a_series_shorter_than_one_window_yields_nothing() -> None:
    """Rather than a degenerate estimate from a partial window."""

    assert rolling_ir(_series_with_ir(0.5, 100), window=756) == []


def test_a_flat_series_is_skipped_not_divided_by_zero() -> None:
    assert rolling_ir([0.001] * 900, window=504, step=21) == []


def test_step_and_window_are_validated() -> None:
    with pytest.raises(ValueError, match="window must span"):
        rolling_ir([0.1] * 10, window=1)
    with pytest.raises(ValueError, match="step must be at least"):
        rolling_ir([0.1] * 10, window=5, step=0)


# --- the reported result ------------------------------------------------------


def test_result_reports_the_ratio_it_was_given_not_a_recomputed_one() -> None:
    """The error bar must describe the number the gate actually used."""

    result = PrecisionResult.from_active_returns(
        _series_with_ir(0.5, 1512), information_ratio=0.448, floor=0.50
    )

    assert result.information_ratio == 0.448
    assert result.years == pytest.approx(6.0, abs=0.01)


def test_result_on_the_firms_panel_reads_as_unresolved() -> None:
    result = PrecisionResult.from_active_returns(
        _series_with_ir(0.448, 1285), information_ratio=0.448, floor=0.50
    )

    assert not result.is_resolvable
    assert "INDISTINGUISHABLE" in result.summary()
    assert result.rolling_min is not None


def test_summary_survives_a_panel_too_short_to_roll() -> None:
    """Short panels still get an error bar; they just get no rolling range."""

    result = PrecisionResult.from_active_returns(
        _series_with_ir(0.4, 300), information_ratio=0.4, floor=0.50
    )

    assert result.rolling_min is None
    assert result.rolling_share_above_floor is None
    assert "rolling" not in result.summary()
