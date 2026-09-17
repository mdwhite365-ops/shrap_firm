"""The skill posterior that fills the Kelly slot.

The property that makes this safe to merge is **continuity**: with no live
evidence the posterior returns exactly the flat fraction the firm already uses,
so nothing changes on the day it ships. Everything after that is evidence moving
size, in the direction the evidence points.

What makes it tolerable to *run* is narrower than the first draft of this file
claimed, and worth stating precisely: the update is linear and unbiased, and all
of the conservatism lives in two places — a prior centred at the promote floor,
and the clamps at zero and at the spec's 0.50 cap. There is no asymmetric
weighting, and a reader should not assume one.
"""

from __future__ import annotations

import math
import random

import pytest

from shrap.research.forward_score import TRADING_DAYS_PER_YEAR as FORWARD_TRADING_DAYS
from shrap.research.strategy_evaluator.engine import DEFAULT_INFORMATION_RATIO_FLOOR
from shrap.risk_compliance.risk_officer.limits import STAGE_FRACTIONS
from shrap.risk_compliance.risk_officer.posterior import (
    FULL_SIZE_IR,
    MAX_POSTERIOR_FRACTION,
    PRIOR_IR,
    TRADING_DAYS_PER_YEAR,
    SkillPosterior,
    prior_posterior,
    update_posterior,
)
from shrap.risk_compliance.risk_officer.sizing import size_intent


def _series_with_ir(target_ir: float, n: int) -> list[float]:
    """A series whose annualised information ratio is exactly ``target_ir``.

    Constructed rather than sampled. The first draft of this file used
    ``random.gauss`` with a requested mean, and over 252 sessions at a realistic
    effect size the realised mean landed on the *opposite side of zero* from the
    one requested — so a test named "underperformance sizes to zero" was handing
    the posterior a winning series. A test of a deterministic rule should not
    have a sampling distribution.
    """

    rng = random.Random(11)
    raw = [rng.gauss(0.0, 1.0) for _ in range(n)]
    mean = sum(raw) / n
    sd = math.sqrt(sum((value - mean) ** 2 for value in raw) / (n - 1))
    # Standardise to mean 0 / sample sd 1, then shift so mean/sd * sqrt(252) is
    # exactly the ratio asked for.
    return [(value - mean) / sd + target_ir / math.sqrt(TRADING_DAYS_PER_YEAR) for value in raw]


# --- the constants must not drift ---------------------------------------------


def test_prior_is_the_promote_floor() -> None:
    """Pinned rather than imported, to keep numpy out of the Pre-Trade path.

    A local copy of a number that must agree with another module is exactly the
    failure #210 fixed by collapsing eight copies of one protocol. The copy is
    deliberate here; this test is the thing that makes it safe.
    """

    assert PRIOR_IR == DEFAULT_INFORMATION_RATIO_FLOOR


def test_annualisation_agrees_across_the_firm() -> None:
    assert TRADING_DAYS_PER_YEAR == FORWARD_TRADING_DAYS


# --- continuity: nothing changes on the day this ships ------------------------


def test_no_evidence_returns_exactly_the_flat_paper_fraction() -> None:
    """The headline safety property, and the reason there is no cliff.

    The flat 0.25 is the zero-evidence *limit* of this rule, not a special case
    branched around it. It falls out of three independently calibrated numbers:
    the promote floor (0.5), the Kelly cap (0.50) and "an IR of 1.0 is
    exceptional and rare".
    """

    assert prior_posterior().fraction == STAGE_FRACTIONS["paper"]
    assert PRIOR_IR / FULL_SIZE_IR * MAX_POSTERIOR_FRACTION == STAGE_FRACTIONS["paper"]


def test_too_few_sessions_returns_the_prior() -> None:
    """Absence of evidence must never be able to size a position up."""

    assert update_posterior([]).fraction == prior_posterior().fraction
    assert update_posterior([0.001]).fraction == prior_posterior().fraction
    assert update_posterior([]).n_sessions == 0


def test_a_flat_series_carries_no_information_however_long() -> None:
    """Zero dispersion means no ratio — not a division by zero, and not a win."""

    posterior = update_posterior([0.0] * 500)

    assert posterior.observed_ir is None
    assert posterior.fraction == prior_posterior().fraction
    assert not posterior.is_evidence_based


# --- evidence moves size ------------------------------------------------------


def test_sustained_outperformance_raises_size_above_the_stage_fraction() -> None:
    """The governance change Mike took on 2026-09-17: code may raise risk."""

    posterior = update_posterior(_series_with_ir(1.2, 252))

    assert posterior.observed_ir is not None and posterior.observed_ir > PRIOR_IR
    assert posterior.fraction > STAGE_FRACTIONS["paper"]
    assert posterior.is_evidence_based


def test_sustained_underperformance_sizes_to_zero() -> None:
    posterior = update_posterior(_series_with_ir(-1.2, 252))

    assert posterior.mean_ir < 0.0
    assert posterior.fraction == 0.0


def test_a_losing_posterior_is_never_a_short() -> None:
    """Clamped at zero: the evidence says stop, not reverse."""

    posterior = SkillPosterior(mean_ir=-5.0, sd_ir=0.3, n_sessions=300, observed_ir=-5.0)
    assert posterior.fraction == 0.0


