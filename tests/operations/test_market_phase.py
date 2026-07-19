"""Tests for the market-phase boundary computation (pure logic).

Dates are real XNYS calendar days in 2026; expected UTC instants are written
out by hand so a DST or calendar regression fails loudly.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from shrap.common.envelope import Envelope
from shrap.operations.market_phase import Phase, PhaseSchedule, Transition, build_schedule

_ET = ZoneInfo("America/New_York")


def _on_day(schedule: PhaseSchedule, day: date) -> list[Transition]:
    """Transitions whose boundary falls on exchange-local calendar day ``day``."""
    return [t for t in schedule.transitions if t.at.astimezone(_ET).date() == day]


def test_regular_day_boundaries() -> None:
    # Wednesday 2026-07-15, EDT (UTC-4).
    s = build_schedule(date(2026, 7, 13), date(2026, 7, 17))
    day = _on_day(s, date(2026, 7, 15))
    assert [(t.phase, t.at) for t in day] == [
        (Phase.PRE_OPEN, datetime(2026, 7, 15, 8, 0, tzinfo=UTC)),
        (Phase.OPEN, datetime(2026, 7, 15, 13, 30, tzinfo=UTC)),
        (Phase.AFTER_HOURS, datetime(2026, 7, 15, 20, 0, tzinfo=UTC)),
        (Phase.OVERNIGHT, datetime(2026, 7, 16, 0, 0, tzinfo=UTC)),
    ]
    assert all(t.session_date == date(2026, 7, 15) for t in day[:3])
    assert day[3].session_date == date(2026, 7, 16)  # overnight prepares the next session
    assert not any(t.is_early_close for t in day)


def test_half_day_early_close() -> None:
    # Friday 2026-11-27 (day after Thanksgiving) closes 13:00 ET; EST (UTC-5).
    s = build_schedule(date(2026, 11, 23), date(2026, 11, 30))
    friday = _on_day(s, date(2026, 11, 27))
    phases = {t.phase: t for t in friday}
    assert phases[Phase.AFTER_HOURS].at == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
    assert phases[Phase.OPEN].at == datetime(2026, 11, 27, 14, 30, tzinfo=UTC)
    assert all(t.is_early_close for t in friday if t.session_date == date(2026, 11, 27))
    # Thanksgiving Thursday is a closed-day pointing at the half-day session.
    thursday = _on_day(s, date(2026, 11, 26))
    assert [t.phase for t in thursday] == [Phase.CLOSED_DAY]
    assert thursday[0].session_date == date(2026, 11, 27)
    assert thursday[0].is_early_close


def test_holiday_closed_day() -> None:
    # Independence Day 2026 falls on a Saturday; observed Friday 2026-07-03.
    s = build_schedule(date(2026, 7, 1), date(2026, 7, 6))
    friday = _on_day(s, date(2026, 7, 3))
    assert [t.phase for t in friday] == [Phase.CLOSED_DAY]
    assert friday[0].at == datetime(2026, 7, 3, 8, 0, tzinfo=UTC)
    assert friday[0].session_date == date(2026, 7, 6)


def test_weekend_closed_days_point_at_monday() -> None:
    s = build_schedule(date(2026, 7, 17), date(2026, 7, 20))
    # Friday 20:00 ET -> overnight into Saturday 00:00 UTC, aimed at Monday.
    overnight = next(t for t in s.transitions if t.phase == Phase.OVERNIGHT)
    assert overnight.at == datetime(2026, 7, 18, 0, 0, tzinfo=UTC)
    assert overnight.session_date == date(2026, 7, 20)
    for day in (date(2026, 7, 18), date(2026, 7, 19)):
        closed = _on_day(s, day)
        assert [t.phase for t in closed] == [Phase.CLOSED_DAY]
        assert closed[0].at.time().isoformat() == "08:00:00"
        assert closed[0].session_date == date(2026, 7, 20)
    monday = _on_day(s, date(2026, 7, 20))
    assert monday[0].phase == Phase.PRE_OPEN
    assert monday[0].at == datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def test_dst_spring_forward_moves_utc_boundaries() -> None:
    # Clocks spring forward Sunday 2026-03-08: 09:30 ET is 14:30 UTC before,
    # 13:30 UTC after.
    s = build_schedule(date(2026, 3, 5), date(2026, 3, 10))
    friday_open = next(t for t in _on_day(s, date(2026, 3, 6)) if t.phase == Phase.OPEN)
    monday_open = next(t for t in _on_day(s, date(2026, 3, 9)) if t.phase == Phase.OPEN)
    assert friday_open.at == datetime(2026, 3, 6, 14, 30, tzinfo=UTC)
    assert monday_open.at == datetime(2026, 3, 9, 13, 30, tzinfo=UTC)


def test_dst_fall_back_moves_utc_boundaries() -> None:
    # Clocks fall back Sunday 2026-11-01.
    s = build_schedule(date(2026, 10, 29), date(2026, 11, 3))
    friday_open = next(t for t in _on_day(s, date(2026, 10, 30)) if t.phase == Phase.OPEN)
    monday_open = next(t for t in _on_day(s, date(2026, 11, 2)) if t.phase == Phase.OPEN)
    assert friday_open.at == datetime(2026, 10, 30, 13, 30, tzinfo=UTC)
    assert monday_open.at == datetime(2026, 11, 2, 14, 30, tzinfo=UTC)


def test_current_phase_mid_session_and_mid_overnight() -> None:
    s = build_schedule(date(2026, 7, 13), date(2026, 7, 17))
    mid_session = s.current(datetime(2026, 7, 15, 15, 0, tzinfo=UTC))
    assert mid_session.phase == Phase.OPEN
    assert mid_session.session_date == date(2026, 7, 15)
    mid_overnight = s.current(datetime(2026, 7, 16, 2, 0, tzinfo=UTC))
    assert mid_overnight.phase == Phase.OVERNIGHT
    assert mid_overnight.session_date == date(2026, 7, 16)


def test_transitions_are_ordered_and_contiguous() -> None:
    s = build_schedule(date(2026, 7, 1), date(2026, 7, 31))
    ats = [t.at for t in s.transitions]
    assert ats == sorted(ats)
    assert len(set(ats)) == len(ats)
    legal = {
        (Phase.PRE_OPEN, Phase.OPEN),
        (Phase.OPEN, Phase.AFTER_HOURS),
        (Phase.AFTER_HOURS, Phase.OVERNIGHT),
        (Phase.OVERNIGHT, Phase.PRE_OPEN),
        (Phase.OVERNIGHT, Phase.CLOSED_DAY),
        (Phase.CLOSED_DAY, Phase.PRE_OPEN),
        (Phase.CLOSED_DAY, Phase.CLOSED_DAY),
    }
    for prev, nxt in zip(s.transitions, s.transitions[1:], strict=False):
        assert (prev.phase, nxt.phase) in legal, (prev, nxt)


def test_due_and_next_after() -> None:
    s = build_schedule(date(2026, 7, 13), date(2026, 7, 17))
    after = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)  # pre-open boundary
    now = datetime(2026, 7, 15, 21, 0, tzinfo=UTC)
    assert [t.phase for t in s.due(after, now)] == [Phase.OPEN, Phase.AFTER_HOURS]
    upcoming = s.next_after(now)
    assert upcoming is not None
    assert upcoming.phase == Phase.OVERNIGHT
    assert upcoming.at == datetime(2026, 7, 16, 0, 0, tzinfo=UTC)


def test_payload_conforms_to_envelope() -> None:
    s = build_schedule(date(2026, 7, 13), date(2026, 7, 17))
    open_t = next(t for t in _on_day(s, date(2026, 7, 15)) if t.phase == Phase.OPEN)
    payload = s.payload_for(open_t, reason="transition")
    assert payload == {
        "phase": "open",
        "previous_phase": "pre-open",
        "session_date": "2026-07-15",
        "next_phase": "after-hours",
        "next_transition_at": "2026-07-15T20:00:00+00:00",
        "calendar": "XNYS",
        "is_early_close": False,
        "effective_at": "2026-07-15T13:30:00+00:00",
        "reason": "transition",
    }
    env = Envelope.new(
        produced_by="market-phase@test",
        schema_version="1.0.0",
        payload=payload,
    )
    assert Envelope.from_redis_fields(env.to_redis_fields()) == env
