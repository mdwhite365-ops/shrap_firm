"""Turn exit decisions into signals the existing order path already understands.

Pure planning, no I/O — the same split :mod:`shrap.research.strategy_runner.engine`
uses, so this is testable without a database or a broker.

**Why this rides the strategy-signal stream rather than a new one.** An exit is
an order like any other and must go through the Decision Maker, the Pre-Trade
Checker and the Risk Officer exactly as an entry does. Giving exits their own
path would be giving them their own set of gates to be missing, and the firm
already knows what that costs: KI-030 was the Runner selling positions the
account never held. The payload is built by
:func:`~shrap.research.strategy_runner.engine.build_payload`, so nothing
downstream can tell an exit from an entry.

**Attribution.** A position belongs to an account; a signal must name a
strategy. ADR-0017 makes that a 1:1 map — three accounts are three strategy
slots — so the account's own strategy is the one charged with the exit. An
account with no strategy assigned produces no exits rather than a guess.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from shrap.research.strategy_runner.engine import (
    PlannedSignal,
    RunnerSignalConfig,
    build_payload,
)
from shrap.risk_compliance.risk_officer.exits import (
    ExitDecision,
    ExitRule,
    PositionPnL,
    evaluate_exits,
)

SIDE_SELL = "sell"


@dataclass(frozen=True, slots=True)
class ExitPlan:
    """What one exit pass would do, before anything is published."""

    signals: tuple[PlannedSignal, ...]
    decisions: tuple[ExitDecision, ...]

    @property
    def is_empty(self) -> bool:
        return not self.signals


def justification(decision: ExitDecision) -> str:
    """One line an operator can read in the audit trail without the source.

    The reason code alone does not say how far past the threshold the position
    went, and that is the first thing anyone asks when an exit fires.
    """

    return (
        f"{decision.reason}: {decision.ticker} at {decision.measured_pct:+.2%} "
        f"against a threshold of {decision.threshold_pct:.2%}"
    )


def plan_exits(
    *,
    account_id: str,
    strategy_id: str | None,
    positions: Sequence[PositionPnL],
    rule: ExitRule,
    config: RunnerSignalConfig,
    regime_label: str | None = None,
    suppressed: Collection[str] = (),
) -> ExitPlan:
    """Decide which positions to close, and build the sells that close them.

    Returns an empty plan — never a partial one — when the rule is unarmed or
    the account has no strategy to attribute the order to.
    """

    if strategy_id is None or not strategy_id.strip():
        # No attribution, no order. See the module docstring.
        return ExitPlan(signals=(), decisions=())

    decisions = evaluate_exits(positions, rule, suppressed=suppressed)
    signals = tuple(
        PlannedSignal(
            strategy_id=strategy_id,
            ticker=decision.ticker,
            side=SIDE_SELL,
            payload=build_payload(
                strategy_id=strategy_id,
                ticker=decision.ticker,
                side=SIDE_SELL,
                quantity=decision.quantity,
                account_id=account_id,
                config=config,
                regime_label=regime_label,
                justification=justification(decision),
            ),
        )
        for decision in decisions
    )
    return ExitPlan(signals=signals, decisions=tuple(decisions))


def strategy_by_account(records: Sequence[object]) -> dict[str, str]:
    """Map each account to the one strategy that trades it (ADR-0017).

    An account that somehow carries two strategies keeps the first by
    ``strategy_id`` order so the choice is deterministic rather than dependent
    on row order — an exit attributed to a different strategy on each pass would
    make the audit trail unreadable.
    """

    by_account: dict[str, str] = {}
    for record in sorted(records, key=lambda r: str(getattr(r, "strategy_id", ""))):
        account = str(getattr(record, "account_id", "") or "")
        strategy = str(getattr(record, "strategy_id", "") or "")
        if account and strategy and account not in by_account:
            by_account[account] = strategy
    return by_account


def suppressed_tickers(
    emitted_at: Mapping[str, float], *, now: float, window_seconds: float
) -> set[str]:
    """Tickers whose exit is recent enough that re-emitting would double it.

    Position snapshots refresh roughly every 300s, so a sold position keeps
    reading as open for minutes after the order is on the wire. Without this the
    rule re-fires on every tick until the fill is reflected — the oversell shape
    of KI-030.

    In-process and therefore lost on restart, which is stated rather than fixed:
    the Pre-Trade Checker's own per-symbol cooldown is the backstop, and a
    duplicate after a restart is bounded by it.
    """

    return {ticker for ticker, at in emitted_at.items() if now - at < window_seconds}


__all__ = [
    "SIDE_SELL",
    "ExitPlan",
    "justification",
    "plan_exits",
    "strategy_by_account",
    "suppressed_tickers",
]
