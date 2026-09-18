"""Take a profit, or cut a loss. The firm could do neither.

`grep` for ``stop_loss``, ``take_profit``, ``profit_target`` or
``trailing_stop`` across the whole codebase returned **zero matches** before
this module. The only way out of a position was the next morning's
cross-sectional re-ranking at 09:30.

**Measured on the live accounts, 2026-09-17.** Every fill in the firm's history
lands between 09:30:03 and 09:30:07 ET — one burst at the open and nothing for
the rest of the session. Two paper accounts have made **$74 and $55** over
several weeks against a benchmark running a Sharpe of +1.15.

**Why re-ranking is not an exit.** These are momentum strategies. A name that
spikes +20% moves *up* the ranking, so the next morning's re-rank tends to hold
it or buy more. The one mechanism that would take the profit is the one that
does not exist — which is what this module is.

**What this module is not.** It is pure logic: positions in, decisions out, no
I/O and nothing async. It does not decide *whether* to arm the rule, and every
threshold defaults to ``None`` so that merging it changes nothing. The
thresholds themselves are uncalibrated and picking them is Mike's ruling — see
:data:`DEFAULT_TAKE_PROFIT_PCT`.

**One property to understand before arming it.** There is no cap on how many
positions may exit in a single pass. A market-wide drop that breaches the stop
on every holding liquidates the whole book at once. That is arguably what a stop
is *for*, and it is also how a stop turns a drawdown into a realised loss at the
bottom. The firm has not ruled on whether it wants a cap, so this module does
not invent one; it is recorded here because an operator arming a stop needs to
know it.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from shrap.risk_compliance.risk_officer.sizing import MIN_TRADEABLE_NOTIONAL

# The firm has NOT calibrated these. Picking them is Mike's ruling, not a
# default to be guessed.
DEFAULT_TAKE_PROFIT_PCT: float | None = None
DEFAULT_STOP_LOSS_PCT: float | None = None

REASON_TAKE_PROFIT = "take-profit"
REASON_STOP_LOSS = "stop-loss"
REASON_INTRADAY_TAKE_PROFIT = "intraday-take-profit"
REASON_INTRADAY_STOP_LOSS = "intraday-stop-loss"


@dataclass(frozen=True, slots=True)
class PositionPnL:
    """One position with its P&L, as the broker reports it."""

    ticker: str
    quantity: float
    """Signed. Negative for a short."""

    market_value: float
    """Signed, as the broker reports it."""

    unrealized_plpc: float | None
    """Fraction since entry: ``0.18`` is +18%. ``None`` when the broker did not
    report it.

    Taken from the venue rather than computed here. Reconstructing it from a
    cost basis and a price is the shape that produced five of the firm's
    trading-path defects (#192-#199): a component recomputed a fact the broker
    already recorded, and the two disagreed."""

    unrealized_intraday_plpc: float | None
    """Today's move only, as a fraction. This is the one that answers "it spiked
    and we did nothing" — a position can be flat since entry and up 12% today."""


@dataclass(frozen=True, slots=True)
class ExitRule:
    """Thresholds for exiting a position. All default to None (unarmed)."""

    take_profit_pct: float | None = None
    stop_loss_pct: float | None = None
    intraday_take_profit_pct: float | None = None
    intraday_stop_loss_pct: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "take_profit_pct",
            "stop_loss_pct",
            "intraday_take_profit_pct",
            "intraday_stop_loss_pct",
        ):
            value = getattr(self, name)
            if value is not None and value <= 0.0:
                raise ValueError(
                    f"{name} must be > 0.0, got {value}. A negative stop would "
                    "invert the rule: it would fire when the position is UP, "
                    "not down."
                )

    @property
    def is_armed(self) -> bool:
        return any(
            getattr(self, name) is not None
            for name in (
                "take_profit_pct",
                "stop_loss_pct",
                "intraday_take_profit_pct",
                "intraday_stop_loss_pct",
            )
        )


@dataclass(frozen=True, slots=True)
class ExitDecision:
    """One position to sell, with the reason and the P&L that triggered it."""

    ticker: str
    quantity: float  # POSITIVE quantity to sell (abs of the holding)
    reason: str  # one of the REASON_* constants
    measured_pct: float  # the P&L that triggered it
    threshold_pct: float


