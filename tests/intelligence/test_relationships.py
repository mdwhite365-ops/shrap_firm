"""Measured links (ADR-0018 card 8).

The series here are built so the answer is known: a name that is the driver
plus noise, one that is the market and nothing else, and one whose link to the
driver flips sign halfway through.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from shrap.intelligence.relationships import (
    VERDICT_STABLE,
    VERDICT_TOO_SHORT,
    VERDICT_UNSTABLE,
    VERDICT_WEAK,
    measure,
    render,
    residual,
    returns_from_closes,
)
from shrap.intelligence.relationships_cli import parse_drivers

N = 630  # ten 63-session windows


def _days(n: int = N) -> list[date]:
    return [date(2022, 1, 3) + timedelta(days=i) for i in range(n)]


def _noise(seed: int, n: int = N, scale: float = 0.01) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(0.0, scale) for _ in range(n)]


def _series(values: list[float]) -> dict[date, float]:
    return dict(zip(_days(len(values)), values, strict=True))


MARKET = _noise(1)
OIL = _noise(2)


def test_returns_are_keyed_by_the_later_session_and_skip_bad_closes() -> None:
    d = _days(4)

    out = returns_from_closes([(d[2], 110.0), (d[0], 100.0), (d[1], 0.0), (d[3], 121.0)])

    assert out == {d[3]: pytest.approx(0.10)}


def test_a_name_that_is_only_the_market_has_no_link_to_oil_once_the_market_is_removed() -> None:
    """Raw, an oil fund and a stock that both ride the market correlate. That is
    two things going up with SPY, and says nothing about each other."""

    driver = [0.8 * m + o for m, o in zip(MARKET, OIL, strict=True)]
    name = [1.2 * m + e for m, e in zip(MARKET, _noise(3, scale=0.002), strict=True)]

    link = measure("XLE", "AAPL", _series(driver), _series(name), _series(MARKET))

    assert link.corr is not None and link.corr > 0.4
    assert link.corr_ex_market == pytest.approx(0.0, abs=0.1)
    assert link.verdict in (VERDICT_WEAK, VERDICT_UNSTABLE)


def test_a_name_that_falls_when_oil_rises_is_stable_and_negative() -> None:
    driver = [0.8 * m + o for m, o in zip(MARKET, OIL, strict=True)]
    name = [m - 0.7 * o + e for m, o, e in zip(MARKET, OIL, _noise(4, scale=0.004), strict=True)]

    link = measure("XLE", "AIRLINE", _series(driver), _series(name), _series(MARKET))

    assert link.corr_ex_market is not None and link.corr_ex_market < -0.6
    assert link.beta_ex_market == pytest.approx(-0.7, abs=0.1)
    assert (link.windows, link.agreement, link.verdict) == (10, 1.0, VERDICT_STABLE)


def test_a_link_that_flips_sign_halfway_is_unstable_whatever_the_whole_span_says() -> None:
    """Negative for six windows, positive for four. The whole-span figure is a
    mild negative that never described any actual quarter."""

    half = 6 * 63
    name = [
        m + (-0.8 if i < half else 0.8) * o + e
        for i, (m, o, e) in enumerate(zip(MARKET, OIL, _noise(5, scale=0.004), strict=True))
    ]

    link = measure("XLE", "FLIP", _series(OIL), _series(name), _series(MARKET))

    assert link.corr_ex_market is not None and link.corr_ex_market < 0
    assert link.agreement == pytest.approx(0.6)
    assert link.latest_window is not None and link.latest_window > 0.5
    assert link.verdict != VERDICT_STABLE

    more_flipped = [
        m + (-0.8 if i < 5 * 63 else 0.8) * o
        for i, (m, o) in enumerate(zip(MARKET, OIL, strict=True))
    ]
    assert (
        measure("XLE", "FLIP", _series(OIL), _series(more_flipped), _series(MARKET)).verdict
        == VERDICT_UNSTABLE
    )


def test_too_few_windows_is_not_a_verdict() -> None:
    n = 3 * 63
    name = [-0.9 * o for o in OIL[:n]]

    link = measure("XLE", "NEW", _series(OIL[:n]), _series(name), _series(MARKET[:n]))

    assert (link.windows, link.verdict) == (3, VERDICT_TOO_SHORT)


def test_the_newest_window_is_always_whole() -> None:
    """A stub is dropped from the old end, so `latest` describes a full quarter."""

    n = 63 * 4 + 20
    link = measure("XLE", "X", _series(OIL[:n]), _series(_noise(6, n)), None)

    assert link.windows == 4


def test_the_market_itself_is_measured_raw() -> None:
    name = [1.1 * m + e for m, e in zip(MARKET, _noise(7, scale=0.003), strict=True)]

    link = measure("SPY", "QQQ", _series(MARKET), _series(name), None)

    assert link.corr_ex_market == pytest.approx(link.corr)
    assert link.verdict == VERDICT_STABLE


def test_only_sessions_both_have_are_used() -> None:
    days = _days()
    driver = dict(zip(days[:400], OIL[:400], strict=True))
    name = dict(zip(days[100:], OIL[100:], strict=True))

    link = measure("XLE", "X", driver, name, None)

    assert (link.sessions, link.first, link.last) == (300, days[100], days[399])


def test_removing_the_market_leaves_nothing_of_a_pure_market_series() -> None:
    left = residual(_series([2.0 * m for m in MARKET]), _series(MARKET))

    assert max(abs(v) for v in left.values()) == pytest.approx(0.0, abs=1e-12)


def test_the_report_ranks_by_strength_and_states_its_thresholds() -> None:
    strong = measure(
        "XLE",
        "AIRLINE",
        _series(OIL),
        _series([m - 0.7 * o for m, o in zip(MARKET, OIL, strict=True)]),
        _series(MARKET),
    )
    none = measure("XLE", "AAPL", _series(OIL), _series(_noise(8)), _series(MARKET))

    text = render([none, strong], market="SPY", window=63)

    assert text.index("| AIRLINE |") < text.index("| AAPL |")
    assert "with SPY removed from both sides" in text
    assert "| stable |" in text and "## XLE" in text


def test_drivers_are_parsed_forgivingly() -> None:
    assert parse_drivers(" xle, TLT ,,gld") == ["XLE", "TLT", "GLD"]
