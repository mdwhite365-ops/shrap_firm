"""Market phase computation — deterministic exchange-calendar boundaries.

Pure logic, no I/O. The Market Phase Scheduler agent
(``shrap.agents.operations.market_phase``) wraps this in a service loop that
publishes phase transitions to the bus.

Phase model (five states over the exchange-local trading day):

    pre-open     04:00 ET -> market open (09:30 ET on regular days)
    open         market open -> market close (16:00 ET, earlier on half-days)
    after-hours  market close -> 20:00 ET
    overnight    20:00 ET -> 04:00 ET next calendar day
    closed-day   a full non-session day (weekend/holiday), entered at what
                 would have been pre-open (04:00 ET)

``session_date`` semantics: for pre-open/open/after-hours, the session day
itself. For overnight and closed-day — phases that prepare for the next
session — the *upcoming* session date, so consumers can distinguish "market
opens in three hours" from "market opens Monday". ``is_early_close`` always
refers to the ``session_date`` session.

The exchange calendar comes from pandas-market-calendars (XNYS by default),
which owns holiday and half-day rules. Boundary datetimes are constructed in
the exchange timezone and converted to UTC, so DST transitions are handled by
zoneinfo rather than by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

DEFAULT_CALENDAR = "XNYS"
DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_PRE_OPEN = time(4, 0)
DEFAULT_EXTENDED_END = time(20, 0)

# NYSE never closes for more than a long weekend; two weeks of lookahead
# always contains the next session.
_NEXT_SESSION_MARGIN_DAYS = 14


class Phase:
    """Phase names as published on the bus."""

    PRE_OPEN = "pre-open"
    OPEN = "open"
    AFTER_HOURS = "after-hours"
    OVERNIGHT = "overnight"
    CLOSED_DAY = "closed-day"


@dataclass(frozen=True, slots=True)
class Transition:
    """Entering ``phase`` at ``at`` (UTC)."""

    at: datetime
    phase: str
    session_date: date
    is_early_close: bool


@dataclass(frozen=True, slots=True)
class PhaseSchedule:
    """Ordered phase transitions over a window, with payload construction."""

    calendar: str
    transitions: tuple[Transition, ...]

    def current(self, now: datetime) -> Transition:
        """The transition most recently entered as of ``now``."""
        current: Transition | None = None
        for transition in self.transitions:
            if transition.at <= now:
                current = transition
            else:
                break
        if current is None:
            raise ValueError(f"schedule window starts after now={now.isoformat()}")
        return current

    def due(self, after: datetime, now: datetime) -> list[Transition]:
        """Transitions with ``after < at <= now``, oldest first."""
        return [t for t in self.transitions if after < t.at <= now]

    def next_after(self, at: datetime) -> Transition | None:
        for transition in self.transitions:
            if transition.at > at:
                return transition
        return None

    def payload_for(self, transition: Transition, reason: str) -> dict[str, Any]:
        """Event payload for entering ``transition``.

        ``effective_at`` is the true boundary time; a publish delayed by a
        Redis outage still carries it, so consumers can distinguish a late
        publish from a late phase (the envelope's ``produced_at`` is the
        publish time).
        """
        idx = self.transitions.index(transition)
        prev = self.transitions[idx - 1] if idx > 0 else None
        nxt = self.transitions[idx + 1] if idx + 1 < len(self.transitions) else None
        return {
            "phase": transition.phase,
            "previous_phase": prev.phase if prev else None,
            "session_date": transition.session_date.isoformat(),
            "next_phase": nxt.phase if nxt else None,
            "next_transition_at": nxt.at.isoformat() if nxt else None,
            "calendar": self.calendar,
            "is_early_close": transition.is_early_close,
            "effective_at": transition.at.isoformat(),
            "reason": reason,
        }


def build_schedule(
    start: date,
    end: date,
    *,
    calendar_name: str = DEFAULT_CALENDAR,
    timezone_name: str = DEFAULT_TIMEZONE,
    pre_open: time = DEFAULT_PRE_OPEN,
    extended_end: time = DEFAULT_EXTENDED_END,
) -> PhaseSchedule:
    """Compute every phase transition on calendar days ``start`` through ``end``."""

    tz = ZoneInfo(timezone_name)
    calendar = mcal.get_calendar(calendar_name)
    frame = calendar.schedule(
        start_date=start.isoformat(),
        end_date=(end + timedelta(days=_NEXT_SESSION_MARGIN_DAYS)).isoformat(),
    )
    regular_close: time = calendar.close_time.replace(tzinfo=None)

    session_dates: list[date] = []
    opens: dict[date, datetime] = {}
    closes: dict[date, datetime] = {}
    for stamp, row in frame.iterrows():
        session = stamp.date()
        session_dates.append(session)
        opens[session] = row["market_open"].to_pydatetime().astimezone(UTC)
        closes[session] = row["market_close"].to_pydatetime().astimezone(UTC)

    def next_session(after: date) -> date:
        for session in session_dates:
            if session > after:
                return session
        raise ValueError(f"no session within {_NEXT_SESSION_MARGIN_DAYS} days after {after}")

    def at_local(day: date, boundary: time) -> datetime:
        return datetime.combine(day, boundary, tzinfo=tz).astimezone(UTC)

    def is_early(session: date) -> bool:
        return closes[session].astimezone(tz).time() != regular_close

    transitions: list[Transition] = []
    day = start
    while day <= end:
        if day in opens:
            early = is_early(day)
            upcoming = next_session(day)
            transitions.append(Transition(at_local(day, pre_open), Phase.PRE_OPEN, day, early))
            transitions.append(Transition(opens[day], Phase.OPEN, day, early))
            transitions.append(Transition(closes[day], Phase.AFTER_HOURS, day, early))
            transitions.append(
                Transition(
                    at_local(day, extended_end), Phase.OVERNIGHT, upcoming, is_early(upcoming)
                )
            )
        else:
            upcoming = next_session(day)
            transitions.append(
                Transition(at_local(day, pre_open), Phase.CLOSED_DAY, upcoming, is_early(upcoming))
            )
        day += timedelta(days=1)

    transitions.sort(key=lambda t: t.at)
    return PhaseSchedule(calendar=calendar_name, transitions=tuple(transitions))