def evaluate_exits(
    positions: Sequence[PositionPnL],
    rule: ExitRule,
    *,
    suppressed: Collection[str] = (),
) -> list[ExitDecision]:
    """Evaluate all positions against the rule and return exit decisions.

    Returns at most one decision per ticker. Stop loss takes priority over take
    profit, and since-entry rules take priority over intraday ones.
    """

    # Unarmed rule returns [] immediately. This ships dark; it must be
    # impossible for it to act until thresholds are set.
    if not rule.is_armed:
        return []

    decisions: list[ExitDecision] = []
    suppressed_set = set(suppressed)

    for position in positions:
        ticker = position.ticker

        # A ticker in `suppressed` is skipped. The caller passes tickers with
        # an exit already in flight. Without it the rule re-emits the same sell
        # on every tick until the fill lands, and position snapshots refresh
        # only every ~300s.
        if ticker in suppressed_set:
            continue

        # Shorts are skipped. The broker's plpc sign convention for shorts has
        # not been verified against a real short position, and every strategy
        # in the registry is long-only, so acting on an unverified sign could
        # double a losing short instead of closing it. Failing to act is
        # recoverable; inverting a stop is not.
        if position.quantity < 0:
            continue

        # Zero quantity is skipped (nothing to sell).
        if position.quantity == 0.0:
            continue

        # Dust is skipped, reusing the Pre-Trade Checker's own floor rather
        # than a second number. The live book carries four positions at 1e-09
        # shares and one worth $0.53 — the same `U` residue that KI-033 was
        # about. Those orders are refused downstream regardless, so emitting
        # them every tick would put roughly 1,500 refusals a day in the log and
        # bury the one exit that mattered.
        if abs(position.market_value) < MIN_TRADEABLE_NOTIONAL:
            continue

        # None P&L is skipped, never treated as 0.0. A missing broker field
        # means we do not know the position's P&L; guessing zero would make
        # every threshold look un-breached and read as "checked and fine".
        if position.unrealized_plpc is None and position.unrealized_intraday_plpc is None:
            continue

        # Stop loss takes priority over take profit for the same position, and
        # the since-entry rules take priority over the intraday ones. Return at
        # most ONE decision per ticker. Two sells for one position would
        # oversell into a short.
        decision: ExitDecision | None = None

        if position.unrealized_plpc is not None:
            if rule.stop_loss_pct is not None and position.unrealized_plpc <= -rule.stop_loss_pct:
                decision = ExitDecision(
                    ticker=ticker,
                    quantity=abs(position.quantity),
                    reason=REASON_STOP_LOSS,
                    measured_pct=position.unrealized_plpc,
                    threshold_pct=rule.stop_loss_pct,
                )
            elif (
                rule.take_profit_pct is not None
                and position.unrealized_plpc >= rule.take_profit_pct
            ):
                decision = ExitDecision(
                    ticker=ticker,
                    quantity=abs(position.quantity),
                    reason=REASON_TAKE_PROFIT,
                    measured_pct=position.unrealized_plpc,
                    threshold_pct=rule.take_profit_pct,
                )

        if decision is None and position.unrealized_intraday_plpc is not None:
            if (
                rule.intraday_stop_loss_pct is not None
                and position.unrealized_intraday_plpc <= -rule.intraday_stop_loss_pct
            ):
                decision = ExitDecision(
                    ticker=ticker,
                    quantity=abs(position.quantity),
                    reason=REASON_INTRADAY_STOP_LOSS,
                    measured_pct=position.unrealized_intraday_plpc,
                    threshold_pct=rule.intraday_stop_loss_pct,
                )
            elif (
                rule.intraday_take_profit_pct is not None
                and position.unrealized_intraday_plpc >= rule.intraday_take_profit_pct
            ):
                decision = ExitDecision(
                    ticker=ticker,
                    quantity=abs(position.quantity),
                    reason=REASON_INTRADAY_TAKE_PROFIT,
                    measured_pct=position.unrealized_intraday_plpc,
                    threshold_pct=rule.intraday_take_profit_pct,
                )

        if decision is not None:
            decisions.append(decision)

    return decisions


__all__ = [
    "DEFAULT_STOP_LOSS_PCT",
    "DEFAULT_TAKE_PROFIT_PCT",
    "REASON_INTRADAY_STOP_LOSS",
    "REASON_INTRADAY_TAKE_PROFIT",
    "REASON_STOP_LOSS",
    "REASON_TAKE_PROFIT",
    "ExitDecision",
    "ExitRule",
    "PositionPnL",
    "evaluate_exits",
]
