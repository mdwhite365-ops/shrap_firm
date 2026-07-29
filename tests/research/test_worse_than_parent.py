"""A revision that loses to the strategy it revised is killed, not held.

The standdown revision (2026-07-29) came back worse than its parent on every
metric — ir 0.392 -> 0.158, sharpe 0.782 -> 0.690 — and earned the identical
verdict, `hold-for-data (below-sharpe-floor)`. Nothing compared it to the thing
it was supposed to improve on.

Held rather than killed, a lineage accumulates strictly-worse attempts that all
read as `hypothesis`, and the attempt count from #141 then measures a search
that was never pruned.
"""

from __future__ import annotations

from shrap.research.strategy_evaluator.verdict import (
    REASON_BELOW_SHARPE_FLOOR,
    REASON_NO_ACTIVE_EDGE,
    REASON_PROMOTE,
    REASON_WORSE_THAN_PARENT,
    VERDICT_HOLD,
    VERDICT_KILL,
    VERDICT_PROMOTE,
    map_verdict,
)

_PASSING = {
    "anchor_required": False,
    "anchor_fresh": False,
    "total_trades": 2507,
    "min_trades": 150,
    "sharpe_floor": 1.0,
    "information_ratio_floor": 0.5,
    "stress_sharpe": 0.7,
}


def _verdict(**over: object):
    return map_verdict(**{**_PASSING, **over})  # type: ignore[arg-type]


# --- the real case ------------------------------------------------------------


def test_the_standdown_revision_would_now_be_killed() -> None:
    """The actual numbers from the Dell, 2026-07-29."""

    result = _verdict(
        base_sharpe=0.690,
        stress_sharpe=0.653,
        information_ratio=0.158,
        parent_information_ratio=0.392,
    )

    assert result.verdict == VERDICT_KILL
    assert result.reason == REASON_WORSE_THAN_PARENT


def test_the_parent_itself_is_untouched_by_the_gate() -> None:
    """An original has no parent, so the comparison never runs and the parent
    keeps the hold-for-data it earned."""

    result = _verdict(base_sharpe=0.782, information_ratio=0.392)

    assert result.verdict == VERDICT_HOLD
    assert result.reason == REASON_BELOW_SHARPE_FLOOR


# --- the comparison -----------------------------------------------------------


def test_an_improvement_survives() -> None:
    result = _verdict(base_sharpe=0.9, information_ratio=0.45, parent_information_ratio=0.392)

    assert result.verdict == VERDICT_HOLD
    assert result.reason == REASON_BELOW_SHARPE_FLOOR


def test_merely_matching_the_parent_is_not_improving_on_it() -> None:
    """A revision that changes something and achieves nothing is noise in the
    lineage — it inflates the attempt count without adding evidence."""

    result = _verdict(base_sharpe=0.782, information_ratio=0.392, parent_information_ratio=0.392)

    assert result.verdict == VERDICT_KILL
    assert result.reason == REASON_WORSE_THAN_PARENT


def test_a_revision_that_clears_every_floor_is_still_killed_if_it_lost() -> None:
    """The case worth arguing about, and the reason it is a kill.

    If the revision beat the promote floors, the parent beat them by MORE — so
    promoting the revision instead would be strictly worse. There is nothing
    further data can say about a variant already dominated by the thing it
    varied.
    """

    result = _verdict(base_sharpe=1.4, information_ratio=0.60, parent_information_ratio=0.75)

    assert result.verdict == VERDICT_KILL
    assert result.reason == REASON_WORSE_THAN_PARENT


def test_a_revision_that_clears_the_floors_and_beat_its_parent_promotes() -> None:
    result = _verdict(base_sharpe=1.4, information_ratio=0.80, parent_information_ratio=0.75)

    assert result.verdict == VERDICT_PROMOTE
    assert result.reason == REASON_PROMOTE


# --- when the comparison cannot be made --------------------------------------


def test_an_unmeasured_parent_does_not_condemn_its_revision() -> None:
    """`None` means never measured comparably — no evaluation, or none since the
    protocol changed. "Cannot compare" is not "did not improve"."""

    result = _verdict(base_sharpe=0.690, information_ratio=0.158, parent_information_ratio=None)

    assert result.verdict == VERDICT_HOLD
    assert result.reason == REASON_BELOW_SHARPE_FLOOR


def test_losing_to_the_benchmark_outranks_losing_to_the_parent() -> None:
    """Both are kills; the reported reason should be the more fundamental one.
    A strategy that lost to simply owning the names has a bigger problem than
    its ancestry."""

    result = _verdict(base_sharpe=0.3, information_ratio=-0.2, parent_information_ratio=0.392)

    assert result.verdict == VERDICT_KILL
    assert result.reason == REASON_NO_ACTIVE_EDGE


def test_a_parent_that_lost_to_the_benchmark_is_still_a_bar_to_clear() -> None:
    """A negative parent IR is a low bar, not no bar. A revision that fails to
    beat even that has improved on nothing."""

    result = _verdict(base_sharpe=0.5, information_ratio=0.05, parent_information_ratio=0.10)

    assert result.verdict == VERDICT_KILL
    assert result.reason == REASON_WORSE_THAN_PARENT
