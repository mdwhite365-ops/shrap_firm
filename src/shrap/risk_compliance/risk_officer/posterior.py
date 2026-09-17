"""The Bayesian Updater's posterior over a strategy's skill — the empty Kelly slot.

``docs/risk/policy.md`` specifies ``Kelly fraction x posterior edge x regime fit``
and the firm shipped two of the three factors. The posterior never arrived, so
:mod:`shrap.risk_compliance.risk_officer.sizing` implemented the spec's own
documented fallback — open question 4, "Kelly inputs when posterior is thin:
fall back to flat fraction" — and left ``kelly_posterior`` visible and ``None``.
This module fills it.

**Why a posterior rather than a bigger flat number.** The firm's forward test
cannot reach statistical significance on a human timescale: ``t = IR x sqrt(years)``,
so at the promote floor of 0.50 a verdict is ~16 years away. Waiting for one
means never acting on evidence; ignoring evidence means the flat fraction is the
same on day 1 and day 500. A posterior removes the need for a verdict — size
becomes continuous in accumulated evidence, and every session moves it a little.

**The model.** Normal-normal conjugate on the annualized information ratio,
which is the same quantity the promote gate scores, computed from the same
per-session excess series :mod:`shrap.research.live_benchmark` already produces.

    prior:       IR ~ N(PRIOR_IR, PRIOR_IR_SD^2)
    likelihood:  IR_observed ~ N(IR_true, TRADING_DAYS / n)
    posterior:   precision-weighted blend of the two

The likelihood variance is the standard result for a Sharpe-like estimator: the
standard error of an annualized ratio over ``n`` sessions is ``sqrt(252/n)``.
Nine sessions therefore carry an SE of 5.3 — which is why the live reading of
+0.84 moved nothing, and why this module will not pretend otherwise.

**The prior is the promote floor, and that is what makes this safe.** A strategy
at ``paper`` cleared an IR floor of 0.5 to get there, so "weakly believe it sits
at its floor" is the honest starting belief. It also gives the mechanism a
property worth stating plainly:

    with no live evidence at all, this returns exactly 0.25 —
    the flat paper fraction the firm uses today.

That is not a tuned coincidence. It falls out of three numbers the firm already
calibrated independently: the promote floor (0.5), the spec's Kelly cap (0.50),
and the evaluator's note that an IR of 1.0 is "exceptional and rare". The flat
fraction is the zero-evidence limit of this rule, so there is no cliff at the
first session and no special case for a strategy that has never traded.

**This can raise size above the stage fraction (Mike, 2026-09-17).** That is a
deliberate governance change: code may now increase risk without a human
promoting a stage. The brakes are that the prior's skepticism is doing the
regularising, the cap is the spec's own 0.50, and ``max_daily_loss`` /
``max_strategy_drawdown`` in :mod:`~shrap.risk_compliance.risk_officer.limits`
are untouched.

Be precise about where that conservatism actually lives, because it is easy to
claim more than is there. **The update is linear and unbiased** — equal and
opposite departures in observed IR move size equally and oppositely. There is no
asymmetric weighting. What bounds the upside is only where the two limits sit: a
losing strategy reaches zero once its posterior mean does, at an observed IR of
about -0.5, while the cap needs a posterior mean of 1.0, which against a prior at
0.5 takes a *sustained* observed IR of ~1.5 — "exceptional and rare" by the
Evaluator's own calibration note. The effect is one-sided; the mechanism is not.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

# Sessions per year, matching `research.forward_score.TRADING_DAYS_PER_YEAR` and
# the annualisation inside `strategy_evaluator.engine.sharpe`. All three describe
# the same calendar and must agree; a test pins them.
TRADING_DAYS_PER_YEAR = 252

# The prior's centre: the information-ratio floor a strategy had to clear to be
# promoted. Deliberately NOT imported from
# `research.strategy_evaluator.engine.DEFAULT_INFORMATION_RATIO_FLOOR`, which
# would pull numpy into the Pre-Trade Checker's import path for one float. A test
# asserts the two are equal, so they cannot drift silently — the failure mode
# #210 fixed by collapsing eight copies of one protocol.
PRIOR_IR = 0.5

# How strongly the prior is held, in IR units. 1.0 is deliberately wide: it is
# "we believe the floor, but we are ready to be shown otherwise", not "we are
# confident". Widening it makes live evidence count for more and the promote gate
# for less. Unruled first cut, Mike's to calibrate.
PRIOR_IR_SD = 1.0

# The posterior IR at which a strategy earns the full Kelly cap. The Evaluator's
# own calibration note calls an out-of-sample IR of 1.0 "exceptional and rare",
# which is the right bar for the largest size the firm will take.
FULL_SIZE_IR = 1.0

# The spec's Kelly ceiling: "the fraction at 25% by default and capped at 50%"
# (docs/risk/policy.md). No amount of evidence sizes past this here; the exposure
# and drawdown limits still bind on top.
MAX_POSTERIOR_FRACTION = 0.50

# Two sessions is the minimum for a dispersion estimate. Below it there is no
# observation to update on and the posterior is the prior — which is the flat
# fraction, i.e. exactly today's behaviour.
MIN_SESSIONS = 2

# The centre of the prior when a BACKTEST is the evidence: no skill.
#
# `PRIOR_IR` above centres on the promote floor, justified by "a strategy at
# paper cleared an IR floor of 0.5 to get there". KI-036 measured that
# justification away on 2026-09-17: the standard error of an annualised IR on
# this firm's panel is ~0.47, so clearing a 0.50 floor with a reading of 0.55 --
# or missing it with 0.448 -- are the same event as far as the data can tell.
# Believing the floor because a gate was passed is believing a coin flip.
#
# Zero is the honest belief about a trading rule before its evidence is read,
# and it is the belief the backtest then updates. The two constants coexist
# because they answer different questions: `PRIOR_IR` is what the firm believes
# about a strategy it has only a STAGE for, and this is what it believes about
# one it has a MEASUREMENT for.
SKEPTICAL_PRIOR_IR = 0.0


@dataclass(frozen=True, slots=True)
class SkillPosterior:
    """What the firm currently believes about one strategy's information ratio."""

    mean_ir: float
    """Posterior mean. The prior when there is no evidence."""

    sd_ir: float
    """Posterior standard deviation. Shrinks as ``n_sessions`` grows."""

    n_sessions: int
    observed_ir: float | None
    """The live reading before the prior was applied. ``None`` when unmeasurable."""

    backtest_ir: float | None = None
    """The backtested information ratio this belief started from, if any.

    Kept alongside ``observed_ir`` rather than folded into it because they are
    different kinds of evidence and the difference matters when reading a
    number back: a backtest is one measurement of the past made once, a live
    series is the strategy being wrong in public. Only the backtest carries a
    selection-bias discount (see :func:`posterior_from_backtest`)."""

    backtest_sd: float | None = None
    """The standard error the backtest was blended at, AFTER that discount.

    Recorded because it is the number that decides how much the backtest moved
    the belief, and it is not recoverable from the others."""

    @property
    def fraction(self) -> float:
        """The Kelly-substitute multiplier, in ``[0, MAX_POSTERIOR_FRACTION]``.

        A linear ramp from no size at a posterior IR of zero to the cap at
        :data:`FULL_SIZE_IR`. Linear rather than anything cleverer because the
        posterior is already doing the uncertainty work, and a second nonlinearity
        on top would be two corrections for one problem.

        Negative posterior means clamp to zero: a strategy the evidence says is
        losing does not get a short position, it gets no position.
        """

        ramp = self.mean_ir / FULL_SIZE_IR
        return max(0.0, min(ramp * MAX_POSTERIOR_FRACTION, MAX_POSTERIOR_FRACTION))

    @property
    def is_evidence_based(self) -> bool:
        """Whether any measurement informed this, as opposed to a prior alone.

        A backtest counts. It is weaker evidence than live sessions and is
        discounted for selection bias before it is used, but a belief built
        from one is not the same as a belief built from a stage label — which
        is the whole point of connecting the two.
        """

        live = self.n_sessions >= MIN_SESSIONS and self.observed_ir is not None
        return live or self.backtest_ir is not None

    def to_payload(self) -> dict[str, object]:
        return {
            "mean_ir": self.mean_ir,
            "sd_ir": self.sd_ir,
            "n_sessions": self.n_sessions,
            "observed_ir": self.observed_ir,
            "backtest_ir": self.backtest_ir,
            "backtest_sd": self.backtest_sd,
            "fraction": self.fraction,
            "evidence_based": self.is_evidence_based,
        }


