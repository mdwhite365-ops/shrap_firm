"""Label selection with hysteresis and debounce (spec Processing steps 3-4).

The classifier picks the highest-scoring qualifying profile, prefers the
prior label on near-ties (hysteresis), and only declares a regime change
after the same challenger has led for ``debounce_m`` consecutive runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from shrap.intelligence.regime.features import FeatureVector
from shrap.intelligence.regime.profiles import (
    UNKNOWN_LABEL,
    UNKNOWN_SIZING_BAND,
    ProfileScore,
    RegimeProfile,
    score_profiles,
)


@dataclass(frozen=True, slots=True)
class ClassifierState:
    """Carry-over between runs: current label plus the challenger streak."""

    label: str = UNKNOWN_LABEL
    leader: str = UNKNOWN_LABEL
    streak: int = 0


@dataclass(frozen=True, slots=True)
class Classification:
    """Outcome of one classifier run."""

    label: str
    prior_label: str
    changed: bool
    leader: str
    streak: int
    confidence: float
    sizing_band: tuple[float, float]
    scores: list[ProfileScore]
    missing_features: list[str]

    @property
    def state(self) -> ClassifierState:
        return ClassifierState(label=self.label, leader=self.leader, streak=self.streak)


def _winner(
    scores: list[ProfileScore], prior_label: str, epsilon: float
) -> tuple[str, float, tuple[float, float]]:
    """Best qualifying profile, with hysteresis: near-ties keep the prior label."""

    qualifying = [s for s in scores if s.qualifies]
    if not qualifying:
        return UNKNOWN_LABEL, 0.0, UNKNOWN_SIZING_BAND
    best = qualifying[0]
    for candidate in qualifying[1:]:
        if candidate.name == prior_label and best.score - candidate.score <= epsilon:
            best = candidate
            break
    return best.name, best.score, best.sizing_band


def classify(
    features: FeatureVector,
    profiles: tuple[RegimeProfile, ...],
    prior: ClassifierState,
    debounce_m: int = 3,
    epsilon: float = 0.05,
) -> Classification:
    """Run one classification pass against ``prior`` state.

    The emitted label only moves to a new value after the same challenger has
    won ``debounce_m`` consecutive runs; until then the prior label holds and
    the streak is reported for observability.
    """

    scores = score_profiles(profiles, features)
    winner_label, confidence, winner_band = _winner(scores, prior.label, epsilon)

    if winner_label == prior.label:
        return Classification(
            label=prior.label,
            prior_label=prior.label,
            changed=False,
            leader=prior.label,
            streak=0,
            confidence=confidence,
            sizing_band=winner_band,
            scores=scores,
            missing_features=features.missing(),
        )

    streak = prior.streak + 1 if winner_label == prior.leader else 1
    if streak >= debounce_m:
        return Classification(
            label=winner_label,
            prior_label=prior.label,
            changed=True,
            leader=winner_label,
            streak=streak,
            confidence=confidence,
            sizing_band=winner_band,
            scores=scores,
            missing_features=features.missing(),
        )

    # Challenger leads but has not survived the debounce window: hold the
    # prior label (and its band — sizing follows the emitted label, not the
    # not-yet-confirmed challenger).
    prior_band = _band_for(prior.label, profiles)
    prior_confidence = _score_for(prior.label, scores)
    return Classification(
        label=prior.label,
        prior_label=prior.label,
        changed=False,
        leader=winner_label,
        streak=streak,
        confidence=prior_confidence,
        sizing_band=prior_band,
        scores=scores,
        missing_features=features.missing(),
    )


def _band_for(label: str, profiles: tuple[RegimeProfile, ...]) -> tuple[float, float]:
    for profile in profiles:
        if profile.name == label:
            return profile.sizing_band
    return UNKNOWN_SIZING_BAND


def _score_for(label: str, scores: list[ProfileScore]) -> float:
    for score in scores:
        if score.name == label:
            return score.score
    return 0.0
