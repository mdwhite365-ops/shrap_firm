"""The skill posterior that fills the Kelly slot.

**The continuity property this file was built around is gone, and the tests now
pin its absence.** When this shipped, a zero-evidence posterior returned exactly
the flat stage fraction (both 0.25), so enabling it changed nothing on day one.
Mike raised `STAGE_FRACTIONS["paper"]` to 0.80 on 2026-09-18 after the accounts
turned out to be 84% cash, and this rule stayed at 0.25. Enabling
`posterior_sizing` on a paper strategy now cuts its size by **3.2x**, and
because `MAX_POSTERIOR_FRACTION` is 0.50 the posterior can no longer size *up*
past the paper stage at all — the upside half of the mechanism is dead there.

Both rules are defensible and they answer different questions. Reconciling them
is unruled. What these tests guarantee is that the disagreement is explicit.

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
    SKEPTICAL_PRIOR_IR,
    TRADING_DAYS_PER_YEAR,
    SkillPosterior,
    posterior_from_backtest,
    prior_posterior,
    selection_discounted_sd,
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


def test_no_evidence_returns_exactly_one_quarter() -> None:
    """The zero-evidence limit of the rule, which is still 0.25.

    It falls out of three independently calibrated numbers: the promote floor
    (0.5), the Kelly cap (0.50) and "an IR of 1.0 is exceptional and rare". It
    is the *limit* of the rule rather than a special case branched around it.
    """

    assert prior_posterior().fraction == 0.25
    assert PRIOR_IR / FULL_SIZE_IR * MAX_POSTERIOR_FRACTION == 0.25


def test_enabling_the_posterior_is_now_a_cliff_and_that_is_recorded() -> None:
    """This module used to claim "there is no cliff at the first session".

    That held only while the zero-evidence posterior and the stage table both
    read 0.25. Mike raised `STAGE_FRACTIONS["paper"]` to 0.80 on 2026-09-18
    when the accounts turned out to be 84% cash, and this rule did not move.

    The claim is therefore false and the docstring now says so. Pinned as a
    test because a silent 3.2x drop in exposure the moment a flag is enabled is
    exactly the kind of thing that should fail loudly if someone "fixes" one
    number without the other.
    """

    assert prior_posterior().fraction < STAGE_FRACTIONS["paper"]
    assert STAGE_FRACTIONS["paper"] / prior_posterior().fraction == pytest.approx(3.2)


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


def test_sustained_outperformance_can_no_longer_outgrow_the_paper_stage() -> None:
    """The upside half of this mechanism is dead at the `paper` stage.

    When this was written, `STAGE_FRACTIONS["paper"]` was 0.25 and the module
    documented a deliberate governance change: "code may now increase risk
    without a human promoting a stage." Mike raised the paper fraction to 0.80
    on 2026-09-18, and `MAX_POSTERIOR_FRACTION` is 0.50 — the spec's Kelly
    ceiling, which no amount of evidence passes.

    So at `paper`, enabling the posterior can now only ever size **down**. A
    strategy running a sustained observed IR of 1.2 reaches 0.425, still well
    under 0.80. The mechanism is unchanged; what changed is that the stage it is
    compared against is now above its ceiling.
    """

    posterior = update_posterior(_series_with_ir(1.2, 252))

    assert posterior.observed_ir is not None and posterior.observed_ir > PRIOR_IR
    assert posterior.fraction > 0.25
    assert posterior.fraction <= MAX_POSTERIOR_FRACTION
    assert posterior.fraction < STAGE_FRACTIONS["paper"]


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
    # Compared against the rule's own zero-evidence value (0.25), not the
    # stage table, which no longer agrees with it.
    assert abs(posterior.fraction - 0.25) < 0.1


# --- wiring into sizing -------------------------------------------------------


def test_size_intent_without_a_posterior_is_unchanged() -> None:
    """Every existing caller keeps today's behaviour exactly."""

    decision = size_intent(requested_quantity=100.0, stage="paper", regime_multiplier=0.75)

    assert decision.approved_quantity == pytest.approx(100.0 * 0.80 * 0.75)
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
    # Still reported, so the card shows what the stage WOULD have given.
    assert decision.stage_fraction == 0.80


