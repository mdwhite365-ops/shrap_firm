"""Tests for deterministic regime feature computation."""

from __future__ import annotations

import math

from shrap.intelligence.regime.features import (
    breadth_pct_above_sma,
    compute_features,
    log_returns,
    pct_above_sma,
    realized_vol,
    return_dispersion,
    sma,
    trend_50_over_200,
    window_return,
)


def test_log_returns_basic() -> None:
    returns = log_returns([100.0, 110.0, 99.0])
    assert len(returns) == 2
    assert math.isclose(returns[0], math.log(1.1))
    assert math.isclose(returns[1], math.log(99.0 / 110.0))


def test_realized_vol_of_constant_series_is_zero() -> None:
    closes = [100.0] * 30
    assert realized_vol(closes, 20) == 0.0


def test_realized_vol_insufficient_data_is_none() -> None:
    assert realized_vol([100.0, 101.0], 20) is None


def test_realized_vol_alternating_series_matches_hand_computation() -> None:
    # +1%/-1% alternating: per-day log returns alternate; stdev is computable by hand.
    closes = [100.0]
    for i in range(20):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 0.99))
    vol = realized_vol(closes, 20)
    assert vol is not None
    returns = log_returns(closes)[-20:]
    mean = sum(returns) / 20
    expected = math.sqrt(sum((r - mean) ** 2 for r in returns) / 19) * math.sqrt(252)
    assert math.isclose(vol, expected)


def test_sma_and_pct_above() -> None:
    closes = [1.0, 2.0, 3.0, 4.0]
    assert sma(closes, 4) == 2.5
    distance = pct_above_sma(closes, 4)
    assert distance is not None
    assert math.isclose(distance, 4.0 / 2.5 - 1.0)


def test_trend_50_over_200_requires_history() -> None:
    assert trend_50_over_200([100.0] * 100) is None
    flat = trend_50_over_200([100.0] * 200)
    assert flat is not None
    assert math.isclose(flat, 0.0)


def test_window_return() -> None:
    closes = [100.0] * 20 + [110.0]
    value = window_return(closes, 20)
    assert value is not None
    assert math.isclose(value, 0.1)
    assert window_return([100.0], 20) is None


def test_breadth_counts_symbols_above_their_own_sma() -> None:
    rising = [float(i) for i in range(1, 202)]
    falling = [float(i) for i in range(201, 0, -1)]
    breadth = breadth_pct_above_sma({"UP": rising, "DOWN": falling, "SHORT": [1.0]}, 200)
    assert breadth == 0.5  # SHORT is not computable and is excluded


def test_return_dispersion_needs_three_symbols() -> None:
    closes_a = [100.0] * 20 + [110.0]
    closes_b = [100.0] * 20 + [100.0]
    assert return_dispersion({"A": closes_a, "B": closes_b}, 20) is None
    closes_c = [100.0] * 20 + [90.0]
    dispersion = return_dispersion({"A": closes_a, "B": closes_b, "C": closes_c}, 20)
    assert dispersion is not None
    assert math.isclose(dispersion, 0.1)  # returns are +0.1, 0.0, -0.1 → stdev 0.1


def test_compute_features_marks_missing_without_interpolating() -> None:
    features = compute_features(
        primary_closes=[100.0, 101.0],
        closes_by_symbol={"SPY": [100.0, 101.0]},
        hyg_closes=[],
        tlt_closes=[],
    )
    assert features.vol_20d is None
    assert features.credit_hyg_tlt_20d is None
    assert "vol_20d" in features.missing()
    assert "credit_hyg_tlt_20d" in features.missing()


def test_compute_features_full_vector_has_no_missing() -> None:
    steady = [100.0 * (1.001**i) for i in range(260)]
    features = compute_features(
        primary_closes=steady,
        closes_by_symbol={"SPY": steady, "QQQ": steady, "IWM": steady},
        hyg_closes=steady,
        tlt_closes=steady,
    )
    assert features.missing() == []
    payload = features.as_payload()
    assert set(payload) == {
        "vol_20d",
        "vol_trend",
        "pct_above_200dma",
        "trend_50_200",
        "breadth_above_200dma",
        "dispersion_20d",
        "credit_hyg_tlt_20d",
    }
