"""Event plans (ADR-0018 cards 3 to 5).

The property everything here leans on: a plan's effect is computed from the
plan rows and the clock, with no stored ``active`` flag. So the tests that
matter most are the ones showing a plan stops applying when nothing runs.
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from shrap.risk_compliance import pre_trade_checker_agent
from shrap.risk_compliance.event_plans import store as plan_store
from shrap.risk_compliance.event_plans.cli import (
    build_trigger_plan,
    build_window_plan,
    describe,
    parse_instant,
    render,
)
from shrap.risk_compliance.event_plans.plans import (
    ACTION_PAUSE_BUYS,
    ACTION_SCALE_BUYS,
    NO_EFFECT,
    TRIGGER_DAILY_RETURN,
    TRIGGER_WINDOW,
    Activation,
    DailyReturn,
    Plan,
    PlanError,
    due_activations,
    effect_at,
    triggered,
    validate,
)
from shrap.risk_compliance.event_plans.store import EventPlanStore, sweep_triggers

NOW = datetime(2026, 12, 9, 15, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


def _window(
    plan_id: str = "w1",
    *,
    multiplier: float = 0.5,
    start: datetime = NOW - HOUR,
    end: datetime = NOW + HOUR,
    pause: bool = False,
    **over: Any,
) -> Plan:
    fields: dict[str, Any] = {
        "plan_id": plan_id,
        "name": f"plan {plan_id}",
        "event_kind": "fed",
        "trigger": TRIGGER_WINDOW,
        "action": ACTION_PAUSE_BUYS if pause else ACTION_SCALE_BUYS,
        "multiplier": 0.0 if pause else multiplier,
        "starts_at": start,
        "expires_at": end,
        "reason": "a decision is due",
        "created_by": "human",
    }
    fields.update(over)
    return Plan(**fields)


def _shock(
    plan_id: str = "t1",
    *,
    threshold: float = -0.03,
    hold: timedelta = 2 * DAY,
    pause: bool = True,
    start: datetime = NOW - DAY,
    end: datetime = NOW + 30 * DAY,
    **over: Any,
) -> Plan:
    fields: dict[str, Any] = {
        "plan_id": plan_id,
        "name": f"plan {plan_id}",
        "event_kind": "shock",
        "trigger": TRIGGER_DAILY_RETURN,
        "action": ACTION_PAUSE_BUYS if pause else ACTION_SCALE_BUYS,
        "multiplier": 0.0 if pause else 0.5,
        "starts_at": start,
        "expires_at": end,
        "reason": "stand aside after a fall",
        "created_by": "human",
        "ticker": "SPY",
        "threshold": threshold,
        "hold": hold,
    }
    fields.update(over)
    return Plan(**fields)


def _fired(plan: Plan, at: datetime = NOW, session: date = date(2026, 12, 8)) -> Activation:
    assert plan.hold is not None
    return Activation(plan.plan_id, session, at, at + plan.hold, -0.04)


# --- a plan ends by the clock, not by anyone ending it -------------------------


def test_a_window_plan_applies_inside_its_window_and_not_one_instant_after() -> None:
    plan = _window(start=NOW - HOUR, end=NOW + HOUR)

    assert effect_at([plan], [], NOW).multiplier == 0.5
    assert effect_at([plan], [], NOW - HOUR).in_force  # start is inclusive
    assert effect_at([plan], [], NOW + HOUR) == NO_EFFECT  # end is exclusive
    assert effect_at([plan], [], NOW - 2 * HOUR) == NO_EFFECT


def test_an_expired_plan_has_no_effect_though_nothing_ever_cleared_it() -> None:
    """#291 and #293 were both a release nobody called. There is none here to
    call: the same untouched row simply stops being returned."""

    plan = _window(start=NOW - 3 * DAY, end=NOW - DAY)

    assert plan.cancelled_at is None
    assert effect_at([plan], [], NOW) == NO_EFFECT


def test_a_trigger_plan_applies_only_inside_an_activation() -> None:
    plan = _shock(hold=2 * DAY)
    activation = _fired(plan, at=NOW)

    assert effect_at([plan], [], NOW) == NO_EFFECT
    assert effect_at([plan], [activation], NOW + DAY).pause_buys
    assert effect_at([plan], [activation], NOW + 2 * DAY) == NO_EFFECT


def test_an_activation_runs_its_hold_even_after_the_plan_stops_being_armed() -> None:
    """Armed until tomorrow, fired today, holds two days. It was in force when
    it fired, and it finishes."""

    plan = _shock(end=NOW + 6 * HOUR, hold=2 * DAY)

    assert effect_at([plan], [_fired(plan, at=NOW)], NOW + DAY).pause_buys


def test_a_cancelled_plan_has_no_effect_even_inside_its_hold() -> None:
    """A cancel is a ruling. A plan that kept acting after one is #293 again."""

    cancelled = _shock(cancelled_at=NOW, cancelled_by="human")

    assert effect_at([cancelled], [_fired(cancelled, at=NOW - HOUR)], NOW + HOUR) == NO_EFFECT
    assert effect_at([_window(cancelled_at=NOW, cancelled_by="human")], [], NOW) == NO_EFFECT


