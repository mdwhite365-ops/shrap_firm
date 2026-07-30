"""The promote bar rises with the size of the search that produced the candidate.

Mike, 2026-07-30: *"let finish what we need to get strats testing till we find
some that pass."* That is the correct thing to want and it is also, exactly, the
procedure that manufactures false positives — test enough variants of one idea
and something clears any fixed floor by luck.

The firm has counted this since PR #141. `StrategyRegistry.attempts` even says
why, and then declines to act:

    "A lineage on attempt 20 that finally clears an information ratio of 0.5 has
    not found edge — it has found the best of twenty draws, and the gate cannot
    know that without this number. Reported rather than enforced: what the gate
    should DO about a long lineage is a calibration, and calibrations are Mike's."

This is the gate reading it. The calibration is still Mike's; the default is a
defensible curve rather than a derived constant.
"""

from __future__ import annotations

import pytest

from shrap.research.strategy_evaluator.verdict import (
    REASON_BELOW_ADJUSTED_IR_FLOOR,
    REASON_BELOW_INFORMATION_RATIO_FLOOR,
    REASON_NO_ACTIVE_EDGE,
    REASON_PROMOTE,
    REASON_WORSE_THAN_PARENT,
    VERDICT_HOLD,
    VERDICT_KILL,
    VERDICT_PROMOTE,
    map_verdict,
    required_information_ratio,
)

_CLEARS_EVERYTHING = {
    "anchor_required": False,
    "anchor_fresh": False,
    "total_trades": 2507,
    "min_trades": 150,
    "sharpe_floor": 1.0,
    "information_ratio_floor": 0.5,
    "base_sharpe": 1.4,
    "stress_sharpe": 1.1,
}


def _verdict(**over: object):
    return map_verdict(**{**_CLEARS_EVERYTHING, **over})  # type: ignore[arg-type]


# --- the curve ----------------------------------------------------------------


def test_a_first_attempt_is_not_penalised() -> None:
    """Every strategy evaluated before this card was attempt 1, so none of their
    verdicts change."""

    assert required_information_ratio(0.5, 1) == 0.5


def test_zero_or_negative_attempts_do_not_lower_the_bar() -> None:
    """A registry that answered nonsensically must not make promotion easier."""

    assert required_information_ratio(0.5, 0) == 0.5
    assert required_information_ratio(0.5, -3) == 0.5


def test_the_bar_rises_with_every_attempt() -> None:
    floors = [required_information_ratio(0.5, n) for n in range(1, 21)]

    assert floors == sorted(floors)
    assert floors[0] < floors[-1]


def test_the_documented_curve_is_what_the_code_computes() -> None:
    """The table in the docstring is the calibration Mike is being asked to
    accept, so it has to be the one that actually runs."""

    assert round(required_information_ratio(0.5, 2), 2) == 0.65
    assert round(required_information_ratio(0.5, 3), 2) == 0.72
    assert round(required_information_ratio(0.5, 5), 2) == 0.81
    assert round(required_information_ratio(0.5, 10), 2) == 0.91
    assert round(required_information_ratio(0.5, 20), 2) == 1.00


def test_it_grows_sublinearly_so_a_long_lineage_stays_promotable() -> None:
    """A bar that rose linearly would make any well-explored idea unpromotable
    regardless of merit, which is a different failure from the one being fixed.
    Twenty attempts double the bar; they do not twentyfold it."""

    assert required_information_ratio(0.5, 20) < 2 * required_information_ratio(0.5, 1) + 1e-9


# --- what it changes ----------------------------------------------------------


def test_a_first_attempt_clearing_the_floor_still_promotes() -> None:
    result = _verdict(information_ratio=0.55, attempts=1)

    assert result.verdict == VERDICT_PROMOTE
    assert result.reason == REASON_PROMOTE


