"""Realistic transaction-cost model for the walk-forward engine.

Four components, per the Evaluator spec's Processing step 3:

- **commission** — flat bps on traded notional.
- **half-spread** — bps on traded notional (crossing half the quoted spread).
- **slippage scaled by ADV participation** — bps that grow linearly with the
  fraction of average daily dollar-volume the trade represents. Bigger size in a
  thinner name costs more, which is the honest direction.
- **borrow cost for shorts** — a flat, configurable annual rate accrued daily on
  the short leg. The spec's open question flags that no clean retail borrow feed
  exists; this card uses a flat rate (deferred: a real borrow feed).

Costs are expressed as a fraction of the normalized book (equity starts at
1.0), so they compose directly with per-period returns. Everything here is a
pure function of the trade and the pre-trade ADV — deterministic and replayable.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

_BPS = 1.0e-4

# Conservative defaults. commission-free venues still leave spread + impact;
# the bias is to model friction the strategy must overcome, not to flatter it.
DEFAULT_COMMISSION_BPS = 0.5
DEFAULT_HALF_SPREAD_BPS = 2.0
DEFAULT_SLIPPAGE_BPS_PER_ADV = 10.0
DEFAULT_BORROW_RATE_ANNUAL = 0.03
DEFAULT_ADV_WINDOW = 20
DEFAULT_CAPITAL = 100_000.0
TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True, slots=True)
class CostModel:
    """Deterministic cost model. All bps are per unit of traded notional."""

    commission_bps: float = DEFAULT_COMMISSION_BPS
    half_spread_bps: float = DEFAULT_HALF_SPREAD_BPS
    slippage_bps_per_adv: float = DEFAULT_SLIPPAGE_BPS_PER_ADV
    borrow_rate_annual: float = DEFAULT_BORROW_RATE_ANNUAL
    adv_window: int = DEFAULT_ADV_WINDOW
    capital: float = DEFAULT_CAPITAL

    def stressed(self, cost_multiplier: float) -> CostModel:
        """Return a copy with the three friction bps and borrow scaled up.

        Used by the realistic-friction stress test (default ``+50%`` → 1.5).
        """

        return replace(
            self,
            commission_bps=self.commission_bps * cost_multiplier,
            half_spread_bps=self.half_spread_bps * cost_multiplier,
            slippage_bps_per_adv=self.slippage_bps_per_adv * cost_multiplier,
            borrow_rate_annual=self.borrow_rate_annual * cost_multiplier,
        )

    def trade_cost_fraction(self, delta_weight: float, adv_dollar: float) -> float:
        """Cost of one rebalance leg as a fraction of the book.

        ``delta_weight`` is the signed change in a ticker's target weight;
        ``adv_dollar`` is the pre-trade average daily dollar volume. A
        non-positive ADV (illiquid / missing) is treated as full participation
        — the maximum slippage penalty — which fails closed.
        """

        turnover = abs(delta_weight)
        if turnover == 0.0:
            return 0.0
        notional = turnover * self.capital
        participation = 1.0 if adv_dollar <= 0.0 else min(notional / adv_dollar, 1.0)
        cost_bps = (
            self.commission_bps + self.half_spread_bps + self.slippage_bps_per_adv * participation
        )
        return cost_bps * _BPS * turnover

    def borrow_cost_fraction(self, weight: float) -> float:
        """One day of borrow on a (possibly short) weight, as a book fraction.

        Only the short side accrues borrow; a long or flat weight costs nothing.
        """

        short = max(-weight, 0.0)
        if short == 0.0:
            return 0.0
        return short * self.borrow_rate_annual / TRADING_DAYS_PER_YEAR


__all__ = [
    "DEFAULT_ADV_WINDOW",
    "DEFAULT_BORROW_RATE_ANNUAL",
    "DEFAULT_CAPITAL",
    "DEFAULT_COMMISSION_BPS",
    "DEFAULT_HALF_SPREAD_BPS",
    "DEFAULT_SLIPPAGE_BPS_PER_ADV",
    "TRADING_DAYS_PER_YEAR",
    "CostModel",
]