def test_the_smallest_multiplier_wins_and_any_pause_pauses() -> None:
    half, third = _window("a", multiplier=0.5), _window("b", multiplier=0.3)

    both = effect_at([half, third], [], NOW)
    assert both.multiplier == 0.3 and not both.pause_buys
    assert both.plan_ids == ("b", "a")

    with_pause = effect_at([half, _window("c", pause=True)], [], NOW)
    assert with_pause.pause_buys and with_pause.multiplier == 0.0


# --- what may be stored --------------------------------------------------------


@pytest.mark.parametrize(
    ("plan", "message"),
    [
        (_window(multiplier=1.2), "outside \\[0, 1\\]"),
        (_window(multiplier=-0.1), "outside \\[0, 1\\]"),
        (_window(multiplier=1.0), "strictly between 0 and 1"),
        (_window(multiplier=0.0), "strictly between 0 and 1"),
        (_window(action=ACTION_PAUSE_BUYS), "multiplier 0"),
        (_window(reason="  "), "needs a reason"),
        (_window(name=""), "needs a name"),
        (_window(end=NOW - 2 * HOUR), "after starts_at"),
        (_window(start=NOW, end=NOW + 15 * DAY), "at most 14 days"),
        (_window(ticker="SPY"), "takes no ticker"),
        (_window(trigger="vibes"), "unknown trigger"),
        (_window(action="buy-more"), "unknown action"),
        (_shock(ticker=None), "needs the ticker"),
        (_shock(threshold=0.0), "non-zero threshold"),
        (_shock(threshold=-3.0), "as a fraction"),
        (_shock(hold=6 * DAY), "at most 5 days"),
        (_shock(hold=None), "hold must be positive"),
        (_shock(start=NOW, end=NOW + 400 * DAY), "at most 366 days"),
    ],
)
def test_a_plan_that_would_fail_quietly_is_refused(plan: Plan, message: str) -> None:
    with pytest.raises(PlanError, match=message):
        validate(plan)


def test_well_formed_plans_pass() -> None:
    assert validate(_window()).multiplier == 0.5
    assert validate(_window(pause=True)).pauses
    assert validate(_shock()).ticker == "SPY"


def test_the_table_repeats_the_two_checks_that_matter_most() -> None:
    """A row written past the validator must still be unable to raise exposure
    or to have no end."""

    assert "CHECK (multiplier >= 0 AND multiplier <= 1)" in plan_store.CREATE_EVENT_PLANS_TABLE_SQL
    assert "CHECK (expires_at > starts_at)" in plan_store.CREATE_EVENT_PLANS_TABLE_SQL
    assert "CHECK (until > activated_at)" in plan_store.CREATE_EVENT_PLAN_ACTIVATIONS_TABLE_SQL


# --- when a shock plan fires ---------------------------------------------------


def test_the_thresholds_sign_is_its_direction_and_equality_fires() -> None:
    fall, rise = _shock(threshold=-0.03), _shock(threshold=0.05, ticker="XLE")
    day = date(2026, 12, 8)

    assert triggered(fall, DailyReturn("SPY", day, -0.03))
    assert triggered(fall, DailyReturn("spy", day, -0.041))
    assert not triggered(fall, DailyReturn("SPY", day, -0.029))
    assert not triggered(fall, DailyReturn("SPY", day, 0.04))
    assert triggered(rise, DailyReturn("XLE", day, 0.05))
    assert not triggered(rise, DailyReturn("SPY", day, 0.09))