def test_the_same_number_on_the_fifth_attempt_does_not() -> None:
    """The whole card in one test. An identical measurement means something
    different depending on how many tries produced it."""

    result = _verdict(information_ratio=0.55, attempts=5)

    assert result.verdict == VERDICT_HOLD
    assert result.reason == REASON_BELOW_ADJUSTED_IR_FLOOR


def test_a_fifth_attempt_that_clears_the_raised_bar_promotes() -> None:
    """The adjustment is a higher bar, not a ban. An idea that keeps improving
    can still earn promotion — it just has to earn more of it."""

    result = _verdict(information_ratio=0.90, attempts=5)

    assert result.verdict == VERDICT_PROMOTE


def test_the_adjusted_failure_holds_rather_than_kills() -> None:
    """Unlike `worse-than-parent`, this strategy has not been shown to be bad.
    It has been shown to be insufficiently distinguished from luck GIVEN the
    search, and more out-of-sample data is exactly what settles that."""

    result = _verdict(information_ratio=0.55, attempts=5)

    assert result.verdict == VERDICT_HOLD


def test_the_reason_is_distinct_from_the_plain_floor_miss() -> None:
    """Two different problems that would otherwise read identically on the card:
    "not good enough" versus "not good enough for how hard we looked"."""

    plain = _verdict(information_ratio=0.40, attempts=1)
    adjusted = _verdict(information_ratio=0.55, attempts=5)

    assert plain.reason == REASON_BELOW_INFORMATION_RATIO_FLOOR
    assert adjusted.reason == REASON_BELOW_ADJUSTED_IR_FLOOR


# --- priority against the other gates ----------------------------------------


def test_losing_to_the_benchmark_still_outranks_the_search_penalty() -> None:
    """Both hold the strategy back; the reported reason should be the more
    fundamental one. Having no edge at all is a bigger problem than having
    unproven edge."""

    result = _verdict(information_ratio=-0.2, attempts=10)

    assert result.verdict == VERDICT_KILL
    assert result.reason == REASON_NO_ACTIVE_EDGE


def test_being_worse_than_the_parent_outranks_it_too() -> None:
    result = _verdict(information_ratio=0.55, parent_information_ratio=0.60, attempts=5)

    assert result.verdict == VERDICT_KILL
    assert result.reason == REASON_WORSE_THAN_PARENT


def test_the_sharpe_floor_is_untouched_by_the_search_penalty() -> None:
    """Sharpe measures the market plus the strategy. On a window where the
    benchmark itself returned 0.772, inflating that floor would penalise a
    lineage for the market's behaviour rather than for its own search."""

    result = _verdict(base_sharpe=0.9, information_ratio=2.0, attempts=20)

    assert result.reason != REASON_BELOW_ADJUSTED_IR_FLOOR


def test_an_unmeasured_information_ratio_is_not_penalised_into_a_hold() -> None:
    """`None` means not measured. Inventing a failure from an absent number
    would block promotion on missing data rather than on evidence."""

    result = _verdict(information_ratio=None, attempts=20)

    assert result.verdict == VERDICT_PROMOTE


# --- the case this was built for ---------------------------------------------


def test_a_search_that_varies_one_idea_until_something_passes_is_caught() -> None:
    """Six variants of one hypothesis, each a little luckier than the last, with
    the sixth finally clearing the unadjusted floor. Without the adjustment the
    firm promotes the best of six draws and calls it edge.

    Note these are all *first-attempt* comparisons against a fixed 0.5: the
    point is that the same 0.58 means something different as attempt 6 than it
    would as attempt 1.
    """

    lucky_sixth = 0.58

    as_a_first_try = _verdict(information_ratio=lucky_sixth, attempts=1)
    as_the_sixth_try = _verdict(information_ratio=lucky_sixth, attempts=6)

    assert as_a_first_try.verdict == VERDICT_PROMOTE
    assert as_the_sixth_try.verdict == VERDICT_HOLD


