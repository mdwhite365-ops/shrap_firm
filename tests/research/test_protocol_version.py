"""The protocol version must move when the measurement changes.

Caught on the first live dry-run after #138/#139: a 1,510-bar result printed
`protocol=0.1` beside a stored 506-bar one. The run was a dry run so nothing was
corrupted, but committing it would have made two incomparable measurements
indistinguishable in `research.evaluations` — the one thing this constant exists
to prevent.
"""

from __future__ import annotations

from datetime import date, timedelta

from shrap.research.strategy_evaluator.benchmark import EqualWeightBuyAndHold
from shrap.research.strategy_evaluator.engine import PROTOCOL_VERSION, EvalConfig
from shrap.research.strategy_evaluator.strategy import BarSample, PricePanel

_INTERSECTION_ERA = "0.1"


def _bars(n: int, *, offset: int = 0) -> list[BarSample]:
    start = date(2020, 1, 6)
    return [
        BarSample(start + timedelta(days=offset + i), 10.0, 10.0, 10.0, 10.0, 1.0e6)
        for i in range(n)
    ]


def test_the_version_left_the_intersection_era_behind() -> None:
    """``0.1`` denotes the intersected panel, the full-roster benchmark and the
    five-year default lookback. None of those is what runs now, so the label
    must not be reused for results produced under the current protocol."""

    assert PROTOCOL_VERSION != _INTERSECTION_ERA


def test_the_version_and_the_panel_model_move_together() -> None:
    """Tied in one assertion deliberately.

    Whoever reverts the panel to an intersection, or restores the five-year
    default, has to confront the version in the same breath — the failure this
    guards is silent and only shows up as two rows that look comparable.
    """

    bars = {"OLD": _bars(300), "NEW": _bars(50, offset=250)}
    panel = PricePanel.from_bars(bars)

    union_aligned = panel.n_bars == 300
    lookback_uncapped = EvalConfig().window_years is None

    if union_aligned or lookback_uncapped:
        assert PROTOCOL_VERSION != _INTERSECTION_ERA, (
            "the panel or lookback is post-0.1 but the protocol version is not"
        )


def test_the_benchmark_change_is_covered_by_the_same_version() -> None:
    """The benchmark is the promote gate, so a change to it invalidates every
    information ratio on record just as surely as a change to the panel."""

    bars = {"A": _bars(100), "B": _bars(100), "C": _bars(40, offset=60)}
    panel = PricePanel.from_bars(bars)
    weights = EqualWeightBuyAndHold().target_weights(panel.window(0))

    # Pre-0.2 this was 1/3 each, including the name that had not listed.
    assert weights["C"] == 0.0
    assert weights["A"] == 0.5
    assert PROTOCOL_VERSION != _INTERSECTION_ERA
