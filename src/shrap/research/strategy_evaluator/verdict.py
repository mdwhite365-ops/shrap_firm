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
    if stress_sharpe <= 0.0:
        return Verdict(VERDICT_KILL, REASON_FAILS_FRICTION_STRESS)
    if base_sharpe < sharpe_floor:
        return Verdict(VERDICT_HOLD, REASON_BELOW_SHARPE_FLOOR)
    # Beat the benchmark, but not by enough to distinguish skill from luck.
    if information_ratio is not None and information_ratio < information_ratio_floor:
        return Verdict(VERDICT_HOLD, REASON_BELOW_INFORMATION_RATIO_FLOOR)
    return Verdict(VERDICT_PROMOTE, REASON_PROMOTE)


__all__ = [
    "REASON_ANCHOR_NOT_LIVE",
    "REASON_BELOW_INFORMATION_RATIO_FLOOR",
    "REASON_BELOW_SHARPE_FLOOR",
    "REASON_FAILS_FRICTION_STRESS",
    "REASON_INSUFFICIENT_DATA",
    "REASON_INSUFFICIENT_TRADES",
    "REASON_NO_ACTIVE_EDGE",
    "REASON_NO_EDGE",
    "REASON_PROMOTE",
    "VERDICT_HOLD",
    "VERDICT_KILL",
    "VERDICT_PROMOTE",
    "Verdict",
    "map_verdict",
]
