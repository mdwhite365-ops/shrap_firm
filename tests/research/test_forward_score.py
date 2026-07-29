"""Forward-test scoring: growth over drawdown, and the four refusals.

The load-bearing tests are the refusals. Each guards a case where the quiet
alternative — an infinite score, an annualised three-week sample, a zero
standing in for "unknown" — puts a strategy at the top of the leaderboard
without evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from shrap.research.forward_score import (
    DEFAULT_MIN_SESSIONS_FOR_RATE,
    REASON_NO_DRAWDOWN,
    EquitySample,
    ForwardScore,
    rank_accounts,
    score_account,
)

START = datetime(2026, 7, 1, 14, 30, tzinfo=UTC)


def _curve(values: list[float], *, per_day: int = 1) -> list[EquitySample]:
    """One sample per `1/per_day` of a day, starting at START."""

    step = timedelta(days=1) / per_day
    return [EquitySample(START + i * step, v) for i, v in enumerate(values)]


# --- the arithmetic -----------------------------------------------------------


def test_growth_is_return_since_deployment() -> None:
    r = score_account(_curve([10_000.0, 10_500.0, 11_000.0]))
    assert r.growth == pytest.approx(0.10)


def test_score_is_growth_over_the_drawdown_it_took() -> None:
    """$10k -> $9k -> $11k: up 10%, having been down 10% on the way."""

    r = score_account(_curve([10_000.0, 9_000.0, 11_000.0]))
    assert r.growth == pytest.approx(0.10)
    assert r.max_drawdown == pytest.approx(0.10)
    assert r.score == pytest.approx(1.0)


def test_the_slower_safer_account_outranks_the_faster_riskier_one() -> None:
    """THE ranking this metric exists to produce.

    Raw growth would rank these the other way, and raw growth is what selects
    for the leverage that empties an account.
    """

    steady = score_account(_curve([10_000.0, 9_400.0, 10_500.0]))  # +5%, -6% dd
    wild = score_account(_curve([10_000.0, 5_000.0, 11_500.0]))  # +15%, -50% dd

    assert wild.growth > steady.growth  # the wild one grew more...
    assert steady.score is not None and wild.score is not None
    assert steady.score > wild.score  # ...and still ranks below


def test_a_losing_account_scores_negative_rather_than_erroring() -> None:
    """Down is a real, rankable result, not an exceptional case."""

    r = score_account(_curve([10_000.0, 9_000.0, 8_000.0]))
    assert r.growth == pytest.approx(-0.20)
    assert r.score is not None and r.score < 0


def test_drawdown_is_measured_on_every_sample_not_on_daily_closes() -> None:
    """An intraday round trip that lost 8% and recovered took that risk.

    Snapshots land every ~300s, so the dip is visible. Scoring on closes only
    would hide exactly the behaviour the denominator is meant to penalise.
    """

    intraday = _curve([10_000.0, 9_200.0, 10_400.0], per_day=24)  # hourly, one session
    r = score_account(intraday)
    assert r.sessions == 1
    assert r.max_drawdown == pytest.approx(0.08)


# --- the refusals -------------------------------------------------------------


def test_a_curve_that_never_drew_down_scores_nothing_not_infinity() -> None:
    """THE refusal.

    Dividing by zero would put an untested strategy at the top of the
    leaderboard on no evidence. Its growth is still reported.
    """

    r = score_account(_curve([10_000.0, 10_500.0, 11_000.0]))
    assert r.max_drawdown == 0.0
    assert r.score is None
    assert not r.is_scored
    assert r.growth == pytest.approx(0.10)  # still reported
    assert "infinite" in r.reason and "has not been tested by a loss" in REASON_NO_DRAWDOWN


def test_a_short_window_reports_growth_but_no_annualised_rate() -> None:
    """Annualising three weeks is noise wearing a CAGR's clothes."""

    r = score_account(_curve([10_000.0, 9_500.0, 10_200.0]))
    assert r.sessions < DEFAULT_MIN_SESSIONS_FOR_RATE
    assert r.growth == pytest.approx(0.02)
    assert r.annualised_growth is None


def test_a_long_enough_window_does_annualise() -> None:
    values = [10_000.0 + 40.0 * i for i in range(DEFAULT_MIN_SESSIONS_FOR_RATE)]
    values[5] = 9_800.0  # a dip, so there is a drawdown to divide by
    r = score_account(_curve(values))
    assert r.sessions >= DEFAULT_MIN_SESSIONS_FOR_RATE
    assert r.annualised_growth is not None
    assert r.annualised_growth > r.growth  # compounded out to a year


def test_one_sample_is_not_a_track_record() -> None:
    r = score_account(_curve([10_000.0]))
    assert r.score is None
    assert "fewer than two" in r.reason
    assert r.growth == 0.0


def test_an_empty_curve_is_handled_rather_than_crashing() -> None:
    r = score_account([])
    assert r.samples == 0 and r.sessions == 0 and r.score is None


def test_a_zero_starting_book_refuses_rather_than_dividing_by_it() -> None:
    with pytest.raises(ValueError, match="cannot be a starting book"):
        score_account(_curve([0.0, 5_000.0]))


# --- the leaderboard ----------------------------------------------------------


def _scored(score: float | None) -> ForwardScore:
    return ForwardScore(
        growth=0.1,
        max_drawdown=0.1,
        score=score,
        annualised_growth=None,
        samples=10,
        sessions=10,
        reason="" if score is not None else "unscored",
    )


def test_ranking_puts_the_best_score_first() -> None:
    ranked = rank_accounts({"a": _scored(0.5), "b": _scored(2.0), "c": _scored(1.0)})
    assert [name for name, _ in ranked] == ["b", "c", "a"]


def test_an_unscored_account_sorts_last_never_first() -> None:
    """No score is absence of evidence, not evidence of quality — and not a
    zero either, since a *negative* score still beats knowing nothing."""

    ranked = rank_accounts({"unscored": _scored(None), "losing": _scored(-3.0)})
    assert [name for name, _ in ranked] == ["losing", "unscored"]