def prior_posterior(
    *,
    prior_ir: float = PRIOR_IR,
    prior_ir_sd: float = PRIOR_IR_SD,
) -> SkillPosterior:
    """The belief before any live session — the promote floor, weakly held."""

    return SkillPosterior(
        mean_ir=prior_ir,
        sd_ir=prior_ir_sd,
        n_sessions=0,
        observed_ir=None,
    )


def selection_discounted_sd(standard_error: float, attempts: int) -> float:
    """Widen a backtest's standard error for the search that produced it.

    A strategy's backtest IR is not a random draw: it is the best of ``attempts``
    tried within its lineage, so it is biased upward by the search itself. The
    Evaluator already prices this by raising the bar it must clear --
    :func:`shrap.research.strategy_evaluator.verdict.required_information_ratio`
    uses ``floor * sqrt(1 + ln(attempts))``.

    A posterior has no bar to raise, so the same factor is applied to the
    *uncertainty* instead. That is the Bayesian statement of the same idea: a
    number found by searching is known less well than one found by looking once,
    and shrinking it toward the prior is what "known less well" means. The two
    corrections agree by construction and cannot drift apart -- a test pins them
    to the identical factor.

    ``attempts <= 1`` is no search and no discount.
    """

    if standard_error <= 0.0:
        raise ValueError("standard_error must be positive to weight a likelihood")
    if attempts <= 1:
        return standard_error
    return standard_error * math.sqrt(1.0 + math.log(attempts))


