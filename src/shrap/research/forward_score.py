"""Forward-test scoring — how fast a live strategy actually grew its account.

Every measure the firm had until now came from a *backtest*: walk-forward Sharpe,
information ratio against a benchmark, PBO. Those answer "is there evidence of
skill?" before a strategy is deployed. Nothing answered "is it actually making
money?" afterwards, so a promoted strategy could decay silently.

ADR-0017 supplies both the data and the metric. One strategy per broker account
means the account's equity curve **is** that strategy's P&L — no attribution
model, no netting, a number the broker produces rather than one we derive. Mike's
ruling: *"their rewards should also be how quickly they can grow those
accounts."*

    score = growth since deployment / max drawdown since deployment

**Why the denominator exists.** Rewarding raw growth selects for leverage and
concentration: a strategy that puts the account into one name and gets lucky
grows it fastest right up until it doesn't. Growth stays in the numerator — this
is still "how fast did it grow the account" — divided by how much of the account
it risked to get there. A strategy growing 5%/month with a 6% worst drawdown
outranks one growing 15%/month with a 50% drawdown, which is the correct ordering
for a firm that intends to exist next year.

Four refusals, each guarding a way the score would otherwise flatter a strategy
that has not earned it:

**A curve that never drew down scores nothing, not infinity.** Dividing by zero
would put an untested strategy at the top of the leaderboard on no evidence —
precisely the "looks fine and isn't" failure the gates keep catching. It reports
its growth and says why there is no score yet.

**A short window produces no rate.** Annualising three weeks of returns yields
noise wearing a CAGR's clothes. Growth and drawdown are always reported; the
annualised figure appears only once the window supports one.

**Drawdown is measured on every sample, not on daily closes.** The Reconciliation
Agent writes a snapshot every ~300s, so an intraday round trip that lost 8% and
recovered is *seen*. This is deliberately stricter than close-to-close drawdown:
the risk was taken whether or not it showed up at 16:00.

**A discontinuous curve is not a return.** A deposit, withdrawal, or paper-account
reset moves equity without any strategy earning it. The score cannot tell those
apart from P&L, so a reset means a new deployment window, not a 100% drawdown.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from shrap.research.strategy_evaluator.engine import max_drawdown

# Trading sessions of live data before an annualised rate is meaningful.
# Roughly one month. A drawdown estimate from a handful of sessions describes
# the sample, not the strategy. This is a calibration Mike owns; it is a floor
# on *reporting a rate*, never on reporting growth.
DEFAULT_MIN_SESSIONS_FOR_RATE = 20

TRADING_DAYS_PER_YEAR = 252

REASON_TOO_FEW_SAMPLES = "fewer than two equity samples — nothing to measure yet"
REASON_NO_DRAWDOWN = (
    "no drawdown observed yet, so growth-over-drawdown is undefined. Reported as "
    "no score rather than an infinite one: a strategy that has not been tested by "
    "a loss has not earned the top of the leaderboard"
)


@dataclass(frozen=True, slots=True)
class EquitySample:
    """One observation of an account's equity, as the broker reported it."""

    at: datetime
    equity: float


@dataclass(frozen=True, slots=True)
class ForwardScore:
    """What a strategy's own account says about it.

    ``growth`` and ``max_drawdown`` are always populated when there is enough
    data to compute them at all. ``score`` and ``annualised_growth`` are
    ``None`` whenever producing them would overstate what the sample supports,
    with ``reason`` saying which case applied.
    """

    growth: float
    """Cumulative return since deployment, as a fraction. -0.05 is down 5%."""

    max_drawdown: float
    """Worst peak-to-trough decline over the window, as a positive fraction."""

    score: float | None
    """growth / max_drawdown. Negative when the account is down — which is
    meaningful and rankable, not an error."""

    annualised_growth: float | None
    """Only once the window supports it. Never extrapolated from a short run."""

    samples: int
    sessions: int
    """Distinct calendar dates observed — the honest unit for "how long"."""

    reason: str = ""
    """Non-empty when ``score`` is None."""

    @property
    def is_scored(self) -> bool:
        return self.score is not None


def _sessions(samples: Sequence[EquitySample]) -> int:
    return len({s.at.date() for s in samples})


def score_account(
    samples: Sequence[EquitySample],
    *,
    min_sessions_for_rate: int = DEFAULT_MIN_SESSIONS_FOR_RATE,
) -> ForwardScore:
    """Score one account's equity curve since deployment.

    ``samples`` must already be sliced to the deployment window and ordered by
    time; the caller owns that, because "since deployment" is a fact about the
    strategy's lifecycle rather than about the curve. A paper-account reset is a
    new window, not a drawdown.
    """

    n = len(samples)
    if n < 2:
        return ForwardScore(
            growth=0.0,
            max_drawdown=0.0,
            score=None,
            annualised_growth=None,
            samples=n,
            sessions=_sessions(samples),
            reason=REASON_TOO_FEW_SAMPLES,
        )

    equity = [s.equity for s in samples]
    start, end = equity[0], equity[-1]
    if start <= 0.0:
        raise ValueError(
            f"deployment equity is {start}, which cannot be a starting book. "
            "Refusing to compute a growth rate against it."
        )

    growth = end / start - 1.0
    drawdown = max_drawdown(equity)
    sessions = _sessions(samples)

    annualised: float | None = None
    if sessions >= min_sessions_for_rate and growth > -1.0:
        # Compound the realised per-session rate out to a year. Guarded on the
        # session floor above: this is the number that lies loudest on a short
        # sample.
        annualised = (1.0 + growth) ** (TRADING_DAYS_PER_YEAR / sessions) - 1.0

    if drawdown <= 0.0:
        return ForwardScore(
            growth=growth,
            max_drawdown=0.0,
            score=None,
            annualised_growth=annualised,
            samples=n,
            sessions=sessions,
            reason=REASON_NO_DRAWDOWN,
        )

    return ForwardScore(
        growth=growth,
        max_drawdown=drawdown,
        score=growth / drawdown,
        annualised_growth=annualised,
        samples=n,
        sessions=sessions,
    )


def rank_accounts(scores: dict[str, ForwardScore]) -> list[tuple[str, ForwardScore]]:
    """Order accounts best-first. Unscored accounts sort last, never first.

    Three accounts starting from the same $10,000 on the same day is the firm's
    first honest leaderboard. An account with no score yet — too short, or no
    drawdown to divide by — is not evidence of quality, so it goes to the bottom
    rather than being treated as a zero or as a win.
    """

    return sorted(
        scores.items(),
        key=lambda kv: (kv[1].score is not None, kv[1].score or 0.0),
        reverse=True,
    )


__all__ = [
    "DEFAULT_MIN_SESSIONS_FOR_RATE",
    "REASON_NO_DRAWDOWN",
    "REASON_TOO_FEW_SAMPLES",
    "TRADING_DAYS_PER_YEAR",
    "EquitySample",
    "ForwardScore",
    "rank_accounts",
    "score_account",
]
