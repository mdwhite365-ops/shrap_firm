"""``history_cached`` must return exactly what ``history`` returns, always.

This is an optimisation card, so the ONLY thing that matters is that it changes
no result. A backtest is a long chain of arithmetic over these tuples; a single
element out of place, or one extra bar visible at one index, would move every
number downstream and there would be nothing in the output to say so.

So the reference implementation stays in the code and these tests assert
bit-identity against it, exhaustively — every ticker, every index, including the
out-of-range ones — over panels built to contain the awkward cases:

- a name that listed midway (RIVN listed 2021-11-10, ETHA 2024-07-23 on the
  firm's real universe, so ragged starts are the normal case and not an edge)
- a name absent in the middle of its own history
- a name with no bars at all
- indexes below zero and past the end
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest

from shrap.research.strategy_evaluator.strategy import BarSample, PricePanel


def _panel(spec: dict[str, list[bool]]) -> PricePanel:
    """A panel where ``spec[ticker][i]`` says whether that name traded on bar i."""

    n = max((len(v) for v in spec.values()), default=0)
    dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]
    bars: dict[str, list[BarSample]] = {}
    for ticker, flags in spec.items():
        rows = []
        for i, live in enumerate(flags):
            if not live:
                continue
            price = 100.0 + i + hash(ticker) % 7
            rows.append(
                BarSample(
                    session_date=dates[i],
                    open=price,
                    high=price + 1,
                    low=price - 1,
                    close=price,
                    volume=1000.0 + i,
                )
            )
        bars[ticker] = rows
    return PricePanel.from_bars(bars)


def _assert_identical(panel: PricePanel) -> None:
    """Every ticker, every index, both series, against the reference."""

    for ticker in panel.tickers:
        for index in range(-2, panel.n_bars + 2):
            for name, series in (("closes", panel.closes), ("volumes", panel.volumes)):
                reference = panel.history(ticker, series, index)
                cached = panel.history_cached(ticker, name, index)
                assert cached == reference, (
                    f"{ticker} {name} at index {index}: "
                    f"cached={cached[:5]}... reference={reference[:5]}..."
                )


def test_a_dense_panel_is_identical() -> None:
    _assert_identical(_panel({"AAA": [True] * 12, "BBB": [True] * 12}))


def test_a_name_that_listed_midway_is_identical() -> None:
    """The real case: ETHA launched 2024-07-23 into a panel starting 2021."""

    _assert_identical(_panel({"OLD": [True] * 12, "NEW": [False] * 8 + [True] * 4}))


def test_a_hole_in_the_middle_is_identical() -> None:
    """A halted name. The compressed series must close the gap, not pad it."""

    _assert_identical(_panel({"AAA": [True] * 12, "GAPPY": [True] * 4 + [False] * 3 + [True] * 5}))


def test_a_name_with_a_single_bar_is_identical() -> None:
    _assert_identical(_panel({"AAA": [True] * 12, "ONCE": [False] * 11 + [True]}))


def test_an_unknown_ticker_returns_empty_both_ways() -> None:
    panel = _panel({"AAA": [True] * 5})
    assert panel.history_cached("NOPE", "closes", 3) == ()
    assert panel.history("NOPE", panel.closes, 3) == ()


def test_an_unknown_series_name_returns_empty() -> None:
    """A series with no precomputed form falls through rather than guessing."""

    panel = _panel({"AAA": [True] * 5})
    assert panel.history_cached("AAA", "opens", 3) == ()


@pytest.mark.parametrize("seed", range(25))
def test_randomised_panels_are_identical(seed: int) -> None:
    """Fuzzed against the reference. 25 seeds x every ticker x every index."""

    rng = random.Random(seed)
    n_tickers = rng.randint(1, 5)
    n_bars = rng.randint(1, 18)
    spec = {f"T{t}": [rng.random() > 0.3 for _ in range(n_bars)] for t in range(n_tickers)}
    # At least one name must trade, or from_bars has nothing to build a grid on.
    if not any(any(flags) for flags in spec.values()):
        spec["T0"][0] = True
    _assert_identical(_panel(spec))


def test_the_cache_holds_no_absent_bars() -> None:
    """Absent bars carry nan and must never reach a compressed series.

    A nan that leaked in would not raise — it would poison one name's formation
    return and silently drop it out of every ranking.
    """

    panel = _panel({"AAA": [True] * 10, "GAPPY": [True] * 3 + [False] * 4 + [True] * 3})
    for ticker in panel.tickers:
        values = panel.history_cached(ticker, "closes", panel.n_bars - 1)
        assert not any(math.isnan(v) for v in values)
        # And its length is how many times the name actually traded.
        assert len(values) == sum(panel.live[ticker])


def test_panels_with_equal_inputs_remain_equal() -> None:
    """The cache fields must not enter equality, or two identical panels differ."""

    assert _panel({"AAA": [True] * 6}) == _panel({"AAA": [True] * 6})