def posterior_from_backtest(
    *,
    information_ratio: float,
    standard_error: float,
    attempts: int = 1,
    prior_ir: float = SKEPTICAL_PRIOR_IR,
    prior_ir_sd: float = PRIOR_IR_SD,
) -> SkillPosterior:
    """Turn a walk-forward result into a belief, instead of a pass or a fail.

    This is the join KI-036 argued for. The Evaluator's verdict compares the
    backtest IR against a floor and throws the number away; on a six-year panel
    that comparison is worth ~0.11 standard errors and decides nothing. The same
    number, carrying its own error bar, is a perfectly good likelihood.

    ``standard_error`` is :func:`shrap.research.ir_precision.ir_standard_error`
    -- passed in rather than imported so this module keeps no dependency on the
    research package, which is what lets it live in the Pre-Trade Checker's
    import path.

    **What it does to size, on the firm's real numbers.** Momentum 126/21 scored
    IR 0.448 with an SE of 0.47 over four lineage attempts:

        discounted SD  0.47 * sqrt(1 + ln 4)      = 0.726
        posterior mean (0.448 / 0.726^2) / (1 + 1 / 0.726^2) = 0.293
        fraction       0.293 * 0.50               = 0.147

    versus the flat 0.25 it would get today. **Sizes go down**, because a
    strategy that measured 0.448 against a skeptical prior is believed less than
    one assumed to sit at the floor. That direction is not an accident of the
    constants: any strategy whose discounted backtest lands under the old
    ``PRIOR_IR`` of 0.5 sizes below the flat fraction, and every strategy the
    firm has ever evaluated is in that set.

    A strategy that genuinely measured well still earns more -- a discounted
    posterior mean of 0.9 sizes at 0.45 -- so this is a reallocation toward
    evidence, not a blanket cut.
    """

    effective_sd = selection_discounted_sd(standard_error, attempts)
    prior_precision = 1.0 / (prior_ir_sd**2)
    likelihood_precision = 1.0 / (effective_sd**2)
    posterior_precision = prior_precision + likelihood_precision
    posterior_mean = (
        prior_ir * prior_precision + information_ratio * likelihood_precision
    ) / posterior_precision

    return SkillPosterior(
        mean_ir=posterior_mean,
        sd_ir=math.sqrt(1.0 / posterior_precision),
        n_sessions=0,
        observed_ir=None,
        backtest_ir=information_ratio,
        backtest_sd=effective_sd,
    )


