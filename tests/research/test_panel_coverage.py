"""Coverage reporting for the intersected price panel.

The motivating case is real. The firm's first cross-sectional verdict ran the
momentum rule over the 50-name launch list, which contains ETHA — listed
2024-07, while the runbook instructs a backfill from 2018-01-01. Because
``PricePanel`` aligns on dates *every* ticker has, the panel was as short as
ETHA's history and every other name's extra years were discarded. The verdict,
the summary line and the evaluation card all reported metrics without reporting
the sample they were computed on.

These tests pin the reporting, not the intersection: dropping the dates is
correct point-in-time behaviour and stays. What changes is that it says so.
"""

from __future__ import annotations

from datetime import date, timedelta

from shrap.research.strategy_evaluator.strategy import BarSample, PanelCoverage, PricePanel

_START = date(2020, 1, 6)


def _bars(n: int, *, offset: int = 0, skip: set[int] | None = None) -> list[BarSample]:
    """``n`` consecutive daily bars starting ``offset`` days in."""

    skip = skip or set()
    return [
        BarSample(
            session_date=_START + timedelta(days=offset + i),
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1.0e6,
        )
        for i in range(n)
        if i not in skip
    ]


def test_one_short_ticker_truncates_the_whole_panel() -> None:
    """The ETHA case, in miniature: 49 long histories and one short one."""

    bars_by_ticker = {f"OLD{i}": _bars(500) for i in range(49)}
    bars_by_ticker["ETHA"] = _bars(100, offset=400)

    panel = PricePanel.from_bars(bars_by_ticker)
    coverage = PanelCoverage.from_bars(bars_by_ticker)

    # The intersection keeps only the window ETHA also covers.
    assert panel.n_bars == 100
    assert coverage.n_bars == panel.n_bars
    assert coverage.candidate_bars == 500

    # And it names the ticker responsible rather than leaving it to be inferred
    # from fifty inception dates.
    worst = coverage.worst
    assert worst is not None
    assert worst.ticker == "ETHA"
    assert worst.missing == 400


def test_the_summary_field_carries_both_numbers() -> None:
    """``bars=100/500`` — the ratio is the point. A bare panel length reads as
    'this is how much data there was', which is the misreading to prevent."""

    bars_by_ticker = {"OLD": _bars(500), "NEW": _bars(100, offset=400)}
    assert PanelCoverage.from_bars(bars_by_ticker).summary() == "bars=100/500 binds=NEW"


def test_a_lossless_panel_names_no_binding_ticker() -> None:
    """When every ticker covers every date there is nothing to blame, and the
    summary must not invent a culprit by picking an arbitrary ticker."""

    coverage = PanelCoverage.from_bars({"AAA": _bars(250), "BBB": _bars(250)})

    assert coverage.worst is None
    assert coverage.summary() == "bars=250/250"


def test_interior_gaps_bind_as_hard_as_a_late_listing() -> None:
    """A ticker present across the full span but missing scattered sessions
    punches holes in every other ticker's history too.

    Ranking by history *length* would rate this ticker as complete — it starts
    on day one and ends on the last day. Ranking by dates missed catches it,
    which is why ``worst`` is defined that way.
    """

    bars_by_ticker = {
        "SOLID": _bars(200),
        "SWISS": _bars(200, skip={10, 20, 30, 40, 50}),
    }
    coverage = PanelCoverage.from_bars(bars_by_ticker)

    worst = coverage.worst
    assert worst is not None
    assert worst.ticker == "SWISS"
    assert coverage.n_bars == 195
    assert worst.first_date == _START  # full span, still the binding constraint


def test_disjoint_histories_report_an_empty_panel_not_a_crash() -> None:
    """Two tickers that never traded on the same day. The engine will refuse
    this on insufficient data; coverage still has to describe it."""

    coverage = PanelCoverage.from_bars({"EARLY": _bars(50), "LATE": _bars(50, offset=400)})

    assert coverage.n_bars == 0
    assert coverage.candidate_bars == 100
    assert coverage.first_date is None
    assert coverage.last_date is None
    assert coverage.summary().startswith("bars=0/100")


def test_coverage_matches_the_panel_it_describes() -> None:
    """Both are computed from the same mapping, so they cannot disagree about
    the extent of the data — the failure being guarded is a coverage report
    that describes a different fetch than the one that produced the verdict."""

    bars_by_ticker = {
        "AAA": _bars(300),
        "BBB": _bars(300, skip={7}),
        "CCC": _bars(120, offset=180),
    }
    panel = PricePanel.from_bars(bars_by_ticker)
    coverage = PanelCoverage.from_bars(bars_by_ticker)

    assert coverage.n_bars == panel.n_bars
    assert coverage.first_date == panel.dates[0]
    assert coverage.last_date == panel.dates[-1]