def test_a_no_evidence_posterior_now_sizes_well_below_no_posterior() -> None:
    """The continuity this test used to assert is gone, and deliberately so.

    Until 2026-09-18 a zero-evidence posterior and no posterior at all sized
    identically, because both came to 0.25. Mike raised the paper stage to 0.80
    and this rule stayed at 0.25, so switching the posterior on now cuts a
    never-traded strategy to less than a third of its size.

    Pinned with the exact ratio because a silent 3.2x drop the moment a flag is
    enabled is precisely what should fail loudly if someone adjusts one of
    these numbers without the other.
    """

    without = size_intent(requested_quantity=80.0, stage="paper", regime_multiplier=0.75)
    with_prior = size_intent(
        requested_quantity=80.0,
        stage="paper",
        regime_multiplier=0.75,
        posterior=prior_posterior(),
    )

    assert with_prior.approved_quantity < without.approved_quantity
    ratio = without.approved_quantity / with_prior.approved_quantity
    assert ratio == pytest.approx(3.2)


def test_regime_still_scales_on_top_of_the_posterior() -> None:
    """The posterior replaces the STAGE fraction, not the regime multiplier."""

    posterior = SkillPosterior(mean_ir=1.0, sd_ir=0.2, n_sessions=400, observed_ir=1.1)

    full = size_intent(requested_quantity=100.0, stage="paper", posterior=posterior)
    wartime = size_intent(
        requested_quantity=100.0, stage="paper", regime_multiplier=0.25, posterior=posterior
    )

    assert full.approved_quantity == 100.0 * MAX_POSTERIOR_FRACTION
    assert wartime.approved_quantity == full.approved_quantity * 0.25


# --- the backtest as evidence (2026-09-17, KI-036) ----------------------------


def test_the_selection_discount_matches_the_gate_it_mirrors() -> None:
    """One multiple-testing factor, two places it is applied.

    The verdict raises the bar by `sqrt(1 + ln attempts)`; the posterior widens
    the error bar by the same factor. They are the same correction stated two
    ways, and if they ever drift apart the firm is pricing search twice with
    different numbers.
    """

    from shrap.research.strategy_evaluator.verdict import required_information_ratio

    for attempts in (1, 2, 4, 14, 50):
        gate_factor = required_information_ratio(1.0, attempts)
        assert selection_discounted_sd(1.0, attempts) == pytest.approx(gate_factor)


def test_a_single_attempt_is_not_discounted() -> None:
    """No search, no selection bias, nothing to widen."""

    assert selection_discounted_sd(0.47, 1) == 0.47
    assert selection_discounted_sd(0.47, 0) == 0.47


def test_searching_harder_makes_the_result_count_for_less() -> None:
    """The monotonicity the whole correction rests on."""

    assert selection_discounted_sd(0.47, 14) > selection_discounted_sd(0.47, 4) > 0.47


def test_a_non_positive_standard_error_is_refused() -> None:
    """Zero SE would be infinite precision — a backtest that knew everything.

    It would swamp the prior and any amount of live evidence, so it must not be
    reachable by accident.
    """

    with pytest.raises(ValueError, match="standard_error must be positive"):
        selection_discounted_sd(0.0, 4)


def test_the_firms_best_strategy_sizes_below_the_flat_fraction() -> None:
    """Momentum 126/21: IR 0.448, SE 0.47, four lineage attempts.

    The number that makes this a real governance change rather than a
    refactor. Today it would size at the flat 0.25 because a human moved it to
    `paper`; on its own measurement it sizes at 0.147.
    """

    posterior = posterior_from_backtest(information_ratio=0.448, standard_error=0.47, attempts=4)

    assert posterior.mean_ir == pytest.approx(0.293, abs=0.005)
    assert posterior.fraction == pytest.approx(0.147, abs=0.005)
    assert posterior.fraction < prior_posterior().fraction


