"""Order-intent helpers for early paper-trading smoke tests."""

from __future__ import annotations

from typing import Any

VALID_SIDES = {"buy", "sell"}


def build_handcrafted_intent(
    ticker: str,
    side: str,
    quantity: int,
    strategy_id: str,
    justification: str,
) -> dict[str, Any]:
    """Build a paper-only order intent from a manual smoke signal.

    This does not submit an order. It creates the payload that will move through
    Decision Maker -> Risk Officer -> Execution in later slices.
    """
    normalized_side = side.strip().lower()
    if normalized_side not in VALID_SIDES:
        raise ValueError(f"side must be one of {sorted(VALID_SIDES)}")
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        raise ValueError("ticker is required")
    if not strategy_id.strip():
        raise ValueError("strategy_id is required")
    if "why this might be wrong" not in justification.lower():
        raise ValueError("justification must include 'why this might be wrong'")

    return {
        "source": "handcrafted",
        "ticker": normalized_ticker,
        "side": normalized_side,
        "quantity": quantity,
        "strategy_ids": [strategy_id.strip()],
        "urgency": "normal",
        "mode": "paper",
        "justification_text": justification.strip(),
    }
