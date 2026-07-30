"""The verdict mapping — a pure function of metrics, no human in the loop.

Three outcomes, in strict priority order (spec Processing steps 6, 9-10):

1. ``kill`` — the strategy fails a hard gate: too few trades, no measurable
   edge, edge that does not survive the realistic-friction stress, or a dead
   anchor. Killing dominates: *kill more aggressively than you promote*.
2. ``promote`` — every promotion condition holds: the anchor is live, trades
   clear the count gate, Sharpe clears the configured floor, and Sharpe stays
   positive under +50% costs and +1 day of execution lag.
3. ``hold-for-data`` — a real-looking but sub-floor edge that survives friction:
   not enough to promote, not zero enough to kill. Wait for more data.

``information_ratio`` is the strategy's active return over tracking error
against equal-weight buy-and-hold of its own panel. It defaults to ``None``,
meaning "not measured, do not gate on it" — the honest behaviour for a caller
that has not computed it, rather than a silent pass. Absolute Sharpe cannot
separate skill from market exposure (see ``docs/research/eval-protocol.md``
6b), so this is the gate that actually asks whether the strategy added
anything. Note the asymmetry: failing to beat the benchmark **kills**, while
beating it insufficiently only **holds**.

The mapping takes ``anchor_fresh`` as an explicit input so it is a total,
side-effect-free function; the pipeline still short-circuits a dead anchor
before it ever runs the engine, but the verdict function does not assume that.

``anchor_required`` exists because a world-changer anchor is a Framework #1
construct, not a universal one (ADR-0013). A ``technical-catalyst`` strategy is
correctly anchor-*less*, and applying the anchor gate to it would kill it
without the backtest ever running. The two inputs are kept separate rather than
collapsed into one flag so that "no anchor was required" and "the anchor was
live" stay distinguishable in the persisted evaluation row.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

VERDICT_KILL = "kill"
VERDICT_HOLD = "hold-for-data"
VERDICT_PROMOTE = "promote"

# Machine-readable kill/hold reasons (also rendered into the evaluation card).
REASON_ANCHOR_NOT_LIVE = "anchor-not-live"
REASON_INSUFFICIENT_TRADES = "insufficient-trades"
REASON_INSUFFICIENT_DATA = "insufficient-data"
REASON_NO_EDGE = "no-edge"
REASON_FAILS_FRICTION_STRESS = "fails-friction-stress"
REASON_BELOW_SHARPE_FLOOR = "below-sharpe-floor"
REASON_NO_ACTIVE_EDGE = "no-active-edge"
REASON_BELOW_INFORMATION_RATIO_FLOOR = "below-information-ratio-floor"
REASON_PROMOTE = "promote-criteria-met"
# A revision measured worse than the strategy it was revised from.
REASON_WORSE_THAN_PARENT = "worse-than-parent"
# Cleared the base information-ratio floor, but not the higher one its lineage
# has earned by burning attempts.
REASON_BELOW_ADJUSTED_IR_FLOOR = "below-multiple-testing-adjusted-floor"


def required_information_ratio(floor: float, attempts: int) -> float:
    """The information ratio this attempt must clear, given how many came before.

    ``StrategyRegistry.attempts`` has counted this since PR #141 and nothing has
    ever read it. Its own docstring says why: *"A lineage on attempt 20 that
    finally clears an information ratio of 0.5 has not found edge — it has found
    the best of twenty draws, and the gate cannot know that without this
    number."* This is the gate reading it.

    The shape is the expected maximum of ``n`` draws from the null, which grows
    like ``sqrt(2 ln n)``. Normalised so that a first attempt is unpenalised:

        required = floor * sqrt(1 + ln(attempts))

    which gives, at a base floor of 0.5:

        attempts   1     2     3     5     10    20
        required   0.50  0.65  0.72  0.81  0.91  1.00

    **Why the information ratio and not the Sharpe floor.** Sharpe measures the
    market plus the strategy; on a window where the benchmark itself returned
    0.772 a long-only equity rule inherits most of its Sharpe from beta, and
    inflating that floor would penalise a lineage for the market's behaviour.
    The information ratio is the part that is actually the strategy's, so it is
    the part a search has to pay for.

    **Attempts are per lineage, not per firm.** Twenty unrelated strategies
    tested once each is twenty honest experiments; one idea revised twenty times
    is a search over one hypothesis. `lineage_root_id` is what separates them,
    and the root is resolved from the parent row inside the registration
    transaction so a proposer cannot reset its own denominator.

    **This is a calibration and Mike owns it.** The curve above is a defensible
    default, not a derived constant — the true correction depends on how
    correlated the attempts are, and revisions of one strategy are highly
    correlated, which makes ``sqrt(2 ln n)`` conservative. Erring conservative is
    the right direction for a promote gate.
    """

    if attempts <= 1:
        return floor
    return floor * math.sqrt(1.0 + math.log(attempts))


@dataclass(frozen=True, slots=True)
class Verdict:
    """The decision plus the machine-readable reason that produced it."""

    verdict: str
    reason: str


def map_verdict(
    *,
    anchor_fresh: bool,
    total_trades: int,
    base_sharpe: float,
    stress_sharpe: float,
    min_trades: int,
    sharpe_floor: float,
    anchor_required: bool = True,
    information_ratio: float | None = None,
    information_ratio_floor: float = 0.0,
    parent_information_ratio: float | None = None,
    attempts: int = 1,
) -> Verdict:
    """Map measured metrics to a verdict. Pure; deterministic; no tuning.

    Priority: dead anchor and the trade-count gate kill first (regardless of
    headline metrics), then absence of edge, then failure to survive friction,
    then the sub-floor hold, then promote.

    ``anchor_required`` defaults to ``True`` so the anchor-bearing archetypes
    keep their existing behaviour unchanged; only an archetype whose policy
    says it carries no anchor passes ``False``.
    """

    if anchor_required and not anchor_fresh:
        return Verdict(VERDICT_KILL, REASON_ANCHOR_NOT_LIVE)
    if total_trades < min_trades:
        return Verdict(VERDICT_KILL, REASON_INSUFFICIENT_TRADES)
    if base_sharpe <= 0.0:
        return Verdict(VERDICT_KILL, REASON_NO_EDGE)
    # Losing to buy-and-hold is a kill, not a hold. A strategy that trades all
    # year to end up behind the basket it trades has been measured and found
    # actively harmful; more data will not redeem the decisions it already made.
    if information_ratio is not None and information_ratio <= 0.0:
        return Verdict(VERDICT_KILL, REASON_NO_ACTIVE_EDGE)
    # A revision exists to improve on its parent. One that does not has been
    # falsified by its own stated purpose, and keeping it alive is how a lineage
    # accumulates twenty strictly-worse attempts that all read as `hypothesis`.
    #
    # A kill rather than a hold even when the revision would otherwise clear the
    # floors: if it beat them, the parent beat them by MORE, so promoting the
    # revision instead would be strictly worse. There is nothing more data can
    # tell us about a variant already dominated by the thing it varied.
    #
    # `<=` deliberately. Matching the parent is not improving on it, and a
    # revision that changes something and achieves nothing is noise in the
    # lineage. Whether an improvement should have to clear a MARGIN rather than
    # merely be positive is a calibration; Mike owns it.
    if (
        information_ratio is not None
        and parent_information_ratio is not None
        and information_ratio <= parent_information_ratio
    ):
        return Verdict(VERDICT_KILL, REASON_WORSE_THAN_PARENT)
    if stress_sharpe <= 0.0:
        return Verdict(VERDICT_KILL, REASON_FAILS_FRICTION_STRESS)
    if base_sharpe < sharpe_floor:
        return Verdict(VERDICT_HOLD, REASON_BELOW_SHARPE_FLOOR)
    # Beat the benchmark, but not by enough to distinguish skill from luck.
    if information_ratio is not None and information_ratio < information_ratio_floor:
        return Verdict(VERDICT_HOLD, REASON_BELOW_INFORMATION_RATIO_FLOOR)
    # ...and not by enough to distinguish skill from the best of several tries.
    # Held rather than killed: unlike `worse-than-parent`, this strategy has not
    # been shown to be bad. It has been shown to be insufficiently distinguished
    # from luck GIVEN how many attempts preceded it, and more out-of-sample data
    # is exactly what would settle that.
    adjusted_floor = required_information_ratio(information_ratio_floor, attempts)
    if information_ratio is not None and information_ratio < adjusted_floor:
        return Verdict(VERDICT_HOLD, REASON_BELOW_ADJUSTED_IR_FLOOR)
    return Verdict(VERDICT_PROMOTE, REASON_PROMOTE)


__all__ = [
    "REASON_ANCHOR_NOT_LIVE",
    "REASON_BELOW_ADJUSTED_IR_FLOOR",
    "REASON_BELOW_INFORMATION_RATIO_FLOOR",
    "REASON_BELOW_SHARPE_FLOOR",
    "REASON_FAILS_FRICTION_STRESS",
    "REASON_INSUFFICIENT_DATA",
    "REASON_INSUFFICIENT_TRADES",
    "REASON_NO_ACTIVE_EDGE",
    "REASON_NO_EDGE",
    "REASON_PROMOTE",
    "REASON_WORSE_THAN_PARENT",
    "VERDICT_HOLD",
    "VERDICT_KILL",
    "VERDICT_PROMOTE",
    "Verdict",
    "map_verdict",
    "required_information_ratio",
]
