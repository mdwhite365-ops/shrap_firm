"""Per-strategy sizing — spec step 3, with the Kelly input it does not have.

The spec asks for ``Kelly fraction x posterior edge x regime-fit multiplier``,
with the fraction at 25% by default. Two of those three factors exist. The
posterior does not: there is no Bayesian Updater in this firm — no service, no
table, no rows — and the spec explicitly forbids the obvious substitution
("Kelly inputs come from the Bayesian Updater's posterior, not from raw backtest
Sharpe").

Substituting backtest Sharpe would produce a number that looks like Kelly, is
not Kelly, and would size real positions. So this module implements the spec's
own documented fallback for exactly this case — open question 4, "Kelly inputs
when posterior is thin: fall back to flat fraction, currently yes, at the lowest
tier" — and leaves the multiplier visible and unset.

:class:`SizingDecision.kelly_posterior` is that empty slot. It is always ``None``
today. When a Bayesian Updater exists it populates that field and
:func:`size_intent` starts multiplying by it; nothing else changes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shrap.risk_compliance.risk_officer.limits import stage_fraction


@dataclass(frozen=True, slots=True)
class SizingDecision:
    """A scaled quantity plus the factors that produced it."""

    requested_quantity: int
    approved_quantity: int
    stage: str
    stage_fraction: float
    regime_multiplier: float
    kelly_posterior: float | None = None
    """Always ``None`` until a Bayesian Updater exists. See the module docstring."""

    @property
    def was_scaled(self) -> bool:
        return self.approved_quantity < self.requested_quantity

    def to_payload(self) -> dict[str, object]:
        return {
            "requested_quantity": self.requested_quantity,
            "approved_quantity": self.approved_quantity,
            "stage": self.stage,
            "stage_fraction": self.stage_fraction,
            "regime_multiplier": self.regime_multiplier,
            "kelly_posterior": self.kelly_posterior,
        }


def size_intent(
    *,
    requested_quantity: int,
    stage: str | None,
    regime_multiplier: float = 1.0,
) -> SizingDecision:
    """Scale a requested quantity down to what the stage and regime permit.

    Floors, never rounds — the same reasoning as the Runner's notional sizing:
    rounding up overshoots a limit, and the direction that cannot breach is
    down. A fraction that floors to zero yields zero, which the caller reports
    as a veto rather than sending an empty order.
    """

    fraction = stage_fraction(stage)
    scale = fraction * max(0.0, min(regime_multiplier, 1.0))
    approved = math.floor(requested_quantity * scale) if requested_quantity > 0 else 0
    return SizingDecision(
        requested_quantity=requested_quantity,
        approved_quantity=max(0, min(approved, requested_quantity)),
        stage=(stage or "unknown"),
        stage_fraction=fraction,
        regime_multiplier=regime_multiplier,
        kelly_posterior=None,
    )


__all__ = ["SizingDecision", "size_intent"]
