"""Continuous monitoring — the part that would have caught the 53.88%.

Two limits are watched here rather than on the order path, because neither is a
property of any single order:

**Daily loss.** Equity change since the session's first snapshot. A breach sets
``daily_loss`` and halts new intents firm-wide.

**Strategy drawdown.** Peak-to-trough on the account's equity curve since
deployment. A breach sets ``strategy:<id>`` and halts that strategy alone.

The drawdown measure deliberately matches ``research/forward_score.py``, which
already scores deployed strategies on growth-over-drawdown from the same
``ops.account_snapshots`` series. Two different drawdown numbers for one account
— one that scores a strategy and one that halts it — would be a reporting bug
waiting to be discovered during an incident.

**Both limits compare against a peak, not against a starting value.** An account
that doubles and then halves is at breakeven on deposits and in a 50% drawdown,
and only the second describes the risk being run.

Everything here is a pure function of an equity series. No Redis, no Postgres,
no clock — the caller supplies observations and gets breaches back, so the
thresholds can be tested without infrastructure.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from shrap.risk_compliance.risk_officer.limits import PortfolioLimits

SEVERITY_INFO = "info"
SEVERITY_WARN = "warn"
SEVERITY_BREACH = "breach"

# A breach is reported at this fraction of the limit as a warning first, so an
# account approaching a halt is visible before it halts.
WARN_FRACTION = 0.75

LIMIT_DAILY_LOSS = "daily_loss"
LIMIT_STRATEGY_DRAWDOWN = "strategy_drawdown"


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """One account equity observation."""

    at: datetime
    equity: float


@dataclass(frozen=True, slots=True)
class LimitObservation:
    """What a limit currently reads, and whether that is a problem."""

    limit: str
    observed: float
    """The measured magnitude, as a positive fraction (0.12 == 12% down)."""
    threshold: float
    severity: str
    account_id: str | None = None
    strategy_id: str | None = None

    @property
    def is_breach(self) -> bool:
        return self.severity == SEVERITY_BREACH

    @property
    def is_warning(self) -> bool:
        return self.severity == SEVERITY_WARN

    def to_payload(self) -> dict[str, object]:
        return {
            "limit": self.limit,
            "observed": self.observed,
            "threshold": self.threshold,
            "severity": self.severity,
            "account_id": self.account_id,
            "strategy_id": self.strategy_id,
        }


def _severity(observed: float, threshold: float) -> str:
    if observed >= threshold:
        return SEVERITY_BREACH
    if observed >= threshold * WARN_FRACTION:
        return SEVERITY_WARN
    return SEVERITY_INFO


def session_loss(points: Sequence[EquityPoint], session: date) -> float | None:
    """Fractional loss since the session's first observation, or ``None``.

    Positive means down. A gain returns 0.0 rather than a negative loss, so
    callers never have to reason about the sign.

    ``None`` when the session has fewer than two observations — one point is not
    a change, and treating a single reading as a 0% move would silently report
    "no loss" for an account nobody has measured twice.
    """

    todays = [p for p in points if p.at.date() == session]
    if len(todays) < 2:
        return None
    ordered = sorted(todays, key=lambda p: p.at)
    opening = ordered[0].equity
    if opening <= 0.0:
        return None
    latest = ordered[-1].equity
    return max(0.0, (opening - latest) / opening)


def peak_drawdown(points: Sequence[EquityPoint]) -> float | None:
    """Worst peak-to-trough fraction over the whole series, or ``None``.

    Matches `research/forward_score.py`: the peak is a running maximum, so a
    drawdown is measured from the highest equity actually *seen*, not from the
    starting balance.
    """

    if len(points) < 2:
        return None
    ordered = sorted(points, key=lambda p: p.at)
    peak = ordered[0].equity
    worst = 0.0
    for point in ordered[1:]:
        if point.equity > peak:
            peak = point.equity
            continue
        if peak > 0.0:
            worst = max(worst, (peak - point.equity) / peak)
    return worst


def check_daily_loss(
    points: Sequence[EquityPoint],
    session: date,
    limits: PortfolioLimits,
    *,
    account_id: str | None = None,
) -> LimitObservation | None:
    """Observe the daily loss limit for one account. ``None`` when unmeasurable."""

    loss = session_loss(points, session)
    if loss is None:
        return None
    return LimitObservation(
        limit=LIMIT_DAILY_LOSS,
        observed=loss,
        threshold=limits.max_daily_loss,
        severity=_severity(loss, limits.max_daily_loss),
        account_id=account_id,
    )


def check_strategy_drawdown(
    points: Sequence[EquityPoint],
    limits: PortfolioLimits,
    *,
    strategy_id: str,
    account_id: str | None = None,
) -> LimitObservation | None:
    """Observe the drawdown limit for one strategy's account."""

    drawdown = peak_drawdown(points)
    if drawdown is None:
        return None
    return LimitObservation(
        limit=LIMIT_STRATEGY_DRAWDOWN,
        observed=drawdown,
        threshold=limits.max_strategy_drawdown,
        severity=_severity(drawdown, limits.max_strategy_drawdown),
        account_id=account_id,
        strategy_id=strategy_id,
    )


__all__ = [
    "LIMIT_DAILY_LOSS",
    "LIMIT_STRATEGY_DRAWDOWN",
    "SEVERITY_BREACH",
    "SEVERITY_INFO",
    "SEVERITY_WARN",
    "WARN_FRACTION",
    "EquityPoint",
    "LimitObservation",
    "check_daily_loss",
    "check_strategy_drawdown",
    "peak_drawdown",
    "session_loss",
]
