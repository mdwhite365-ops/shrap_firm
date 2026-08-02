"""How often a strategy is allowed to act within one session.

Until now the Runner made exactly one decision per strategy per session: the
pass fired on entry to ``open`` and the idempotency guard was
``(strategy_id, session_date)``. Both are daily-bar assumptions, and ADR-0016's
intraday equities path needs neither (timeline 2.9).

**The dangerous change here is not the intraday one.** Making the Runner wake
more often is easy; the risk is that every strategy already in the registry
starts trading on every wake. Twelve daily strategies at a five-minute cadence
is 78 decisions a day each, against a book sized for one. So:

    Absence of a declared cadence means DAILY.

A strategy spec with no ``cadence`` key behaves exactly as it did before this
module existed, and nothing that is running today changes behaviour when the
Runner's interval firing is switched on. Intraday is opt-in, per strategy, in
the spec the Evaluator already persists.

**The slot is what makes this work with the existing guard.** Rather than
teaching the Runner which strategies to include in which pass, each strategy
computes its own *slot* — the identifier of the decision point it is currently
in. A daily strategy's slot is the constant :data:`SESSION_SLOT`, so once it is
stamped for the session every later pass sees "already ran" and skips it,
however often the Runner wakes. An intraday strategy's slot changes every
``interval_minutes``, so the same guard lets it through exactly once per
interval. No filtering, no second code path, and no way for the two to disagree.

Frequency stays a *capability* rather than a quota (Mike, 2026-07-29): a
strategy that declines to act at a slot is correct and costs one skipped plan.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

CADENCE_DAILY = "daily"
CADENCE_INTRADAY = "intraday"
CADENCES = (CADENCE_DAILY, CADENCE_INTRADAY)

# The slot every daily strategy occupies for a whole session. A literal rather
# than an empty string so a row read out of the database says what it means.
SESSION_SLOT = "session"

# Floor for a declared interval. One minute is the finest bar Alpaca offers, so
# anything shorter would re-decide on data that has not moved.
MIN_INTERVAL_MINUTES = 1

# Ceiling. Beyond a session's length an "intraday" cadence is a daily one with
# extra steps, and declaring it that way hides the intent.
MAX_INTERVAL_MINUTES = 390

DEFAULT_INTERVAL_MINUTES = 5


@dataclass(frozen=True, slots=True)
class Cadence:
    """How often one strategy may act within a session."""

    kind: str
    interval_minutes: int | None = None

    @property
    def is_intraday(self) -> bool:
        return self.kind == CADENCE_INTRADAY


DAILY = Cadence(kind=CADENCE_DAILY)


def read_cadence(spec: Mapping[str, Any] | None) -> Cadence:
    """Read a strategy's cadence from its spec, defaulting to daily.

    Deliberately total: any spec this cannot make sense of — missing key, wrong
    type, unknown kind, out-of-range interval — resolves to :data:`DAILY` rather
    than raising. A malformed cadence must not be able to stop a strategy
    trading, and it must not be able to make one trade *more* than it does
    today. Both failure directions land on the conservative answer.

    The consequence worth stating: a typo like ``"intrday"`` silently trades
    daily. That is the right trade against the alternative, where a typo in the
    other direction would put a daily rule on a five-minute loop.
    """

    if not isinstance(spec, Mapping):
        return DAILY
    raw = spec.get("cadence")
    kind: str
    interval: int | None
    if isinstance(raw, str):
        kind, interval = raw, DEFAULT_INTERVAL_MINUTES
    elif isinstance(raw, Mapping):
        kind = str(raw.get("kind", ""))
        interval = _coerce_interval(raw.get("interval_minutes"))
    else:
        return DAILY
    if kind != CADENCE_INTRADAY:
        return DAILY
    if interval is None:
        return DAILY
    return Cadence(kind=CADENCE_INTRADAY, interval_minutes=interval)


def _coerce_interval(value: object) -> int | None:
    if value is None:
        return DEFAULT_INTERVAL_MINUTES
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    minutes = int(value)
    if minutes < MIN_INTERVAL_MINUTES or minutes > MAX_INTERVAL_MINUTES:
        return None
    return minutes


def slot_for(cadence: Cadence, now: datetime) -> str:
    """The decision slot ``now`` falls in, for a strategy on ``cadence``.

    Daily strategies always return :data:`SESSION_SLOT`. Intraday strategies
    return the UTC minute-of-day floored to their interval, rendered ``HH:MM``.

    Floored rather than derived from a running counter so the slot is a pure
    function of the clock: a Runner restart mid-session recomputes the same slot
    for the same minute and the guard still holds. A counter would reset on
    restart and let every intraday strategy act a second time in one interval.
    """

    if not cadence.is_intraday or cadence.interval_minutes is None:
        return SESSION_SLOT
    minutes = now.hour * 60 + now.minute
    floored = (minutes // cadence.interval_minutes) * cadence.interval_minutes
    return f"{floored // 60:02d}:{floored % 60:02d}"


__all__ = [
    "CADENCES",
    "CADENCE_DAILY",
    "CADENCE_INTRADAY",
    "DAILY",
    "DEFAULT_INTERVAL_MINUTES",
    "MAX_INTERVAL_MINUTES",
    "MIN_INTERVAL_MINUTES",
    "SESSION_SLOT",
    "Cadence",
    "read_cadence",
    "slot_for",
]
