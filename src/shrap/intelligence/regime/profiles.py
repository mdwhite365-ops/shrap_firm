"""Rule-based regime profiles and scoring.

Each profile mirrors a card in docs/regimes/. A regime fits when ALL hard
conditions pass and at least ``min_soft`` soft conditions pass (per spec:
rule-based, not learned). A condition whose feature is missing does not pass
— conservative by construction.

CALIBRATION STATUS: v0.1. Thresholds were first derived from the regime
cards' stated ranges, then adjusted once against the first live reading
(2026-07-06, persisted in intel.regime_history and encoded as a regression
test in tests/intelligence/test_regime_classifier.py). Known systematic
bias: the vol features come from Alpaca's free IEX feed, which is thinner
than SIP and reads realized vol above the SPX figures the cards were
written against — vol thresholds here sit higher than the cards' bands to
compensate. Mike owns calibration; changing a threshold is a PR against
this file referencing the regime card and the observed feature evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from shrap.intelligence.regime.features import FeatureVector

UNKNOWN_LABEL = "unknown"
# Conservative band when no profile fits: the Risk Officer should be sizing
# down, not up, in an unclassified market.
UNKNOWN_SIZING_BAND = (0.25, 0.5)


@dataclass(frozen=True, slots=True)
class Condition:
    """Closed interval check on one feature: lo <= value <= hi (either open)."""

    feature: str
    lo: float | None = None
    hi: float | None = None

    def passes(self, features: FeatureVector) -> bool:
        value = features.get(self.feature)
        if value is None:
            return False
        if self.lo is not None and value < self.lo:
            return False
        if self.hi is not None and value > self.hi:
            return False
        return True


@dataclass(frozen=True, slots=True)
class RegimeProfile:
    """One regime card's machine-checkable conditions and sizing band."""

    name: str
    hard: tuple[Condition, ...]
    soft: tuple[Condition, ...]
    min_soft: int
    sizing_band: tuple[float, float]


@dataclass(frozen=True, slots=True)
class ProfileScore:
    """Fit result for one profile against one feature vector.

    ``qualifies`` is True when all hard conditions passed AND the soft-hit
    count met the profile's minimum.
    """

    name: str
    qualifies: bool
    soft_hits: int
    soft_total: int
    sizing_band: tuple[float, float]

    @property
    def score(self) -> float:
        if not self.qualifies:
            return 0.0
        if self.soft_total == 0:
            return 1.0
        return self.soft_hits / self.soft_total


def score_profile(profile: RegimeProfile, features: FeatureVector) -> ProfileScore:
    hard_ok = all(condition.passes(features) for condition in profile.hard)
    soft_hits = sum(1 for condition in profile.soft if condition.passes(features))
    return ProfileScore(
        name=profile.name,
        qualifies=hard_ok and soft_hits >= profile.min_soft,
        soft_hits=soft_hits,
        soft_total=len(profile.soft),
        sizing_band=profile.sizing_band,
    )


def score_profiles(
    profiles: tuple[RegimeProfile, ...], features: FeatureVector
) -> list[ProfileScore]:
    """Score every profile, best first (qualifying profiles before failing ones)."""

    scores = [score_profile(profile, features) for profile in profiles]
    return sorted(scores, key=lambda s: (s.qualifies, s.score), reverse=True)


DEFAULT_PROFILES: tuple[RegimeProfile, ...] = (
    RegimeProfile(
        # docs/regimes/late-cycle-melt-up.md: suppressed vol, positive trend,
        # narrowing breadth carried by few names. Card band is 8-14% SPX
        # realized; the IEX proxy reads hot (2026-07-06 live: 0.180 in an
        # otherwise textbook melt-up vector), so the ceiling sits at 0.18.
        name="late-cycle-melt-up",
        hard=(
            Condition("vol_20d", hi=0.18),
            Condition("trend_50_200", lo=0.0),
        ),
        soft=(
            Condition("pct_above_200dma", lo=0.03),
            Condition("vol_trend", hi=1.1),
            Condition("credit_hyg_tlt_20d", lo=-0.01),
            Condition("breadth_above_200dma", hi=0.7),
        ),
        min_soft=2,
        sizing_band=(0.75, 1.0),  # vol is coiled; the card warns against short-vol carry
    ),
    RegimeProfile(
        # docs/regimes/crisis-recovery.md: elevated but compressing vol,
        # trend repairing off a low base, breadth recovering. The floor
        # adjoins the melt-up ceiling at 0.18 deliberately: live evidence
        # (2026-07-06, the first regime.changed event) showed the market
        # trading exactly at that boundary, so a gap there would yield
        # unknown in a market both cards nearly describe. Boundary flapping
        # is damped by the debounce window and hysteresis, not by spacing.
        name="crisis-recovery",
        hard=(
            Condition("vol_20d", lo=0.18),
            Condition("vol_trend", hi=1.0),
        ),
        soft=(
            Condition("pct_above_200dma", hi=0.05),
            Condition("credit_hyg_tlt_20d", lo=0.0),
            Condition("breadth_above_200dma", lo=0.2, hi=0.7),
        ),
        min_soft=2,
        sizing_band=(0.75, 1.25),
    ),
    RegimeProfile(
        # docs/regimes/stagflation.md: grinding, directionless, weak credit,
        # high dispersion between winners and losers.
        name="stagflation",
        hard=(
            Condition("vol_20d", lo=0.14, hi=0.28),
            Condition("trend_50_200", hi=0.02),
        ),
        soft=(
            Condition("credit_hyg_tlt_20d", hi=0.0),
            Condition("dispersion_20d", lo=0.05),
            Condition("breadth_above_200dma", hi=0.5),
        ),
        min_soft=2,
        sizing_band=(0.5, 0.75),
    ),
    RegimeProfile(
        # docs/regimes/wartime.md: shock vol, broken breadth, credit stress,
        # dispersion driven by exposure to the conflict.
        name="wartime",
        hard=(
            Condition("vol_20d", lo=0.22),
            Condition("vol_trend", lo=1.0),
        ),
        soft=(
            Condition("breadth_above_200dma", hi=0.4),
            Condition("credit_hyg_tlt_20d", hi=-0.01),
            Condition("dispersion_20d", lo=0.06),
            Condition("pct_above_200dma", hi=0.0),
        ),
        min_soft=2,
        sizing_band=(0.25, 0.75),
    ),
)