def test_an_activation_is_dated_from_when_the_firm_saw_it() -> None:
    plan = _shock(hold=2 * DAY)
    seen = {"SPY": DailyReturn("SPY", date(2026, 12, 8), -0.04)}

    [activation] = due_activations([plan], [], seen, NOW)

    assert (activation.activated_at, activation.until) == (NOW, NOW + 2 * DAY)
    assert activation.session_date == date(2026, 12, 8)


def test_one_session_fires_a_plan_once_however_often_it_is_swept() -> None:
    plan = _shock()
    seen = {"SPY": DailyReturn("SPY", date(2026, 12, 8), -0.04)}
    first = due_activations([plan], [], seen, NOW)

    assert due_activations([plan], first, seen, NOW + 5 * timedelta(minutes=1)) == []


def test_a_plan_outside_its_armed_life_or_cancelled_does_not_fire() -> None:
    seen = {"SPY": DailyReturn("SPY", date(2026, 12, 8), -0.04)}

    assert due_activations([_shock(end=NOW - HOUR)], [], seen, NOW) == []
    assert due_activations([_shock(start=NOW + HOUR)], [], seen, NOW) == []
    assert due_activations([_shock(cancelled_at=NOW - HOUR)], [], seen, NOW) == []
    assert due_activations([_window()], [], seen, NOW) == []


# --- the sweep ------------------------------------------------------------------


class FakePlanStore:
    def __init__(self, plans: list[Plan], returns: dict[str, DailyReturn]) -> None:
        self.plans = plans
        self.returns = returns
        self.recorded: dict[tuple[str, date], Activation] = {}
        self.asked: list[str] = []

    async def relevant(self, now: datetime) -> tuple[list[Plan], list[Activation]]:
        return self.plans, list(self.recorded.values())

    async def last_completed_return(self, ticker: str, now: datetime) -> DailyReturn | None:
        self.asked.append(ticker)
        return self.returns.get(ticker)

    async def record(self, activation: Activation) -> bool:
        key = (activation.plan_id, activation.session_date)
        if key in self.recorded:
            return False
        self.recorded[key] = activation
        return True


async def test_the_sweep_records_a_firing_once_and_reports_it_once() -> None:
    store = FakePlanStore([_shock()], {"SPY": DailyReturn("SPY", date(2026, 12, 8), -0.04)})

    first = await sweep_triggers(store, NOW)  # type: ignore[arg-type]
    second = await sweep_triggers(store, NOW + timedelta(minutes=5))  # type: ignore[arg-type]

    assert [(p.plan_id, a.observed) for p, a in first] == [("t1", -0.04)]
    assert second == []
    assert len(store.recorded) == 1


async def test_a_ticker_with_no_bars_leaves_the_plan_armed_and_unfired() -> None:
    store = FakePlanStore([_shock(ticker="USO")], {})

    assert await sweep_triggers(store, NOW) == []  # type: ignore[arg-type]
    assert store.asked == ["USO"]


async def test_the_sweep_reads_no_bars_when_nothing_is_armed() -> None:
    store = FakePlanStore([_window(), _shock(end=NOW - HOUR)], {})

    assert await sweep_triggers(store, NOW) == []  # type: ignore[arg-type]
    assert store.asked == []


class FakeRedis:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.sent.append((stream, fields))
        return "1-0"


async def test_an_activation_is_published_once() -> None:
    store = FakePlanStore([_shock()], {"SPY": DailyReturn("SPY", date(2026, 12, 8), -0.04)})
    redis = FakeRedis()

    fired = await pre_trade_checker_agent.event_plans_once(redis, store, now=NOW)  # type: ignore[arg-type]
    again = await pre_trade_checker_agent.event_plans_once(redis, store, now=NOW)  # type: ignore[arg-type]

    assert (fired, again) == (1, 0)
    assert [stream for stream, _ in redis.sent] == ["risk.event_plan.activated"]


# --- reading the bars -----------------------------------------------------------


class FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[Any, ...]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append((query, *args))
        return self.rows

    async def __aenter__(self) -> FakeConn:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def acquire(self) -> FakeConn:
        return self.conn