def test_the_response_to_evidence_is_linear_and_unbiased() -> None:
    """No hidden thumb on the scale in either direction.

    An earlier draft of this file claimed the rule "sizes down faster than it
    sizes up" and called that operating principle 2 falling out of the
    arithmetic. It does not: the posterior is a linear blend of prior and
    observation, and the fraction is linear in the posterior, so equal and
    opposite departures in observed IR produce equal and opposite departures in
    size until a clamp is reached. The claim was a nice story the code did not
    support, and the test that was written to demonstrate it failed.

    Recorded as a test rather than deleted, because "this mechanism has no
    built-in conservatism beyond its prior and its clamps" is the thing a reader
    needs to know before trusting it with risk.
    """

    base = update_posterior(_series_with_ir(0.0, 252)).mean_ir
    up = update_posterior(_series_with_ir(0.6, 252)).mean_ir
    down = update_posterior(_series_with_ir(-0.6, 252)).mean_ir

    assert up - base == pytest.approx(base - down, rel=1e-9)


def test_conservatism_comes_from_the_prior_and_the_clamps_only() -> None:
    """Which is why reaching the cap is hard and reaching zero is not.

    A losing strategy hits zero once its posterior mean does, at an observed IR
    of about -0.5. A winning one needs a posterior mean of 1.0 for the cap, which
    against a prior at 0.5 needs a *sustained* observed IR of ~1.5 — "exceptional
    and rare" by the Evaluator's own note. The bound is one-sided in effect, but
    it comes from where the two limits sit, not from any asymmetric update.
    """

    assert update_posterior(_series_with_ir(-0.5, 252)).fraction == 0.0
    assert update_posterior(_series_with_ir(1.5, 252)).fraction == MAX_POSTERIOR_FRACTION
    assert 0.0 < update_posterior(_series_with_ir(1.0, 252)).fraction < MAX_POSTERIOR_FRACTION


def test_the_cap_holds_against_any_amount_of_evidence() -> None:
    """The spec's 50% ceiling. Exposure and drawdown limits still bind on top."""

    absurd = update_posterior(_series_with_ir(120.0, 2000))

    assert absurd.observed_ir is not None and absurd.observed_ir > 100.0
    assert absurd.fraction == MAX_POSTERIOR_FRACTION


def test_uncertainty_shrinks_as_sessions_accumulate() -> None:
    short = update_posterior(_series_with_ir(1.2, 20))
    long = update_posterior(_series_with_ir(1.2, 500))

    assert long.sd_ir < short.sd_ir < prior_posterior().sd_ir


def test_nine_sessions_barely_move_the_posterior() -> None:
    """The live reading that started all this: +0.84 over nine sessions.

    The standard error of an annualised ratio over nine sessions is ~5.3, so a
    reading above the promote floor is worth very little — and the posterior is
    required to say so rather than acting on it.
    """

    posterior = update_posterior(_series_with_ir(0.84, 9))

    assert abs(posterior.mean_ir - PRIOR_IR) < 0.2
    assert abs(posterior.fraction - STAGE_FRACTIONS["paper"]) < 0.1


# --- wiring into sizing -------------------------------------------------------


def test_size_intent_without_a_posterior_is_unchanged() -> None:
    """Every existing caller keeps today's behaviour exactly."""

    decision = size_intent(requested_quantity=100.0, stage="paper", regime_multiplier=0.75)

    assert decision.approved_quantity == 100.0 * 0.25 * 0.75
    assert decision.kelly_posterior is None


def test_size_intent_uses_the_posterior_in_place_of_the_stage_fraction() -> None:
    posterior = SkillPosterior(mean_ir=0.8, sd_ir=0.4, n_sessions=300, observed_ir=0.9)

    decision = size_intent(
        requested_quantity=100.0,
        stage="paper",
        regime_multiplier=1.0,
        posterior=posterior,
    )

    assert decision.kelly_posterior == posterior.fraction
    assert decision.approved_quantity == 100.0 * posterior.fraction
    # The stage fraction is still reported, so a decision row shows both the
    # number that would have applied and the one that did.
    assert decision.stage_fraction == 0.25


def test_a_no_evidence_posterior_sizes_identically_to_no_posterior() -> None:
    """Continuity again, this time through the whole sizing path."""

    without = size_intent(requested_quantity=80.0, stage="paper", regime_multiplier=0.75)
    with_prior = size_intent(
        requested_quantity=80.0,
        stage="paper",
        regime_multiplier=0.75,
        posterior=prior_posterior(),
    )

    assert with_prior.approved_quantity == without.approved_quantity


def test_regime_still_scales_on_top_of_the_posterior() -> None:
    """The posterior replaces the STAGE fraction, not the regime multiplier."""

    posterior = SkillPosterior(mean_ir=1.0, sd_ir=0.2, n_sessions=400, observed_ir=1.1)

    full = size_intent(requested_quantity=100.0, stage="paper", posterior=posterior)
    wartime = size_intent(
        requested_quantity=100.0, stage="paper", regime_multiplier=0.25, posterior=posterior
    )

    assert full.approved_quantity == 100.0 * MAX_POSTERIOR_FRACTION
    assert wartime.approved_quantity == full.approved_quantity * 0.25