def update_posterior(
    excess_series: Sequence[float],
    *,
    prior: SkillPosterior | None = None,
    prior_ir: float = PRIOR_IR,
    prior_ir_sd: float = PRIOR_IR_SD,
) -> SkillPosterior:
    """Update the skill prior with a live per-session excess-return series.

    ``excess_series`` is exactly what
    :func:`shrap.research.live_benchmark.compare_to_benchmark` returns — active
    return per session against an exposure-matched benchmark. Using the same
    series the promote gate's own information ratio is computed from is the point:
    the forward measurement and the sizing decision cannot drift apart.

    ``prior`` chains this onto an existing belief — normally the one
    :func:`posterior_from_backtest` built. Conjugacy is what makes that sound:
    the posterior of the backtest IS the prior for live trading, so a strategy
    arrives at its first session already believing what its backtest showed, and
    each session moves it from there. Passing ``None`` keeps the standalone
    behaviour, where the prior is the promote floor.

    A series that cannot produce a ratio — fewer than two sessions, or zero
    dispersion — returns the prior rather than raising. Absence of evidence is
    not evidence, and it must not be able to size a position up.
    """

    if prior is not None:
        prior_ir, prior_ir_sd = prior.mean_ir, prior.sd_ir

    def _unchanged() -> SkillPosterior:
        """The belief we came in with, live evidence having added nothing."""

        if prior is not None:
            return prior
        return prior_posterior(prior_ir=prior_ir, prior_ir_sd=prior_ir_sd)

    if len(excess_series) < MIN_SESSIONS:
        return _unchanged()

    n = len(excess_series)
    mean = sum(excess_series) / n
    variance = sum((value - mean) ** 2 for value in excess_series) / (n - 1)
    if variance <= 0.0:
        # A flat series has no dispersion, so no ratio and no information about
        # skill — however many sessions of it there are.
        return _unchanged()

    observed_ir = mean / math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)

    # Precision-weighted blend. The likelihood's variance is the squared standard
    # error of an annualised ratio over n sessions: 252/n.
    prior_precision = 1.0 / (prior_ir_sd**2)
    likelihood_precision = n / TRADING_DAYS_PER_YEAR
    posterior_precision = prior_precision + likelihood_precision
    posterior_mean = (
        prior_ir * prior_precision + observed_ir * likelihood_precision
    ) / posterior_precision

    return SkillPosterior(
        mean_ir=posterior_mean,
        sd_ir=math.sqrt(1.0 / posterior_precision),
        n_sessions=n,
        observed_ir=observed_ir,
        backtest_ir=None if prior is None else prior.backtest_ir,
        backtest_sd=None if prior is None else prior.backtest_sd,
    )


__all__ = [
    "FULL_SIZE_IR",
    "MAX_POSTERIOR_FRACTION",
    "MIN_SESSIONS",
    "PRIOR_IR",
    "PRIOR_IR_SD",
    "SKEPTICAL_PRIOR_IR",
    "TRADING_DAYS_PER_YEAR",
    "SkillPosterior",
    "posterior_from_backtest",
    "prior_posterior",
    "selection_discounted_sd",
    "update_posterior",
]
