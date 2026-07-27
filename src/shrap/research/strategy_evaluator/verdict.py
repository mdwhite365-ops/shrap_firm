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

The mapping takes ``anchor_fresh`` as an explicit input so it is a total,
side-effect-free function; the pipeline still short-circuits a dead anchor
before it ever runs the engine, but the verdict function does not assume that.
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
) -> Verdict:
    """Map measured metrics to a verdict. Pure; deterministic; no tuning.

    Priority: dead anchor and the trade-count gate kill first (regardless of
    headline metrics), then absence of edge, then failure to survive friction,
    then the sub-floor hold, then promote.
    """

    if not anchor_fresh:
        return Verdict(VERDICT_KILL, REASON_ANCHOR_NOT_LIVE)
    if total_trades < min_trades:
        return Verdict(VERDICT_KILL, REASON_INSUFFICIENT_TRADES)
    if base_sharpe <= 0.0:
        return Verdict(VERDICT_KILL, REASON_NO_EDGE)
    if stress_sharpe <= 0.0:
        return Verdict(VERDICT_KILL, REASON_FAILS_FRICTION_STRESS)
    if base_sharpe < sharpe_floor:
        return Verdict(VERDICT_HOLD, REASON_BELOW_SHARPE_FLOOR)
    return Verdict(VERDICT_PROMOTE, REASON_PROMOTE)


__all__ = [
    "REASON_ANCHOR_NOT_LIVE",
    "REASON_BELOW_SHARPE_FLOOR",
    "REASON_FAILS_FRICTION_STRESS",
    "REASON_INSUFFICIENT_DATA",
    "REASON_INSUFFICIENT_TRADES",
    "REASON_NO_EDGE",
    "REASON_PROMOTE",
    "VERDICT_HOLD",
    "VERDICT_KILL",
    "VERDICT_PROMOTE",
    "Verdict",
    "map_verdict",
]
