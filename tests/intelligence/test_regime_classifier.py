"""Tests for profile scoring and debounced classification."""

from __future__ import annotations

import pytest

from shrap.intelligence.regime.classifier import ClassifierState, classify
from shrap.intelligence.regime.features import FeatureVector
from shrap.intelligence.regime.profiles import (
    DEFAULT_PROFILES,
    UNKNOWN_LABEL,
    UNKNOWN_SIZING_BAND,
    Condition,
    RegimeProfile,
    score_profile,
)


def _features(**overrides: float | None) -> FeatureVector:
    defaults: dict[str, float | None] = {
        "vol_20d": None,
        "vol_trend": None,
        "pct_above_200dma": None,
        "trend_50_200": None,
        "breadth_above_200dma": None,
        "dispersion_20d": None,
        "credit_hyg_tlt_20d": None,
    }
    defaults.update(overrides)
    return FeatureVector(**defaults)  # type: ignore[arg-type]


MELT_UP = _features(
    vol_20d=0.10,
    vol_trend=0.9,
    pct_above_200dma=0.08,
    trend_50_200=0.03,
    breadth_above_200dma=0.55,
    dispersion_20d=0.04,
    credit_hyg_tlt_20d=0.01,
)

WARTIME = _features(
    vol_20d=0.35,
    vol_trend=1.8,
    pct_above_200dma=-0.12,
    trend_50_200=-0.05,
    breadth_above_200dma=0.15,
    dispersion_20d=0.09,
    credit_hyg_tlt_20d=-0.06,
)


def test_condition_missing_feature_never_passes() -> None:
    condition = Condition("vol_20d", lo=0.0)
    assert not condition.passes(_features())


def test_condition_interval_bounds() -> None:
    condition = Condition("vol_20d", lo=0.10, hi=0.20)
    assert condition.passes(_features(vol_20d=0.15))
    assert not condition.passes(_features(vol_20d=0.09))
    assert not condition.passes(_features(vol_20d=0.21))


def test_score_profile_requires_all_hard_and_min_soft() -> None:
    profile = RegimeProfile(
        name="test",
        hard=(Condition("vol_20d", hi=0.2),),
        soft=(Condition("trend_50_200", lo=0.0), Condition("dispersion_20d", lo=0.05)),
        min_soft=1,
        sizing_band=(0.5, 1.0),
    )
    hit = score_profile(profile, _features(vol_20d=0.1, trend_50_200=0.01))
    assert hit.qualifies
    assert hit.soft_hits == 1

    hard_fail = score_profile(profile, _features(vol_20d=0.3, trend_50_200=0.01))
    assert not hard_fail.qualifies

    soft_fail = score_profile(profile, _features(vol_20d=0.1))
    assert not soft_fail.qualifies


def test_default_profiles_classify_canonical_vectors() -> None:
    melt_up = classify(MELT_UP, DEFAULT_PROFILES, ClassifierState(), debounce_m=1)
    assert melt_up.label == "late-cycle-melt-up"
    assert melt_up.changed

    wartime = classify(WARTIME, DEFAULT_PROFILES, ClassifierState(), debounce_m=1)
    assert wartime.label == "wartime"


LIVE_2026_07_06_MORNING = _features(
    # First live Dell reading, intel.regime_history 18:44 UTC.
    vol_20d=0.1797160628600809,
    vol_trend=0.87467631803015,
    trend_50_200=0.06563858893263386,
    dispersion_20d=0.0398007999097633,
    pct_above_200dma=0.08459915253962036,
    credit_hyg_tlt_20d=0.002881922923068214,
    breadth_above_200dma=0.5555555555555556,
)


def test_live_reading_2026_07_06_below_boundary_classifies_as_melt_up() -> None:
    """Regression: vol_20d 0.180 in an otherwise textbook melt-up vector.

    Under the original ceiling (0.16) this fell in a threshold crack and
    produced unknown despite 4/4 melt-up soft conditions. Calibration v0.1
    moved the melt-up ceiling to 0.18 for the IEX proxy's hot vol reads.
    """

    result = classify(LIVE_2026_07_06_MORNING, DEFAULT_PROFILES, ClassifierState(), debounce_m=1)
    assert result.label == "late-cycle-melt-up"
    assert result.confidence == 1.0  # 4/4 soft conditions
    assert result.sizing_band == (0.75, 1.0)
    # Below the boundary, exactly one profile qualifies.
    assert [score.name for score in result.scores if score.qualifies] == ["late-cycle-melt-up"]


