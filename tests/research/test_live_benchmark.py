"""Live account against an exposure-matched benchmark.

The measurement that reverses its own conclusion when done naively. Ten live
sessions to 2026-08-19:

    equal-weight buy-and-hold   +1.825%
    PA3KQN57WVXY                +0.70%   -> "lost to the benchmark"
    ...averaging 17.9% invested
    17.9% of +1.825%            +0.33%   -> "beat the benchmark"

Both readings come from the same data and disagree about the sign.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from shrap.research.live_benchmark import (
    REASON_TOO_FEW_POINTS,
    SessionPoint,
    compare_to_benchmark,
    equal_weight_session_returns,
)

START = date(2026, 8, 6)


def _points(equities: list[float], exposure_fraction: float) -> list[SessionPoint]:
    return [
        SessionPoint(
            session_date=START + timedelta(days=i),
            equity=e,
            gross_exposure=e * exposure_fraction,
        )
        for i, e in enumerate(equities)
    ]


def test_a_fifth_invested_book_is_not_judged_against_a_full_one() -> None:
    """The correction that changes the sign.

    A book held at 20% through a +1% benchmark move is entitled to +0.2%. Earning
    +0.5% is beating the benchmark, not losing to it, even though 0.5 < 1.0.
    """

    points = _points([10_000.0, 10_050.0], exposure_fraction=0.20)

    result = compare_to_benchmark(points, [0.01], min_sessions=1)

    assert result.is_scored
    assert result.account_return == pytest.approx(0.005)
    assert result.benchmark_return == pytest.approx(0.01)
    assert result.entitled_return == pytest.approx(0.002)
    assert result.excess == pytest.approx(0.003)
    # The naive reading says it lost. Both are reported so the difference is
    # noticed rather than argued about.
    assert not result.beat_benchmark_naively


def test_exposure_comes_from_the_prior_session_not_the_current_one() -> None:
    """Today's return is earned on yesterday's position.

    A book that was flat yesterday and fully invested at today's close earned
    nothing from today's move, and must not be credited for it.
    """

    points = [
        SessionPoint(session_date=START, equity=10_000.0, gross_exposure=0.0),
        SessionPoint(
            session_date=START + timedelta(days=1), equity=10_000.0, gross_exposure=10_000.0
        ),
    ]

    result = compare_to_benchmark(points, [0.05], min_sessions=1)

    assert result.entitled_return == pytest.approx(0.0)  # flat going in
    assert result.excess == pytest.approx(0.0)  # earned nothing, owed nothing


def test_a_flat_book_is_owed_nothing_and_scores_zero_excess() -> None:
    """All cash through a rally neither beats nor loses. It did not play."""

    points = _points([10_000.0, 10_000.0, 10_000.0], exposure_fraction=0.0)

    result = compare_to_benchmark(points, [0.02, 0.03], min_sessions=1)

    assert result.entitled_return == pytest.approx(0.0)
    assert result.excess == pytest.approx(0.0)
    assert result.benchmark_return == pytest.approx(0.0506)


def test_a_short_window_is_scored_but_flagged_underpowered() -> None:
    """Reported, not refused. An excess over four sessions is a fact about
    those four sessions; the caller decides what it is worth."""

    points = _points([10_000.0, 10_010.0, 10_020.0], exposure_fraction=0.2)

    result = compare_to_benchmark(points, [0.001, 0.001], min_sessions=5)

    assert result.is_scored
    assert result.underpowered


def test_two_samples_are_needed_before_anything_is_measured() -> None:
    result = compare_to_benchmark(_points([10_000.0], 0.2), [])

    assert not result.is_scored
    assert result.reason == REASON_TOO_FEW_POINTS


def test_a_mismatched_benchmark_series_refuses_rather_than_truncating() -> None:
    """Silently zipping to the shorter series compares different periods."""

    points = _points([10_000.0, 10_010.0, 10_020.0], exposure_fraction=0.2)

    result = compare_to_benchmark(points, [0.01])

    assert not result.is_scored
    assert "same sessions" in result.reason


def test_a_short_history_ticker_does_not_truncate_the_universe() -> None:
    """The panel trap, in the benchmark this time.

    A name with one close contributes nothing rather than capping every other
    name's window — the failure that silently shortened a backtest in July.
    """

    returns = equal_weight_session_returns(
        {
            "AAA": [100.0, 110.0, 121.0],
            "BBB": [50.0, 55.0, 60.5],
            "NEW": [10.0],  # listed yesterday
        }
    )

    assert len(returns) == 2  # not truncated to zero by NEW
    assert returns[0] == pytest.approx(0.10)
    assert returns[1] == pytest.approx(0.10)


def test_the_2026_08_19_measurement_reproduces() -> None:
    """The live case that motivated the module, with the real figures.

    Ten sessions, benchmark +1.825%, account +0.70%, average exposure 17.9%.
    The naive comparison says the strategy lost by 1.13pp. Exposure-matched, it
    beat what it was entitled to by roughly 0.37pp.

    Not evidence of skill — ten sessions and no beta adjustment — but it settles
    that the two readings disagree about the sign, which is the point.
    """

    # A flat-exposure book whose equity ends +0.70%, over ten benchmark moves
    # compounding to +1.825%.
    per_session = 1.01825 ** (1 / 10) - 1.0
    equities = [10_000.0 * (1.0070 ** (i / 10)) for i in range(11)]
    points = _points(equities, exposure_fraction=0.179)

    result = compare_to_benchmark(points, [per_session] * 10)

    assert result.sessions == 10
    assert result.account_return == pytest.approx(0.0070, abs=1e-6)
    assert result.benchmark_return == pytest.approx(0.01825, abs=1e-6)
    assert result.average_exposure == pytest.approx(0.179)
    # Entitled to ~0.179 x 1.825% = 0.327%.
    assert result.entitled_return == pytest.approx(0.00327, abs=5e-5)
    assert result.excess == pytest.approx(0.0037, abs=5e-5)
    assert not result.beat_benchmark_naively  # 0.70% < 1.825%
    assert result.excess > 0  # ...and yet it beat what it was owed
