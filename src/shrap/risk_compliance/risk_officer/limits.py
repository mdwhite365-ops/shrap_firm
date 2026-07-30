"""The limits themselves, and the regime scaling applied to them.

Numbers live in ``docs/risk/policy.md``; this module is where they become code.
The defaults here must match that document — it is the authority, and a limit
that differs between the two is a bug in this file, not in the doc.

Two decisions worth stating because the quieter alternative would be wrong:

**Regime scales caps by the LOW end of the band.** ``intel.regime.sizing-modifier``
carries a range like ``[0.75, 1.25]``. A veto authority takes the conservative
end of a range by construction; the upper half describes how large a strategy
*may wish* to size, which is the Decision Maker's argument, not the Risk
Officer's. Taking the midpoint would be a defensible trading choice and an
indefensible risk one.

**No regime event means quarter size, not full size.** Absent state is the
``unknown`` band (0.25). Defaulting to 1.0 would mean the firm runs at full size
precisely when it has lost track of what market it is in.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# --- promotion stages ---------------------------------------------------------

# The spec's Kelly fraction per promotion stage. Applied as a flat fraction
# because the posterior these were meant to multiply does not exist — see
# `docs/risk/policy.md` §Sizing and the `kelly_posterior` slot in `sizing.py`.
STAGE_FRACTIONS: dict[str, float] = {
    "paper": 0.25,
    "small-size-paper": 0.25,
    "live-paper": 0.50,
}
DEFAULT_STAGE_FRACTION = 0.25
"""An unrecognised stage sizes at the lowest tier rather than raising.

A new stage name appearing in the registry must not be able to widen sizing by
being unknown to this table.
"""

# --- regime ------------------------------------------------------------------

# Mirrors `intelligence/regime/profiles.py` sizing bands. Only the low end is
# used; the full band is kept so the source stays legible against profiles.py.
REGIME_BANDS: dict[str, tuple[float, float]] = {
    "late-cycle-melt-up": (0.75, 1.00),
    "crisis-recovery": (0.75, 1.25),
    "stagflation": (0.50, 0.75),
    "wartime": (0.25, 0.75),
    "unknown": (0.25, 0.50),
}
UNKNOWN_REGIME = "unknown"
NO_REGIME_MULTIPLIER = REGIME_BANDS[UNKNOWN_REGIME][0]


def regime_multiplier(label: str | None, band: tuple[float, float] | None = None) -> float:
    """The factor applied to every exposure cap for the current regime.

    ``band`` is the value carried on the event, preferred when present so a
    Classifier recalibration takes effect without a change here. ``label`` is
    the fallback for a malformed or bandless event.

    Never exceeds 1.0. ``crisis-recovery`` tops out at 1.25, and a multiplier
    above one would raise a limit above the policy number — these are limits,
    not targets, and no regime licenses more risk than the policy permits.
    """

    if band is not None:
        low = min(band)
        if low > 0.0:
            return min(low, 1.0)
    if label is None:
        return NO_REGIME_MULTIPLIER
    known = REGIME_BANDS.get(label.strip().lower())
    if known is None:
        return NO_REGIME_MULTIPLIER
    return min(known[0], 1.0)


# --- the limits ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PortfolioLimits:
    """Exposure limits as fractions of account NAV.

    Defaults are the v0.1 first cuts from ``docs/risk/policy.md``. Merging the
    Risk Officer card accepts them as the operating limits.
    """

    max_ticker_weight: float = 0.20
    max_gross_exposure: float = 1.00
    max_net_exposure: float = 1.00
    max_cluster_weight: float = 0.15
    max_daily_loss: float = 0.02
    max_strategy_drawdown: float = 0.25
    correlation_threshold: float = 0.80
    min_cluster_history: int = 40

    def __post_init__(self) -> None:
        for name in (
            "max_ticker_weight",
            "max_gross_exposure",
            "max_net_exposure",
            "max_cluster_weight",
            "max_daily_loss",
            "max_strategy_drawdown",
        ):
            value = getattr(self, name)
            if not 0.0 < value <= 1.0:
                raise ValueError(f"{name} must be in (0, 1], got {value}")
        if not -1.0 <= self.correlation_threshold <= 1.0:
            raise ValueError("correlation_threshold must be a correlation")
        if self.min_cluster_history < 2:
            raise ValueError("min_cluster_history must allow a correlation to be computed")

    def scaled_for_regime(self, multiplier: float) -> PortfolioLimits:
        """Tighten the exposure caps for the current regime.

        Only exposure caps scale. The daily-loss and drawdown limits do not:
        those are statements about how much of the account may be lost before
        the firm stops, and that tolerance does not change because the market
        did. Scaling them with the regime would mean a calm market permits
        larger losses, which is backwards.
        """

        if multiplier >= 1.0:
            return self
        return replace(
            self,
            max_ticker_weight=self.max_ticker_weight * multiplier,
            max_gross_exposure=self.max_gross_exposure * multiplier,
            max_net_exposure=self.max_net_exposure * multiplier,
            max_cluster_weight=self.max_cluster_weight * multiplier,
        )


def stage_fraction(stage: str | None) -> float:
    """The flat sizing fraction for a promotion stage."""

    if stage is None:
        return DEFAULT_STAGE_FRACTION
    return STAGE_FRACTIONS.get(stage.strip().lower(), DEFAULT_STAGE_FRACTION)


__all__ = [
    "DEFAULT_STAGE_FRACTION",
    "NO_REGIME_MULTIPLIER",
    "REGIME_BANDS",
    "STAGE_FRACTIONS",
    "UNKNOWN_REGIME",
    "PortfolioLimits",
    "regime_multiplier",
    "stage_fraction",
]
