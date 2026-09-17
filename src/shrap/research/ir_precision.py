"""How precise is an information ratio estimate, and can the promote gate see it?

Every verdict the firm has ever issued compared a point estimate against a
floor with no error bar. This module supplies the error bar, and the answer it
gives is uncomfortable enough to be worth stating in the module that computes
it.

**Measured on the firm's own panel, 2026-09-17** (50 names, 1,543 daily bars,
2020-07-27 to 2026-09-16, active series re-measured on rolling three-year
windows at a one-month step -- identical decisions throughout, only the
measurement period moves):

    strategy            full IR    mean      sd      min      max   >=0.50
    momentum 126/21      +0.448   +0.334   0.168   -0.021   +0.743     16%
    volume-shock 50      +0.248   -0.258   0.286   -0.715   +0.512      3%
    low-volatility 252   -0.501   -0.835   0.402   -1.550   -0.141      0%
    time-series 252      -0.092   -0.150   0.279   -0.939   +0.416      0%

The firm's best strategy scores anywhere from -0.02 to +0.74 depending on which
three years you measure, and clears the floor in one window out of six.

**The rolling dispersion understates the error.** Consecutive windows overlap by
97%, so those estimates are near-duplicates and their spread is a lower bound.
:func:`ir_standard_error` gives the honest figure, and it is far larger.

**The consequence.** At 5.1 years the standard error on an annualised IR is
~0.47. The firm's best measured IR is 0.448 and the promote floor is 0.50:

    |0.50 - 0.448| / 0.47 = 0.11 standard errors

The gate is not "close but not good enough". It is **indistinguishable**. And
the sample size that would settle it does not exist:

    5.1 years  -> SE 0.470   resolves a gap of 0.94 IR at 2 sigma
     10 years  -> SE 0.335   resolves a gap of 0.67
     20 years  -> SE 0.237   resolves a gap of 0.47
     50 years  -> SE 0.150   resolves a gap of 0.30

Separating 0.45 from 0.50 at two sigma needs **~1,800 years** of daily history.
No backfill fixes this; no strategy variant fixes this. A backtest cannot
validate the promote floor, ever.

**This is the 16-year forward-test problem, applied to the backtest.** The same
`t = IR x sqrt(years)` arithmetic that says a paper-traded strategy needs 16
years to prove itself says a *backtested* one needs longer still, because the
floor it must be separated from is not zero but 0.50. Nobody had run the
arithmetic in this direction.

**What this module deliberately does not do.** It does not change a gate. Which
threshold the firm promotes on is Mike's ruling, and the honest successor to a
threshold is a posterior -- `risk_compliance/risk_officer/posterior.py` already
sizes continuously in accumulated evidence, which is what a measurement this
noisy calls for. This module's job is to stop the firm reporting a number
without saying how much of it is noise.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

TRADING_DAYS_PER_YEAR = 252

# Three years, the shortest span the firm treats as a real track record.
DEFAULT_ROLLING_WINDOW = 756

# One month. Overlapping by design -- see the caveat on `rolling_ir`.
DEFAULT_ROLLING_STEP = 21

# Conventional two-sigma significance, matching the forward-test `t = 2` target.
SIGNIFICANCE_SIGMAS = 2.0


def ir_standard_error(information_ratio: float, years: float) -> float:
    """Standard error of an annualised information ratio over ``years``.

    The Lo (2002) result for an annualised Sharpe-type ratio under iid returns:

        SE = sqrt((1 + IR^2 / 2) / T)

    The `IR^2 / 2` term is the contribution of estimating the *denominator* --
    tracking error is itself a sample statistic, and a strategy with a large
    ratio has more of its uncertainty there. It is a small correction at these
    magnitudes, and keeping it costs nothing.

    Serial correlation in the active series inflates this further, so the value
    returned is a floor on the true uncertainty rather than a fair estimate of
    it. The direction of that bias is the honest one: we understate how little
    we know.
    """

    if years <= 0.0:
        raise ValueError("years must be positive to estimate a standard error")
    return math.sqrt((1.0 + information_ratio * information_ratio / 2.0) / years)


def sigmas_from(information_ratio: float, *, floor: float, years: float) -> float:
    """How many standard errors separate this estimate from ``floor``.

    The number the verdict should have been reporting all along. Below ~2 the
    measurement cannot tell the two hypotheses apart, whichever side of the
    floor the point estimate happens to land on.
    """

    return abs(information_ratio - floor) / ir_standard_error(information_ratio, years)


def years_to_resolve(gap: float, *, information_ratio: float = 0.5) -> float:
    """Years of history needed to separate two IRs ``gap`` apart at two sigma.

    Inverts :func:`ir_standard_error`. Answers "how much more data would settle
    this?" -- and on this firm's numbers the answer is the argument for not
    waiting for data.
    """

    if gap <= 0.0:
        raise ValueError("gap must be positive")
    return (1.0 + information_ratio * information_ratio / 2.0) * (SIGNIFICANCE_SIGMAS / gap) ** 2


def rolling_ir(
    active_returns: Sequence[float],
    *,
    window: int = DEFAULT_ROLLING_WINDOW,
    step: int = DEFAULT_ROLLING_STEP,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> list[float]:
    """Re-measure IR on overlapping sub-windows of one active-return series.

    **The decisions are fixed.** The series is produced once by a single
    backtest, so every value here describes the same strategy making the same
    trades. Dispersion is therefore estimation error and not instability in the
    rule -- which is exactly the quantity a verdict needs and never reports.

    **Overlapping windows understate the spread**, badly: at the defaults each
    window shares 97% of its observations with the next, so consecutive
    estimates are near-duplicates. Use this to show a reader the range a
    strategy's score wanders over, and :func:`ir_standard_error` to say how
    uncertain any single score is.

    Returns an empty list when the series is shorter than one window, rather
    than a degenerate estimate from a partial one.
    """

    if window < 2:
        raise ValueError("window must span at least two periods")
    if step < 1:
        raise ValueError("step must be at least one period")
    n = len(active_returns)
    if n < window:
        return []
    out: list[float] = []
    for start in range(0, n - window + 1, step):
        chunk = active_returns[start : start + window]
        sd = statistics.pstdev(chunk)
        if sd == 0.0:
            continue
        mean = statistics.fmean(chunk)
        out.append(mean / sd * math.sqrt(periods_per_year))
    return out


@dataclass(frozen=True, slots=True)
class PrecisionResult:
    """An information ratio reported with what is known about its error."""

    information_ratio: float
    years: float
    floor: float
    standard_error: float
    sigmas_from_floor: float
    rolling_min: float | None
    rolling_max: float | None
    rolling_share_above_floor: float | None

    @property
    def is_resolvable(self) -> bool:
        """Whether the measurement can tell this estimate from the floor.

        False is the normal answer on a six-year panel and is not a defect in
        the strategy. It is a statement about the sample.
        """

        return self.sigmas_from_floor >= SIGNIFICANCE_SIGMAS

    def summary(self) -> str:
        verdict = "resolvable" if self.is_resolvable else "INDISTINGUISHABLE from floor"
        line = (
            f"IR {self.information_ratio:+.3f} +/- {self.standard_error:.3f} "
            f"over {self.years:.1f}y -> {self.sigmas_from_floor:.2f} SE "
            f"from floor {self.floor:.2f} ({verdict})"
        )
        if self.rolling_min is not None and self.rolling_max is not None:
            line += (
                f"; rolling 3y range {self.rolling_min:+.3f} to {self.rolling_max:+.3f}"
                f", above floor in {self.rolling_share_above_floor:.0%} of windows"
            )
        return line

    @classmethod
    def from_active_returns(
        cls,
        active_returns: Sequence[float],
        *,
        information_ratio: float,
        floor: float,
        periods_per_year: int = TRADING_DAYS_PER_YEAR,
        window: int = DEFAULT_ROLLING_WINDOW,
        step: int = DEFAULT_ROLLING_STEP,
    ) -> PrecisionResult:
        """Build from the series the engine already computes.

        ``information_ratio`` is passed in rather than recomputed so that this
        describes the number the verdict actually used. Recomputing it here
        would risk reporting an error bar around a different statistic than the
        one being gated.
        """

        years = len(active_returns) / periods_per_year
        rolls = rolling_ir(
            active_returns, window=window, step=step, periods_per_year=periods_per_year
        )
        return cls(
            information_ratio=information_ratio,
            years=years,
            floor=floor,
            standard_error=ir_standard_error(information_ratio, years),
            sigmas_from_floor=sigmas_from(information_ratio, floor=floor, years=years),
            rolling_min=min(rolls) if rolls else None,
            rolling_max=max(rolls) if rolls else None,
            rolling_share_above_floor=(
                sum(1 for r in rolls if r >= floor) / len(rolls) if rolls else None
            ),
        )


__all__ = [
    "DEFAULT_ROLLING_STEP",
    "DEFAULT_ROLLING_WINDOW",
    "SIGNIFICANCE_SIGMAS",
    "TRADING_DAYS_PER_YEAR",
    "PrecisionResult",
    "ir_standard_error",
    "rolling_ir",
    "sigmas_from",
    "years_to_resolve",
]
