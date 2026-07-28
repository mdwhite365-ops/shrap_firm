"""Notional position sizing — make the live book the book that was evaluated.

Until now the Runner emitted a **fixed one share** per signal
(``DEFAULT_QUANTITY = 1``) and never read account equity at all. A strategy's
``target_weights`` were used only as an in/out flag; the weights themselves were
discarded.

That is a backtest/live mismatch rather than a rough approximation. The Evaluator
measures a strategy at its declared weights — equal-weight top ten, say — and the
Runner would trade one share of each. On a $10,000 account one share of a $750
ETF is **7.5%** of the book while one share of a $50 name is **0.5%**, so a
strategy evaluated as equal-weight would trade as anything but. Every fill would
accumulate under a P&L record that corresponds to no tested strategy.

This module converts a target weight into a share count:

    target_weight x account_equity = notional slot
    notional slot / price          = shares, floored

Four decisions worth stating, because each has a quieter alternative that would
have been wrong:

**Floor, never round.** Rounding up overshoots the slot and, on a small account,
a single overshoot can push gross exposure past 100%. Flooring under-fills
instead, which is the direction that cannot breach a limit.

**A slot smaller than one share yields zero, and that is reported.** At $10,000
across ten names a slot is $1,000, so any name above $1,000/share cannot be held
at all. Returning 0 silently would let a strategy quietly stop expressing part of
its universe; :class:`SizingResult` carries the reason so the caller can log it.

**Missing or stale equity refuses, it does not fall back.** Falling back to a
fixed quantity would resurrect exactly the bug this module exists to remove, and
would do it invisibly at the moment the account state is least trustworthy. This
mirrors the Pre-Trade Checker's Tier-3 rule: unavailable state fails closed and
is never cached.

**Equity is read from ``ops.account_snapshots``, not from the broker.** The
Reconciliation Agent already fetches and persists it every pass, and ADR-0003
keeps broker credentials inside broker-facing containers only. The Runner is not
one of those and must not become one to learn its own account size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

# A snapshot older than this is not the account's current equity. The
# Reconciliation Agent writes one every ~300s, so 30 minutes tolerates several
# missed passes without tolerating a stale day.
DEFAULT_MAX_EQUITY_AGE = timedelta(minutes=30)


class SizingRefused(Exception):
    """Sizing cannot be computed, so no order may be emitted. Fail closed."""


@dataclass(frozen=True, slots=True)
class SizingResult:
    """A share count plus enough context to explain it in a log line."""

    quantity: int
    notional_slot: float
    price: float
    target_weight: float
    equity: float
    reason: str = ""
    """Non-empty when ``quantity`` is 0 for a reason worth surfacing."""

    @property
    def is_tradeable(self) -> bool:
        return self.quantity > 0


def assert_equity_usable(
    equity: float | None,
    observed_at: datetime | None,
    now: datetime,
    *,
    max_age: timedelta = DEFAULT_MAX_EQUITY_AGE,
) -> float:
    """Return usable equity, or refuse. Never returns a default.

    A caller that receives :class:`SizingRefused` must emit no orders. That is
    the point: trading on an unknown account size is worse than not trading.
    """

    if equity is None or observed_at is None:
        raise SizingRefused(
            "no account snapshot available in ops.account_snapshots — the "
            "Reconciliation Agent writes one every pass, so this means it has "
            "never run or cannot reach the broker. Refusing to size."
        )
    if equity <= 0.0:
        raise SizingRefused(f"account equity is {equity}, which cannot fund a position")
    age = now - observed_at
    if age > max_age:
        raise SizingRefused(
            f"account snapshot is {age} old (limit {max_age}) — equity this stale is "
            "not the account's current size. Refusing to size rather than guessing."
        )
    return equity


def size_position(
    *,
    target_weight: float,
    equity: float,
    price: float,
    max_quantity: int | None = None,
) -> SizingResult:
    """Convert a target weight into a floored share count.

    ``max_quantity`` mirrors the Pre-Trade Checker's per-order cap. Applying it
    here as well is deliberate duplication: the checker would veto the excess
    anyway, and a vetoed order is a strategy silently failing to reach its
    target rather than an error anyone sees.
    """

    if price <= 0.0:
        raise SizingRefused(f"price {price} is not usable for sizing")
    if target_weight <= 0.0:
        return SizingResult(
            quantity=0,
            notional_slot=0.0,
            price=price,
            target_weight=target_weight,
            equity=equity,
            reason="target weight is zero or negative — this is an exit, not an entry",
        )

    notional = target_weight * equity
    # Floor: overshooting the slot can breach gross exposure on a small account,
    # and under-filling is the direction that cannot breach a limit.
    quantity = math.floor(notional / price)

    if quantity < 1:
        return SizingResult(
            quantity=0,
            notional_slot=notional,
            price=price,
            target_weight=target_weight,
            equity=equity,
            reason=(
                f"slot ${notional:,.2f} is smaller than one share at ${price:,.2f} — "
                f"this name cannot be held at this account size and weight"
            ),
        )

    capped = ""
    if max_quantity is not None and quantity > max_quantity:
        capped = (
            f"clamped {quantity} -> {max_quantity} by the per-order cap; the position "
            f"will under-fill its ${notional:,.2f} slot"
        )
        quantity = max_quantity

    return SizingResult(
        quantity=quantity,
        notional_slot=notional,
        price=price,
        target_weight=target_weight,
        equity=equity,
        reason=capped,
    )


__all__ = [
    "DEFAULT_MAX_EQUITY_AGE",
    "SizingRefused",
    "SizingResult",
    "assert_equity_usable",
    "size_position",
]
