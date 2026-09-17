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
        """Whether any live session informed this, as opposed to the prior alone."""

        return self.n_sessions >= MIN_SESSIONS and self.observed_ir is not None

    def to_payload(self) -> dict[str, object]:
        return {
            "mean_ir": self.mean_ir,
            "sd_ir": self.sd_ir,
            "n_sessions": self.n_sessions,
            "observed_ir": self.observed_ir,
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


def update_posterior(
    excess_series: Sequence[float],
    *,
    prior_ir: float = PRIOR_IR,
    prior_ir_sd: float = PRIOR_IR_SD,
) -> SkillPosterior:
    """Update the skill prior with a live per-session excess-return series.

    ``excess_series`` is exactly what
    :func:`shrap.research.live_benchmark.compare_to_benchmark` returns — active
    return per session against an exposure-matched benchmark. Using the same
    series the promote gate's own information ratio is computed from is the point:
    the forward measurement and the sizing decision cannot drift apart.

    A series that cannot produce a ratio — fewer than two sessions, or zero
    dispersion — returns the prior rather than raising. Absence of evidence is
    not evidence, and it must not be able to size a position up.
    """

    if len(excess_series) < MIN_SESSIONS:
        return prior_posterior(prior_ir=prior_ir, prior_ir_sd=prior_ir_sd)

    n = len(excess_series)
    mean = sum(excess_series) / n
    variance = sum((value - mean) ** 2 for value in excess_series) / (n - 1)
    if variance <= 0.0:
        # A flat series has no dispersion, so no ratio and no information about
        # skill — however many sessions of it there are.
        return prior_posterior(prior_ir=prior_ir, prior_ir_sd=prior_ir_sd)

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
    )


__all__ = [
    "FULL_SIZE_IR",
    "MAX_POSTERIOR_FRACTION",
    "MIN_SESSIONS",
    "PRIOR_IR",
    "PRIOR_IR_SD",
    "TRADING_DAYS_PER_YEAR",
    "SkillPosterior",
    "prior_posterior",
    "update_posterior",
]
