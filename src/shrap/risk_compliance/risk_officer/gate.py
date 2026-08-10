"""The portfolio check on the order path — spec steps 4, 5 and 6.

Runs after the deterministic policy check and the existing stateful gates, and
like them it can only ever tighten. Three limits apply, all measured on the book
**as it would stand if the order filled**:

- per-ticker weight
- gross and net exposure
- correlation-cluster weight

Two rules govern how a breach is handled, and both matter more than the limits
themselves.

**Scale down, do not reject.** The spec is explicit at step 8: "If approved at
less than the requested size, the intent is scaled down, not rejected." So a
breach computes the largest quantity that *does* fit and approves that. Only a
book with no room for a single share produces a veto.

**A trade may never make a breached limit worse — but it may make it better.**
This is not in the spec and has to be, because of regime scaling. When the
regime moves from ``late-cycle-melt-up`` (caps x0.75) to ``wartime`` (x0.25),
every existing position is instantly over its cap through no action of the
strategy's. A gate that vetoed on "projected exceeds cap" would then refuse the
sells that bring the book back into compliance, trapping the firm in an
over-limit book precisely when the market turned. So the test is directional:
a limit already in breach blocks only trades that increase the breach.

Prices come from ``market_data.daily_bars``. The intent carries no price (see
``trading_floor/intent.py``), and exposure arithmetic needs one.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from shrap.risk_compliance.risk_officer.clusters import Cluster, cluster_positions
from shrap.risk_compliance.risk_officer.exposure import BookExposure
from shrap.risk_compliance.risk_officer.limits import PortfolioLimits

REASON_EXCEEDS_TICKER_CAP = "EXCEEDS_TICKER_CAP"
REASON_EXCEEDS_GROSS_EXPOSURE = "EXCEEDS_GROSS_EXPOSURE"
REASON_EXCEEDS_NET_EXPOSURE = "EXCEEDS_NET_EXPOSURE"
REASON_EXCEEDS_CLUSTER_CAP = "EXCEEDS_CLUSTER_CAP"
REASON_NO_PRICE = "NO_PRICE_FOR_EXPOSURE"
REASON_SCALED_DOWN = "SCALED_DOWN_PORTFOLIO_LIMIT"

# Bisection over a continuous quantity. 40 halvings resolve any realistic order
# to far below a cent's worth of stock, and the loop is bounded rather than
# convergence-tested so it cannot spin on a pathological limit function.
BISECTION_STEPS = 40

# How far above the fitting size to probe when naming the binding limit. Small
# enough not to skip a limit, large enough to actually cross one.
BISECTION_PROBE = 1e-6

# Alpaca accepts nine decimal places on a fractional quantity, so that is the
# finest real size and anything beyond it is bisection noise.
QUANTITY_PRECISION = 9
_QUANTUM: float = float(10**QUANTITY_PRECISION)


def quantize_down(quantity: float) -> float:
    """Snap a quantity down to the broker's precision.

    Down, never nearest: the bisection converges from both sides and can settle
    a hair *over* a limit it was meant to respect, and rounding up would ship
    that hair to the venue.

    It also restores the veto. Bisecting a book with no room lands on something
    like 1.8e-11 rather than a clean zero, which reads as approved and would
    submit an order for a hundred-billionth of a share. Snapping down makes it
    the zero it means.
    """

    return math.floor(quantity * _QUANTUM) / _QUANTUM


BUY = "buy"
SELL = "sell"


@dataclass(frozen=True, slots=True)
class PortfolioDecision:
    """The portfolio layer's verdict on one intent."""

    approved: bool
    approved_quantity: float
    reason_code: str
    notes: list[str] = field(default_factory=list)
    binding_limit: str | None = None
    cluster: tuple[str, ...] | None = None

    @property
    def was_scaled(self) -> bool:
        return self.approved and self.reason_code == REASON_SCALED_DOWN

    def to_payload(self) -> dict[str, object]:
        return {
            "approved": self.approved,
            "approved_quantity": self.approved_quantity,
            "reason_code": self.reason_code,
            "notes": list(self.notes),
            "binding_limit": self.binding_limit,
            "cluster": list(self.cluster) if self.cluster else None,
        }


def _signed_delta(side: str, quantity: float, price: float) -> float:
    """Market-value change of the position, signed by direction."""

    magnitude = quantity * price
    return -magnitude if side.strip().lower() == SELL else magnitude


def _breaches(
    book: BookExposure,
    ticker: str,
    limits: PortfolioLimits,
    price_history: Mapping[str, Sequence[float]],
) -> dict[str, float]:
    """Every limit's current reading on ``book``, keyed by limit name."""

    readings = {
        REASON_EXCEEDS_TICKER_CAP: abs(book.weight(ticker)),
        REASON_EXCEEDS_GROSS_EXPOSURE: book.gross,
        REASON_EXCEEDS_NET_EXPOSURE: abs(book.net),
    }
    weights = {t: value / book.nav for t, value in book.by_ticker.items()}
    clusters = cluster_positions(
        weights,
        price_history,
        threshold=limits.correlation_threshold,
        min_history=limits.min_cluster_history,
    )
    readings[REASON_EXCEEDS_CLUSTER_CAP] = _cluster_weight_for(clusters, ticker)
    return readings


def _cluster_weight_for(clusters: Sequence[Cluster], ticker: str) -> float:
    key = ticker.strip().upper()
    for cluster in clusters:
        if key in cluster.tickers:
            return cluster.weight
    return 0.0


def _cluster_members_for(clusters: Sequence[Cluster], ticker: str) -> tuple[str, ...] | None:
    key = ticker.strip().upper()
    for cluster in clusters:
        if key in cluster.tickers:
            return cluster.tickers
    return None


