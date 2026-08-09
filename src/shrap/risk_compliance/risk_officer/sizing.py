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

from dataclasses import dataclass

from shrap.risk_compliance.risk_officer.limits import stage_fraction

# Below this the order is noise, not a position. A scaled slot worth less than a
# dollar is not worth a broker round trip, and it keeps a rounding artefact from
# reaching the book as a real fill.
MIN_TRADEABLE_NOTIONAL = 1.0


@dataclass(frozen=True, slots=True)
class SizingDecision:
    """A scaled quantity plus the factors that produced it."""

    requested_quantity: float
    approved_quantity: float
    stage: str
    stage_fraction: float
    regime_multiplier: float
    reference_price: float | None = None
    """Price the quantity was sized against, when the caller supplied one."""
    kelly_posterior: float | None = None
    """Always ``None`` until a Bayesian Updater exists. See the module docstring."""

    @property
    def was_scaled(self) -> bool:
        return self.approved_quantity < self.requested_quantity

    @property
    def approved_notional(self) -> float | None:
        if self.reference_price is None:
            return None
        return self.approved_quantity * self.reference_price

    def to_payload(self) -> dict[str, object]:
        return {
            "requested_quantity": self.requested_quantity,
            "approved_quantity": self.approved_quantity,
            "stage": self.stage,
            "stage_fraction": self.stage_fraction,
            "regime_multiplier": self.regime_multiplier,
            "reference_price": self.reference_price,
            "approved_notional": self.approved_notional,
            "kelly_posterior": self.kelly_posterior,
        }


def size_intent(
    *,
    requested_quantity: float,
    stage: str | None,
    regime_multiplier: float = 1.0,
    reference_price: float | None = None,
) -> SizingDecision:
    """Scale a requested quantity down to what the stage and regime permit.

    **This no longer floors, and that is the fix.** It used to take an ``int``
    and ``math.floor`` the product, which is where 26 of 89 live decisions died
    as ``SIZED_TO_ZERO`` — every one of them a request under six shares. A
    5-share request at 0.1875 is 0.9375 shares, and flooring that is a veto.

    Note what the old code was *not*: reordering this as "scale the notional,
    then divide by price" changes nothing, because ``(N x s) / p`` and
    ``(N / p) x s`` are the same number. The floor was doing all the damage, not
    the order of operations. The cure is to carry a fractional quantity as far
    as the broker will accept one and let the Execution Agent round only when a
    specific asset is not fractionable.

    The floor also had a directional bias worth naming. A $700 name at a $1,000
    slot requests one share, scales to 0.1875, and is vetoed; an $18 name
    requests 55, scales to 10, and trades. **The book that resulted is not the
    book the strategy was evaluated at — it is the cheap half of it.**

    ``reference_price`` is optional so callers that have no price still work;
    supplying it lets the decision report notional and lets the
    ``MIN_TRADEABLE_NOTIONAL`` guard apply.
    """

    fraction = stage_fraction(stage)
    scale = fraction * max(0.0, min(regime_multiplier, 1.0))
    approved = requested_quantity * scale if requested_quantity > 0 else 0.0
    approved = max(0.0, min(approved, requested_quantity))

    # A slot too small to be a position is zero, and the caller vetoes it. This
    # is the *only* remaining path to a zero, and it is about the order being
    # meaningless rather than about integer arithmetic.
    if reference_price is not None and 0.0 < approved * reference_price < MIN_TRADEABLE_NOTIONAL:
        approved = 0.0

    return SizingDecision(
        requested_quantity=requested_quantity,
        approved_quantity=approved,
        stage=(stage or "unknown"),
        stage_fraction=fraction,
        regime_multiplier=regime_multiplier,
        reference_price=reference_price,
        kelly_posterior=None,
    )


__all__ = ["MIN_TRADEABLE_NOTIONAL", "SizingDecision", "size_intent"]
