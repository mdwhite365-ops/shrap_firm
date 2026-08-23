"""`shrap-live-benchmark` — reads, renders, and shows both readings.

The rendering matters as much as the arithmetic here. On 2026-08-19 the naive
comparison said the strategy lost by 1.13pp and the exposure-matched one said it
won by 0.37pp, from the same data. A tool that printed only one of those would
settle the argument and lose the lesson.
"""

from __future__ import annotations

import tomllib
from datetime import date, timedelta
from pathlib import Path

import pytest

from shrap.research.live_benchmark import SessionPoint, equal_weight_returns_for_dates
from shrap.research.live_benchmark_cli import AccountSeries, build_parser, render

START = date(2026, 8, 6)


def _series(account: str, equities: list[float], exposure: float) -> AccountSeries:
    return AccountSeries(
        account_id=account,
        points=tuple(
            SessionPoint(
                session_date=START + timedelta(days=i),
                equity=e,
                gross_exposure=e * exposure,
            )
            for i, e in enumerate(equities)
        ),
    )


def _closes(dates: list[date], values: list[float]) -> dict[str, dict[date, float]]:
    return {"AAA": dict(zip(dates, values, strict=True))}


def test_the_cli_is_wired_as_a_script() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    assert pyproject["project"]["scripts"]["shrap-live-benchmark"] == (
        "shrap.research.live_benchmark_cli:main"
    )


def test_disagreeing_readings_are_called_out_explicitly() -> None:
    """A 20%-invested book earning +0.5% through a +1% benchmark move."""

    series = _series("PA3TEST", [10_000.0, 10_050.0], exposure=0.20)
    dates = [p.session_date for p in series.points]
    closes = _closes(dates, [100.0, 101.0])

    out = render([series], closes)

    assert "naive reading: lost to the benchmark" in out
    assert "exposure-matched: beat it" in out
    assert "DISAGREE" in out


def test_agreeing_readings_do_not_raise_the_warning() -> None:
    """A fully invested book that genuinely underperformed."""

    series = _series("PA3TEST", [10_000.0, 9_950.0], exposure=1.0)
    dates = [p.session_date for p in series.points]
    closes = _closes(dates, [100.0, 101.0])

    out = render([series], closes)

    assert "naive reading: lost to the benchmark" in out
    assert "exposure-matched: lost to it" in out
    assert "DISAGREE" not in out


def test_an_unscorable_account_says_why_rather_than_printing_zeroes() -> None:
    series = _series("PA3TEST", [10_000.0], exposure=0.2)

    out = render([series], {})

    assert "not scored" in out
    assert "+0.000%" not in out


def test_benchmark_returns_align_on_dates_not_positions() -> None:
    """The account's sessions and the bar table's need not line up.

    A pass landing on a day with no bar must not shift every later comparison by
    one session, which is what a positional zip would do.
    """

    dates = [START, START + timedelta(days=1), START + timedelta(days=2)]
    closes = {
        "AAA": {dates[0]: 100.0, dates[2]: 110.0},  # no bar on the middle date
        "BBB": {dates[0]: 50.0, dates[1]: 55.0, dates[2]: 60.5},
    }

    returns = equal_weight_returns_for_dates(closes, dates)

    assert len(returns) == 2
    # First transition: only BBB is priced on both ends.
    assert returns[0] == pytest.approx(0.10)
    # Second: BBB again; AAA has no middle price so it contributes to neither.
    assert returns[1] == pytest.approx(0.10)


def test_the_parser_requires_an_explicit_window() -> None:
    """No defaulted date range: "last 30 days" silently changes the answer
    between runs, and this number gets quoted."""

    parser = build_parser()
    args = parser.parse_args(["--start", "2026-08-06", "--end", "2026-08-19"])
    assert args.start == "2026-08-06"
    with pytest.raises(SystemExit):
        parser.parse_args(["--start", "2026-08-06"])