def test_twenty_honest_experiments_are_not_one_twenty_deep_search() -> None:
    """Attempts are per LINEAGE. Twenty unrelated strategies tested once each is
    twenty experiments; one idea revised twenty times is a search over one
    hypothesis. This test documents the distinction the caller must preserve —
    `attempts` comes from `registry.attempts(strategy_id)`, which counts the
    lineage, not the firm.
    """

    unrelated = _verdict(information_ratio=0.55, attempts=1)
    deep_search = _verdict(information_ratio=0.55, attempts=20)

    assert unrelated.verdict == VERDICT_PROMOTE
    assert deep_search.verdict == VERDICT_HOLD


@pytest.mark.parametrize("attempts", [1, 2, 5, 10, 20])
def test_a_strategy_far_above_any_adjusted_bar_always_promotes(attempts: int) -> None:
    """A genuinely strong result is not blocked by having been looked for."""

    result = _verdict(information_ratio=3.0, attempts=attempts)

    assert result.verdict == VERDICT_PROMOTE


# --- a draw is not a row ------------------------------------------------------
#
# Mike ruled 2026-07-30 that killed attempts still count against the survivor.
# The argument: a killed draw is still a draw, excluding failures makes a larger
# search cheaper (inverting the rule), and repeated failures are evidence ABOUT
# the survivor rather than noise around it.
#
# The refinement he agreed to: a STRUCTURAL death took no draw. A revision that
# died on `insufficient-trades` or a dead anchor never sampled the hypothesis —
# the plumbing failed before the question was asked — so charging the survivor
# for it penalises a data problem rather than a search.


def test_the_edge_reason_set_is_shared_with_the_ledger() -> None:
    """One taxonomy, two readers. If they drift, the gate and the corpus view
    disagree about what counts as having learned something."""

    from shrap.research.ledger import EDGE_REASONS
    from shrap.research.strategy_evaluator.store import SELECT_DRAW_COUNT_SQL

    assert "reason = ANY" in SELECT_DRAW_COUNT_SQL
    assert "worse-than-parent" in EDGE_REASONS
    assert "no-active-edge" in EDGE_REASONS
    assert "insufficient-trades" not in EDGE_REASONS
    assert "anchor-not-live" not in EDGE_REASONS


def test_the_query_requires_the_engine_to_have_run() -> None:
    """`total_trades > 0` is the second half of "took a draw". A verdict that
    looks like a finding but ran no computation is the confident-looking null
    result recorded in shrap-first-verdict."""

    from shrap.research.strategy_evaluator.store import SELECT_DRAW_COUNT_SQL

    assert "total_trades > 0" in SELECT_DRAW_COUNT_SQL


def test_re_evaluating_one_strategy_is_not_a_second_draw() -> None:
    """DISTINCT strategy_id. Without it, re-running an evaluation would raise
    the firm's own promote bar — so measuring again would make promotion
    harder, which is absurd."""

    from shrap.research.strategy_evaluator.store import SELECT_DRAW_COUNT_SQL

    assert "count(DISTINCT strategy_id)" in SELECT_DRAW_COUNT_SQL


def test_the_momentum_lineage_is_unchanged_by_the_refinement() -> None:
    """Both momentum revisions died of EDGE reasons — `worse-than-parent` and
    `no-edge` — so both took draws and the survivor still faces attempt 3.

    That the refinement changes nothing in the case that prompted it is the
    point: it is principled rather than convenient.
    """

    from shrap.research.ledger import EDGE_REASONS

    assert {"worse-than-parent", "no-edge"} <= EDGE_REASONS
    assert round(required_information_ratio(0.5, 3), 2) == 0.72


def test_a_revision_of_a_structurally_dead_parent_starts_at_attempt_one() -> None:
    """The refinement's whole purpose. The three probe strategies all died on
    `insufficient-trades`; a revision of one of them has learned nothing from
    it and should not pay for it."""

    from shrap.research.ledger import STRUCTURAL_REASONS

    assert "insufficient-trades" in STRUCTURAL_REASONS
    assert required_information_ratio(0.5, 1) == 0.5