def test_every_strategy_the_firm_has_evaluated_sizes_down() -> None:
    """The docstring claims the direction is structural, not a coincidence.

    Any discounted backtest landing under the old PRIOR_IR of 0.5 sizes below
    the flat fraction, and the firm's whole history of measured IRs is in that
    set. If a future strategy escapes it, that is earned rather than a bug —
    hence the positive control below.
    """

    for measured in (-0.495, -0.092, 0.236, 0.306, 0.415, 0.448, 0.456):
        posterior = posterior_from_backtest(
            information_ratio=measured, standard_error=0.47, attempts=4
        )
        assert posterior.fraction < 0.25, measured


def test_a_genuinely_strong_backtest_earns_more_than_the_flat_fraction() -> None:
    """Not a blanket cut — a reallocation toward evidence."""

    posterior = posterior_from_backtest(information_ratio=1.6, standard_error=0.30, attempts=1)

    assert posterior.fraction > 0.25


def test_a_losing_backtest_gets_no_position_at_all() -> None:
    """ "Kill more aggressively than you promote", stated continuously."""

    assert (
        posterior_from_backtest(information_ratio=-1.5, standard_error=0.40, attempts=1).fraction
        == 0.0
    )


def test_the_backtest_prior_is_centred_on_no_skill_not_on_the_floor() -> None:
    """A backtest of exactly zero must leave the belief at zero.

    Centring on the promote floor would have a strategy that measured *no
    edge at all* still sized as though it sat at 0.5, which is the assumption
    KI-036 removed.
    """

    assert posterior_from_backtest(
        information_ratio=0.0, standard_error=0.47
    ).mean_ir == pytest.approx(0.0)
    assert SKEPTICAL_PRIOR_IR == 0.0


def test_a_backtest_belief_counts_as_evidence_based() -> None:
    """It has no live sessions, but it is not the prior alone either."""

    posterior = posterior_from_backtest(information_ratio=0.448, standard_error=0.47)

    assert posterior.n_sessions == 0
    assert posterior.observed_ir is None
    assert posterior.is_evidence_based
    assert posterior.to_payload()["backtest_ir"] == 0.448


# --- chaining live evidence onto the backtest ---------------------------------


def test_live_sessions_update_the_backtest_belief_rather_than_replacing_it() -> None:
    """Conjugacy: the backtest's posterior IS the prior for live trading."""

    backtest = posterior_from_backtest(information_ratio=0.448, standard_error=0.47, attempts=4)
    chained = update_posterior(_series_with_ir(1.5, 200), prior=backtest)

    assert chained.n_sessions == 200
    assert chained.mean_ir > backtest.mean_ir
    # Held between the two readings, not snapped to either.
    assert backtest.mean_ir < chained.mean_ir < chained.observed_ir
    # Provenance survives the update.
    assert chained.backtest_ir == 0.448


def test_a_strategy_arrives_at_its_first_session_believing_its_backtest() -> None:
    """The property that makes this a connection rather than two mechanisms.

    One session cannot produce a ratio, so the belief must be the backtest's —
    unchanged, and specifically NOT reset to the promote floor.
    """

    backtest = posterior_from_backtest(information_ratio=0.448, standard_error=0.47, attempts=4)

    assert update_posterior([0.001], prior=backtest) == backtest


def test_a_flat_live_series_cannot_wash_out_the_backtest() -> None:
    """Zero dispersion is no information, however many sessions of it there are.

    Returning the floor-centred prior here would let a strategy that stopped
    trading drift its size UP toward 0.25.
    """

    backtest = posterior_from_backtest(information_ratio=0.1, standard_error=0.47)

    assert update_posterior([0.002] * 300, prior=backtest) == backtest


def test_live_evidence_eventually_outweighs_the_backtest() -> None:
    """Enough sessions and the backtest stops mattering — as it should."""

    backtest = posterior_from_backtest(information_ratio=0.448, standard_error=0.47, attempts=4)
    long_run = update_posterior(_series_with_ir(-0.8, 3000), prior=backtest)

    assert long_run.mean_ir < 0.0
    assert long_run.fraction == 0.0


def test_omitting_the_prior_keeps_the_previous_behaviour_exactly() -> None:
    """The legacy path is untouched, including the flat-0.25 property."""

    assert prior_posterior().fraction == 0.25
    series = _series_with_ir(0.9, 100)
    assert update_posterior(series) == update_posterior(series, prior=None)