def test_live_boundary_crossing_2026_07_06_classifies_as_crisis_recovery() -> None:
    """Regression: the first live regime.changed event (19:04 UTC).

    Intraday, vol_20d drifted just above 0.18 and the deployed classifier
    debounced unknown -> crisis-recovery (2/3 soft, confidence 0.67). The
    melt-up ceiling and crisis-recovery floor adjoin at 0.18 so the market
    trading at that boundary always has a label; flapping is damped by the
    debounce window and hysteresis, not by threshold spacing.
    """

    crossed = _features(
        vol_20d=0.181,  # just above the boundary
        vol_trend=0.88,
        trend_50_200=0.066,
        dispersion_20d=0.040,
        pct_above_200dma=0.085,  # fails crisis soft (<= 0.05) -> 2/3 soft
        credit_hyg_tlt_20d=0.003,
        breadth_above_200dma=0.556,
    )
    result = classify(crossed, DEFAULT_PROFILES, ClassifierState(), debounce_m=1)
    assert result.label == "crisis-recovery"
    assert result.confidence == pytest.approx(2 / 3)
    # Above the boundary, melt-up's hard ceiling fails — one qualifier again.
    assert [score.name for score in result.scores if score.qualifies] == ["crisis-recovery"]


def test_all_features_missing_yields_unknown_with_conservative_band() -> None:
    result = classify(_features(), DEFAULT_PROFILES, ClassifierState(label="wartime"))
    # Unknown challenges the prior label but must survive the debounce window.
    assert result.label == "wartime"
    assert result.leader == UNKNOWN_LABEL
    assert result.streak == 1

    # After the debounce window, unknown takes over with the conservative band.
    state = ClassifierState(label="wartime", leader=UNKNOWN_LABEL, streak=2)
    flipped = classify(_features(), DEFAULT_PROFILES, state, debounce_m=3)
    assert flipped.label == UNKNOWN_LABEL
    assert flipped.changed
    assert flipped.sizing_band == UNKNOWN_SIZING_BAND


def test_debounce_requires_m_consecutive_leader_wins() -> None:
    state = ClassifierState(label=UNKNOWN_LABEL)

    first = classify(MELT_UP, DEFAULT_PROFILES, state, debounce_m=3)
    assert not first.changed
    assert first.label == UNKNOWN_LABEL
    assert first.leader == "late-cycle-melt-up"
    assert first.streak == 1

    second = classify(MELT_UP, DEFAULT_PROFILES, first.state, debounce_m=3)
    assert not second.changed
    assert second.streak == 2

    third = classify(MELT_UP, DEFAULT_PROFILES, second.state, debounce_m=3)
    assert third.changed
    assert third.label == "late-cycle-melt-up"
    assert third.streak == 3


def test_debounce_streak_resets_when_leader_flips() -> None:
    state = ClassifierState(label=UNKNOWN_LABEL, leader="late-cycle-melt-up", streak=2)
    result = classify(WARTIME, DEFAULT_PROFILES, state, debounce_m=3)
    assert not result.changed
    assert result.leader == "wartime"
    assert result.streak == 1


def test_stable_label_resets_streak() -> None:
    state = ClassifierState(label="late-cycle-melt-up", leader="wartime", streak=2)
    result = classify(MELT_UP, DEFAULT_PROFILES, state, debounce_m=3)
    assert not result.changed
    assert result.label == "late-cycle-melt-up"
    assert result.streak == 0


def test_hysteresis_prefers_prior_label_on_near_tie() -> None:
    profile_a = RegimeProfile(
        name="a",
        hard=(),
        soft=(Condition("vol_20d", lo=0.0), Condition("trend_50_200", lo=0.0)),
        min_soft=0,
        sizing_band=(1.0, 1.0),
    )
    profile_b = RegimeProfile(
        name="b",
        hard=(),
        soft=(Condition("vol_20d", lo=0.0),),
        min_soft=0,
        sizing_band=(0.5, 0.5),
    )
    # a scores 0.5 (1/2 soft), b scores 1.0 (1/1) — not a tie, b wins outright.
    features = _features(vol_20d=0.1)
    result = classify(features, (profile_a, profile_b), ClassifierState(label="a"), epsilon=0.05)
    assert result.leader == "b"

    # With epsilon wide enough to call it a tie, the prior label holds.
    tied = classify(features, (profile_a, profile_b), ClassifierState(label="a"), epsilon=0.6)
    assert tied.label == "a"
    assert not tied.changed
    assert tied.streak == 0