async def test_the_return_is_latest_over_prior_and_the_session_in_progress_is_excluded() -> None:
    """`daily_bars` holds a partial bar for today (#279). 01:00 UTC on the 12th
    is still the 11th in New York, so the bound is the 11th and that day's bar
    is not read."""

    conn = FakeConn(
        [
            {"session_date": date(2026, 11, 10), "close": 97.0},
            {"session_date": date(2026, 11, 9), "close": 100.0},
        ]
    )
    store = EventPlanStore(FakePool(conn), source="alpaca-iex")  # type: ignore[arg-type]

    observed = await store.last_completed_return(" spy ", datetime(2026, 11, 12, 1, 0, tzinfo=UTC))

    assert observed == DailyReturn("SPY", date(2026, 11, 10), pytest.approx(-0.03))
    _, ticker, adjustment, source, bound = conn.calls[0]
    assert (ticker, adjustment, source, bound) == ("SPY", "all", "alpaca-iex", date(2026, 11, 11))


async def test_one_close_is_not_a_return() -> None:
    conn = FakeConn([{"session_date": date(2026, 11, 10), "close": 97.0}])
    store = EventPlanStore(FakePool(conn))  # type: ignore[arg-type]

    assert await store.last_completed_return("SPY", NOW) is None


def test_the_bar_query_pins_feed_and_adjustment() -> None:
    """Two feeds in one comparison is not a return (#245)."""

    sql = plan_store.SELECT_LAST_TWO_CLOSES_SQL

    assert "source = $3" in sql and "adjustment = $2" in sql and "session_date < $4" in sql


# --- the command ----------------------------------------------------------------


def test_a_time_with_no_offset_is_refused() -> None:
    """ "16:00" typed in New York and read as UTC starts the plan hours early."""

    with pytest.raises(argparse.ArgumentTypeError, match="no UTC offset"):
        parse_instant("2026-12-09T16:00")

    assert parse_instant("2026-12-09T16:00-05:00") == datetime(2026, 12, 9, 21, 0, tzinfo=UTC)


def test_scale_and_pause_are_one_or_the_other() -> None:
    common: dict[str, Any] = {
        "name": "FOMC",
        "kind": "fed",
        "start": NOW,
        "end": NOW + DAY,
        "reason": "decision day",
    }

    assert build_window_plan(scale=0.5, pause=False, **common).multiplier == 0.5
    assert build_window_plan(scale=None, pause=True, **common).pauses
    with pytest.raises(PlanError, match="not both"):
        build_window_plan(scale=0.5, pause=True, **common)
    with pytest.raises(PlanError, match="--scale"):
        build_window_plan(scale=None, pause=False, **common)


def test_a_trigger_plan_is_armed_from_now() -> None:
    plan = build_trigger_plan(
        name="SPY down 3%",
        kind="shock",
        ticker=" spy ",
        threshold=-0.03,
        hold_hours=48,
        armed_days=180,
        scale=None,
        pause=True,
        reason="stand aside",
        now=NOW,
    )

    assert (plan.ticker, plan.starts_at, plan.expires_at) == ("SPY", NOW, NOW + 180 * DAY)
    assert "for 48h after SPY falls 3.0% or more" in describe(plan)


def test_the_listing_leads_with_what_is_in_force() -> None:
    scaled = render([_window()], [], NOW)
    paused = render([_shock()], [_fired(_shock(), at=NOW - HOUR)], NOW)
    idle = render([_window(start=NOW + DAY, end=NOW + 2 * DAY)], [], NOW)

    assert scaled.startswith("IN FORCE NOW: new buys scaled to 50% by plan w1")
    assert paused.startswith("IN FORCE NOW: new buys are PAUSED by plan t1")
    assert "fired on 2026-12-08 (-4.0%)" in paused
    assert idle.startswith("Nothing in force now.") and "[scheduled]" in idle


def test_the_listing_names_each_plans_state() -> None:
    text = render(
        [
            _window("old", start=NOW - 3 * DAY, end=NOW - DAY),
            _shock("armed"),
            _window("gone", cancelled_at=NOW - HOUR, cancelled_by="human"),
        ],
        [],
        NOW,
    )

    assert "[ended] plan old" in text
    assert "[armed] plan armed" in text
    assert "[cancelled 2026-12-09 by human] plan gone" in text
    assert render([], [], NOW).endswith("No plans stored.")