def _caps(limits: PortfolioLimits) -> dict[str, float]:
    return {
        REASON_EXCEEDS_TICKER_CAP: limits.max_ticker_weight,
        REASON_EXCEEDS_GROSS_EXPOSURE: limits.max_gross_exposure,
        REASON_EXCEEDS_NET_EXPOSURE: limits.max_net_exposure,
        REASON_EXCEEDS_CLUSTER_CAP: limits.max_cluster_weight,
    }


def _violation(
    book: BookExposure,
    ticker: str,
    limits: PortfolioLimits,
    price_history: Mapping[str, Sequence[float]],
    baseline: Mapping[str, float],
) -> tuple[str, float, float] | None:
    """The first limit this book breaches *and worsens*, or ``None``.

    ``baseline`` is the same set of readings taken before the trade. A limit
    already over its cap is only a violation when the projected reading is
    higher than the baseline — see the module docstring on regime tightening.
    """

    readings = _breaches(book, ticker, limits, price_history)
    caps = _caps(limits)
    for name, reading in readings.items():
        cap = caps[name]
        if reading <= cap + 1e-12:
            continue
        if reading <= baseline.get(name, 0.0) + 1e-12:
            # Already breached before this trade and not made worse by it.
            continue
        return name, reading, cap
    return None


def check_portfolio(
    *,
    book: BookExposure,
    ticker: str,
    side: str,
    quantity: float,
    price: float | None,
    limits: PortfolioLimits,
    price_history: Mapping[str, Sequence[float]] | None = None,
) -> PortfolioDecision:
    """Approve, scale, or veto one intent against the portfolio limits.

    ``price`` of ``None`` is a veto, not a pass. Exposure cannot be computed
    without it and a risk gate that waves through what it cannot measure is not
    a risk gate.
    """

    symbol = ticker.strip().upper()
    history = price_history or {}
    if quantity <= 0:
        return PortfolioDecision(
            approved=False,
            approved_quantity=0,
            reason_code=REASON_NO_PRICE if price is None else "INVALID_QUANTITY",
            notes=["quantity must be positive to assess exposure"],
        )
    if price is None or price <= 0.0:
        return PortfolioDecision(
            approved=False,
            approved_quantity=0,
            reason_code=REASON_NO_PRICE,
            notes=[
                f"no usable price for {symbol} in market_data.daily_bars, so its "
                "exposure impact cannot be measured. Refusing rather than guessing."
            ],
        )

    baseline = _breaches(book, symbol, limits, history)

    def violation_at(size: float) -> tuple[str, float, float] | None:
        projected = book.projected(symbol, _signed_delta(side, size, price))
        return _violation(projected, symbol, limits, history, baseline)

    full = violation_at(quantity)
    if full is None:
        return PortfolioDecision(
            approved=True,
            approved_quantity=quantity,
            reason_code="APPROVED",
        )

    # Largest quantity that fits. Monotone in size for every limit here — each
    # reading moves one way as the trade grows — so bisection is exact to the
    # tolerance below.
    #
    # This searched over integers until fractional sizing landed. On a
    # fractional quantity an integer bisection does not merely lose precision,
    # it reinstates the floor: it can only ever return a whole share, so a
    # 0.94-share order that fits every limit would come back as zero and be
    # vetoed. That is the bug this card exists to remove, hiding one layer down.
    low, high = 0.0, quantity
    for _ in range(BISECTION_STEPS):
        mid = (low + high) / 2.0
        if violation_at(mid) is None:
            low = mid
        else:
            high = mid
    low = quantize_down(low)

    # Report the limit that actually decided the size — the one breached by the
    # first share that does NOT fit — rather than whichever limit happened to be
    # checked first at the full requested quantity. With several limits breached
    # at once these differ, and the binding one is the actionable answer.
    # Probe just above what fit, to name the limit the next increment breaches
    # rather than whichever was checked first at full size.
    name, reading, cap = violation_at(min(low + BISECTION_PROBE, quantity)) or full

    # Clusters from the PROJECTED book: a name not yet held belongs to no cluster
    # in the current one, and reporting `None` for the very cluster that blocked
    # the order would leave the veto unexplainable.
    projected = book.projected(symbol, _signed_delta(side, quantity, price))
    clusters = cluster_positions(
        {t: v / projected.nav for t, v in projected.by_ticker.items()},
        history,
        threshold=limits.correlation_threshold,
        min_history=limits.min_cluster_history,
    )
    members = _cluster_members_for(clusters, symbol) if name == REASON_EXCEEDS_CLUSTER_CAP else None
    detail = (
        f"{name}: {reading:.4f} of NAV against a cap of {cap:.4f}"
        f" (regime-scaled limits in docs/risk/policy.md)"
    )

    if low <= 0:
        return PortfolioDecision(
            approved=False,
            approved_quantity=0,
            reason_code=name,
            notes=[detail, "no room for any position in this name at this price"],
            binding_limit=name,
            cluster=members,
        )
    return PortfolioDecision(
        approved=True,
        approved_quantity=low,
        reason_code=REASON_SCALED_DOWN,
        notes=[detail, f"scaled {quantity} -> {low} to fit {name}"],
        binding_limit=name,
        cluster=members,
    )


__all__ = [
    "BUY",
    "REASON_EXCEEDS_CLUSTER_CAP",
    "REASON_EXCEEDS_GROSS_EXPOSURE",
    "REASON_EXCEEDS_NET_EXPOSURE",
    "REASON_EXCEEDS_TICKER_CAP",
    "REASON_NO_PRICE",
    "REASON_SCALED_DOWN",
    "SELL",
    "PortfolioDecision",
    "check_portfolio",
]
