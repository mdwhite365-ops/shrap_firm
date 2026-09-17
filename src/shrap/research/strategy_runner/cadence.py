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

# One regular NYSE session, 09:30-16:00 ET. The bar-count arithmetic below and
# the interval ceiling are the same fact, so it is named once.
REGULAR_SESSION_MINUTES = 390

# Ceiling. Beyond a session's length an "intraday" cadence is a daily one with
# extra steps, and declaring it that way hides the intent.
MAX_INTERVAL_MINUTES = REGULAR_SESSION_MINUTES

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


def bars_per_session(cadence: Cadence) -> int:
    """How many bars of this cadence's grain one regular session contains.

    One for a daily strategy; ``390 // interval`` for an intraday one. Floored,
    so a cadence that does not divide the session evenly reports the number of
    *whole* bars — asking for one more than exists is how a warmup window comes
    up a session short.
    """

    if not cadence.is_intraday or cadence.interval_minutes is None:
        return 1
    return max(REGULAR_SESSION_MINUTES // cadence.interval_minutes, 1)


def sessions_for_warmup(cadence: Cadence, warmup_bars: int) -> int:
    """Trading sessions needed to supply ``warmup_bars`` bars at this cadence.

    **This is the conversion whose absence would have been expensive.** A
    strategy's ``warmup`` is counted in *bars*, and the daily path could treat
    bars and sessions as the same unit because at a daily grain they are. At
    five minutes a 200-bar warmup is under three sessions, but read as 200
    sessions it spans a calendar year — and a year of 5-minute bars for fifty
    names is roughly 24.6 million rows, fetched to compute a signal that needed
    three days of them.

    It would not have raised. It would have been slow, then slower as the
    universe grew.
    """

    per_session = bars_per_session(cadence)
    needed = max(warmup_bars, 1)
    return -(-needed // per_session)  # ceil, without importing math for one call


def alpaca_timeframe(cadence: Cadence) -> str:
    """The Alpaca timeframe token for this cadence's bar grain.

    Matches ``market_data.intraday_bars.timeframe``, which stores the same
    token the client requested, so this is also the value to filter reads on.
    """

    if not cadence.is_intraday or cadence.interval_minutes is None:
        return "1Day"
    return f"{cadence.interval_minutes}Min"


__all__ = [
    "CADENCES",
    "CADENCE_DAILY",
    "CADENCE_INTRADAY",
    "DAILY",
    "DEFAULT_INTERVAL_MINUTES",
    "MAX_INTERVAL_MINUTES",
    "MIN_INTERVAL_MINUTES",
    "REGULAR_SESSION_MINUTES",
    "SESSION_SLOT",
    "Cadence",
    "alpaca_timeframe",
    "bars_per_session",
    "read_cadence",
    "sessions_for_warmup",
    "slot_for",
]
