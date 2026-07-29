"""Coverage reporting for the price panel.

Every gate in the protocol is a claim about a sample, and the sample size was
the one number the verdict never carried (#136).

**Rewritten for the ragged panel.** These tests originally pinned the
intersection: one short ticker truncated everyone, and coverage existed to make
that visible. The panel now spans the union, so the question changed — not *how
much history did one young name cost everyone*, which is now none, but *how
many names was this measured across, and from when*.
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


def test_a_short_ticker_no_longer_costs_the_panel_anything() -> None:
    """The ETHA case, inverted. 49 long histories and one short one used to
    yield a 100-bar panel; it now yields the full 500."""

    bars_by_ticker = {f"OLD{i}": _bars(500) for i in range(49)}
    bars_by_ticker["ETHA"] = _bars(100, offset=400)

    panel = PricePanel.from_bars(bars_by_ticker)
    coverage = PanelCoverage.from_bars(bars_by_ticker)

    assert panel.n_bars == 500
    assert coverage.n_bars == panel.n_bars

    # What is still true, and still worth reporting: the universe was only
    # complete for the last 100 bars, so the early folds ranked 49 names.
    assert coverage.fully_covered == 100
    thinnest = coverage.thinnest
    assert thinnest is not None
    assert thinnest.ticker == "ETHA"
    assert thinnest.missing == 400


def test_the_summary_reports_span_and_completeness() -> None:
    """A bare panel length would read as 'the universe looked like this
    throughout', which is the misreading to prevent."""

    bars_by_ticker = {"OLD": _bars(500), "NEW": _bars(100, offset=400)}
    assert PanelCoverage.from_bars(bars_by_ticker).summary() == "bars=500 complete=100 thinnest=NEW"


def test_a_complete_universe_reports_only_its_length() -> None:
    """When every ticker covers every date, `complete` equals `bars` and
    repeating it is noise — and there is no thinnest name to name."""

    coverage = PanelCoverage.from_bars({"AAA": _bars(250), "BBB": _bars(250)})

    assert coverage.thinnest is None
    assert coverage.fully_covered == coverage.n_bars == 250
    assert coverage.summary() == "bars=250"


def test_interior_gaps_count_as_readily_as_a_late_listing() -> None:
    """A ticker present across the full span but missing scattered sessions
    shrinks the cross-section on those dates.

    Ranking by history *length* would rate it complete — it starts on day one
    and ends on the last day. Ranking by dates missed catches it, which is why
    ``thinnest`` is defined that way.
    """

    bars_by_ticker = {
        "SOLID": _bars(200),
        "SWISS": _bars(200, skip={10, 20, 30, 40, 50}),
    }
    coverage = PanelCoverage.from_bars(bars_by_ticker)

    thinnest = coverage.thinnest
    assert thinnest is not None
    assert thinnest.ticker == "SWISS"
    # The panel keeps all 200 dates; only 195 have the whole universe.
    assert coverage.n_bars == 200
    assert coverage.fully_covered == 195
    assert thinnest.first_date == _START  # full span, still incomplete


def test_disjoint_histories_span_both_rather_than_collapsing() -> None:
    """Two tickers that never traded on the same day. Under the intersection
    this was an empty panel; under the union it is a 100-bar panel that never
    held both names at once."""

    coverage = PanelCoverage.from_bars({"EARLY": _bars(50), "LATE": _bars(50, offset=400)})

    assert coverage.n_bars == 100
    assert coverage.fully_covered == 0
    assert coverage.first_date == _START
    assert coverage.summary().startswith("bars=100 complete=0")


def test_coverage_matches_the_panel_it_describes() -> None:
    """Both are computed from the same mapping, so they cannot disagree about
    the extent of the data.

    This is the test that caught the ragged-panel change breaking coverage:
    ``n_bars`` was still the intersection after the panel became the union,
    which would have printed a sample size the run never used.
    """

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
