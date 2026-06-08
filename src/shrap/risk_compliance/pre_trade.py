"""Deterministic pre-trade checker skeleton.

This is the first risk gate for the paper-only sprint. It is intentionally small:
real-money is rejected by code, ticker eligibility is enforced, and early paper smoke
orders can be capped to a maximum quantity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

REAL_MONEY_FORBIDDEN_REASON = "REAL_MONEY_FORBIDDEN_DURING_SPRINT"


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """Initial Month-1 pre-trade policy."""

    allowed_universe: set[str]
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
    requested_quantity: int
    approved_quantity: int = 0
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
    def _parse_requested_quantity(raw_quantity: Any) -> tuple[int, str | None]:
        """Parse quantity strictly; fractional values are vetoed, not rounded.

        The risk gate should be conservative. A fractional share/order quantity
        is rejected as malformed in this Month 1 stub instead of being silently
        floored or rounded.
        """

        if isinstance(raw_quantity, float) and not raw_quantity.is_integer():
            return 0, f"quantity is not a parseable integer: got {raw_quantity!r}"
        try:
            return int(raw_quantity), None
        except (TypeError, ValueError):
            return 0, f"quantity is not a parseable integer: got {raw_quantity!r}"

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

        if ticker not in self._policy.allowed_universe:
            return PreTradeDecision(
                approved=False,
                reason_code="TICKER_NOT_IN_UNIVERSE",
                ticker=ticker,
                requested_quantity=requested_quantity,
                reasons=[f"{ticker} is not in the approved paper universe."],
            )

        if requested_quantity <= 0:
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
