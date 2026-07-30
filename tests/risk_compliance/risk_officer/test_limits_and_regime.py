"""The limits, and how a regime tightens them.

The numbers here are the v0.1 first cuts from ``docs/risk/policy.md``. These
tests pin the code to that document: if someone changes a default without
changing the doc, the doc stops being authoritative and these fail.
"""

from __future__ import annotations

import pytest

from shrap.risk_compliance.risk_officer.limits import (
    DEFAULT_STAGE_FRACTION,
    NO_REGIME_MULTIPLIER,
    PortfolioLimits,
    regime_multiplier,
    stage_fraction,
)

# --- the policy numbers -------------------------------------------------------


def test_defaults_match_the_policy_document() -> None:
    limits = PortfolioLimits()

    assert limits.max_ticker_weight == 0.20
    assert limits.max_gross_exposure == 1.00
    assert limits.max_net_exposure == 1.00
    assert limits.max_cluster_weight == 0.15
    assert limits.max_daily_loss == 0.02
    assert limits.max_strategy_drawdown == 0.25
    assert limits.correlation_threshold == 0.80
    assert limits.min_cluster_history == 40


def test_the_drawdown_limit_would_have_caught_the_observation_that_prompted_it() -> None:
    """The firm's first evaluation reported a 53.88% max drawdown and nothing
    noticed. Whatever this limit is set to, it has to be below that."""

    assert PortfolioLimits().max_strategy_drawdown < 0.5388


def test_a_ticker_cap_at_or_below_the_equal_weight_slot_is_rejected_by_review_not_code() -> None:
    """A ten-name equal-weight strategy targets 10% per name. The cap is 20% —
    twice the intended slot — so normal rebalancing never trips it. This is a
    calibration note in test form: if someone sets it to 0.10, every rebalance
    of the seeded momentum strategy starts getting scaled."""

    assert PortfolioLimits().max_ticker_weight > 0.10


def test_a_limit_outside_zero_to_one_is_refused() -> None:
    with pytest.raises(ValueError, match="max_ticker_weight"):
        PortfolioLimits(max_ticker_weight=1.5)
    with pytest.raises(ValueError, match="max_daily_loss"):
        PortfolioLimits(max_daily_loss=0.0)


# --- regime scaling -----------------------------------------------------------


def test_each_regime_takes_the_low_end_of_its_band() -> None:
    assert regime_multiplier("late-cycle-melt-up") == 0.75
    assert regime_multiplier("stagflation") == 0.50
    assert regime_multiplier("wartime") == 0.25


def test_a_band_above_one_never_raises_a_limit() -> None:
    """crisis-recovery runs 0.75-1.25. The upper half describes how much a
    strategy may want to size up, which is not the veto authority's argument to
    make. These are limits, not targets."""

    assert regime_multiplier("crisis-recovery") == 0.75
    assert regime_multiplier("anything", band=(1.5, 2.0)) == 1.0


def test_no_regime_event_means_quarter_size_not_full_size() -> None:
    """The failure that matters. Defaulting to 1.0 would run the firm at full
    size precisely when it has lost track of what market it is in."""

    assert regime_multiplier(None) == NO_REGIME_MULTIPLIER
    assert NO_REGIME_MULTIPLIER == 0.25


def test_an_unrecognised_label_is_treated_as_unknown() -> None:
    assert regime_multiplier("no-such-regime") == NO_REGIME_MULTIPLIER


def test_the_band_on_the_event_wins_over_the_hardcoded_table() -> None:
    """So a Regime Classifier recalibration takes effect without a change here."""

    assert regime_multiplier("wartime", band=(0.6, 0.9)) == 0.6


def test_scaling_tightens_exposure_caps() -> None:
    scaled = PortfolioLimits().scaled_for_regime(0.5)

    assert scaled.max_ticker_weight == 0.10
    assert scaled.max_gross_exposure == 0.50
    assert scaled.max_cluster_weight == 0.075


def test_scaling_does_not_touch_the_loss_limits() -> None:
    """How much of the account may be lost before the firm stops does not change
    because the market did. Scaling these with the regime would mean a calm
    market permits larger losses, which is backwards."""

    scaled = PortfolioLimits().scaled_for_regime(0.25)

    assert scaled.max_daily_loss == 0.02
    assert scaled.max_strategy_drawdown == 0.25


def test_a_full_size_regime_returns_the_limits_unchanged() -> None:
    limits = PortfolioLimits()

    assert limits.scaled_for_regime(1.0) is limits


# --- stage sizing -------------------------------------------------------------


def test_stage_fractions_follow_the_spec() -> None:
    assert stage_fraction("paper") == 0.25
    assert stage_fraction("small-size-paper") == 0.25
    assert stage_fraction("live-paper") == 0.50


def test_an_unknown_stage_sizes_at_the_lowest_tier() -> None:
    """A new stage name in the registry must not be able to widen sizing by
    being unfamiliar to this table."""

    assert stage_fraction("some-new-stage") == DEFAULT_STAGE_FRACTION
    assert stage_fraction(None) == DEFAULT_STAGE_FRACTION
    assert DEFAULT_STAGE_FRACTION == 0.25
