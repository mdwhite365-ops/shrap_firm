"""Deterministic pre-trade checker skeleton.

This is the first risk gate for the paper-only sprint. It is intentionally small:
real-money is rejected by code, ticker eligibility is enforced, and early paper smoke
orders can be capped to a maximum quantity.

**Quantities are fractional and have been since #195.** This module used to
reject a fractional quantity as malformed, and that was the right call when it
was written: every order was a whole number of shares, so a fraction arriving
here meant something upstream was broken.

#195 made it routine. The Risk Officer scales every order by
``stage_fraction x regime_multiplier`` (0.1875 today), so a one-share intent is
*supposed* to arrive as 0.1875. This gate went on rejecting them, and because it
runs upstream of the Risk Officer, none of the fractional arithmetic below it
ever ran. The visible symptom was that no position smaller than one share could
ever be closed: the Runner emitted the exit, this gate refused it as
``INVALID_QUANTITY`` with a recorded quantity of 0, and the same thing happened
again the next session — 52 times before anyone looked (KI-033).

The lesson is in CLAUDE.md and was written before this instance: *when a card
changes a type or inserts a stage, ask what downstream already declared about
what reaches it.* This module had declared, in code and in a docstring, that a
quantity is a whole number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

REAL_MONEY_FORBIDDEN_REASON = "REAL_MONEY_FORBIDDEN_DURING_SPRINT"


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """Initial Month-1 pre-trade policy."""

    allowed_universe: set[str]
    universe_check_enabled: bool = True
    max_quantity_per_order: int = 1
    kill_switch_active: bool = False

    def __post_init__(self) -> None:
        if self.max_quantity_per_order <= 0:
            raise ValueError("max_quantity_per_order must be positive")
        object.__setattr__(self, "allowed_universe", {t.upper() for t in self.allowed_universe})


@dataclass(frozen=True, slots=True)
class PreTradeDecision:
    """Result of checking one order intent."""

    approved: bool
    reason_code: str
    ticker: str
    requested_quantity: float
    approved_quantity: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def to_event_payload(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "reason_code": self.reason_code,
            "ticker": self.ticker,
            "requested_quantity": self.requested_quantity,
            "approved_quantity": self.approved_quantity,
            "reasons": self.reasons,
        }


class PreTradeChecker:
    """Pure deterministic pre-trade checker."""

    def __init__(self, policy: RiskPolicy) -> None:
        self._policy = policy

    @staticmethod
    def _parse_requested_quantity(raw_quantity: Any) -> tuple[float, str | None]:
        """Parse quantity as a real number; a fraction is legal, not malformed.

        Strictness moved rather than disappeared. What is rejected is a value
        that is not a number at all, or one that cannot describe a position —
        ``NaN`` and the infinities pass ``float()`` happily and would sail
        through every comparison below, so they are named here rather than left
        to be caught by an inequality that silently answers ``False``.

        A bool is not a quantity. ``float(True)`` is ``1.0``, which would make a
        malformed intent look like a one-share order rather than an error.
        """

        if isinstance(raw_quantity, bool):
            return 0.0, f"quantity is not a number: got {raw_quantity!r}"
        try:
            quantity = float(raw_quantity)
        except (TypeError, ValueError):
            return 0.0, f"quantity is not a number: got {raw_quantity!r}"
        if not math.isfinite(quantity):
            return 0.0, f"quantity is not a finite number: got {raw_quantity!r}"
        return quantity, None

    def check(self, intent: dict[str, Any]) -> PreTradeDecision:
        ticker = str(intent.get("ticker", "")).upper()
        requested_quantity, quantity_error = self._parse_requested_quantity(
            intent.get("quantity", 0)
        )
        if quantity_error is not None:
            return PreTradeDecision(
                approved=False,
                reason_code="INVALID_QUANTITY",
                ticker=ticker,
                requested_quantity=requested_quantity,
                reasons=[quantity_error],
            )

        if intent.get("mode") != "paper":
            return PreTradeDecision(
                approved=False,
                reason_code=REAL_MONEY_FORBIDDEN_REASON,
                ticker=ticker,
                requested_quantity=requested_quantity,
                reasons=["Sprint invariant: only paper trading is allowed."],
            )

        if self._policy.kill_switch_active:
            return PreTradeDecision(
                approved=False,
                reason_code="KILL_SWITCH_ACTIVE",
                ticker=ticker,
                requested_quantity=requested_quantity,
                reasons=["Risk policy kill switch is active."],
            )

        if self._policy.universe_check_enabled and ticker not in self._policy.allowed_universe:
            return PreTradeDecision(
                approved=False,
                reason_code="TICKER_NOT_IN_UNIVERSE",
                ticker=ticker,
                requested_quantity=requested_quantity,
                reasons=[f"{ticker} is not in the approved paper universe."],
            )

        if requested_quantity <= 0.0:
            return PreTradeDecision(
                approved=False,
                reason_code="INVALID_QUANTITY",
                ticker=ticker,
                requested_quantity=requested_quantity,
                reasons=["Requested quantity must be positive."],
            )

        approved_quantity = min(requested_quantity, self._policy.max_quantity_per_order)
        if approved_quantity < requested_quantity:
            return PreTradeDecision(
                approved=True,
                reason_code="SCALED_DOWN_MAX_QUANTITY",
                ticker=ticker,
                requested_quantity=requested_quantity,
                approved_quantity=approved_quantity,
                reasons=["Requested quantity exceeded max_quantity_per_order."],
            )

        return PreTradeDecision(
            approved=True,
            reason_code="APPROVED",
            ticker=ticker,
            requested_quantity=requested_quantity,
            approved_quantity=approved_quantity,
            reasons=[],
        )
