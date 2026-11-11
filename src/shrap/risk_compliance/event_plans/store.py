"""Postgres for event plans: two append-mostly tables in the ``risk`` schema.

``risk.event_plans`` holds what was written; ``risk.event_plan_activations``
holds each time a trigger plan fired. Neither has an ``active`` column. Whether
a plan applies is computed from these rows and the clock every time it is
asked (see :mod:`shrap.risk_compliance.event_plans.plans`), so a restart, a
missed sweep or a crashed container cannot leave a plan in force.

The only update either table ever receives is a cancel, which stamps
``cancelled_at`` and never deletes: the row is the audit trail.

Reads ``market_data.daily_bars`` for trigger observations. Reader, never owner.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from shrap.market_data.store import DEFAULT_BAR_SOURCE
from shrap.risk_compliance.event_plans.plans import (
    MAX_HOLD,
    Activation,
    DailyReturn,
    Effect,
    Plan,
    due_activations,
    effect_at,
    validate,
)

MARKET_TZ = ZoneInfo("America/New_York")
BAR_ADJUSTMENT = "all"

CREATE_RISK_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS risk"

# The CHECKs repeat `validate`. A row written past the validator (a hand
# INSERT, a future caller) must still be unable to raise exposure or to have no
# end.
CREATE_EVENT_PLANS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS risk.event_plans (
    plan_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    trigger TEXT NOT NULL,
    action TEXT NOT NULL,
    multiplier DOUBLE PRECISION NOT NULL CHECK (multiplier >= 0 AND multiplier <= 1),
    starts_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL CHECK (expires_at > starts_at),
    ticker TEXT,
    threshold DOUBLE PRECISION,
    hold_seconds DOUBLE PRECISION,
    reason TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    cancelled_at TIMESTAMPTZ,
    cancelled_by TEXT
)
""".strip()

CREATE_EVENT_PLAN_ACTIVATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS risk.event_plan_activations (
    plan_id TEXT NOT NULL,
    session_date DATE NOT NULL,
    activated_at TIMESTAMPTZ NOT NULL,
    until TIMESTAMPTZ NOT NULL CHECK (until > activated_at),
    observed DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (plan_id, session_date)
)
""".strip()

INSERT_PLAN_SQL = """
INSERT INTO risk.event_plans (
    plan_id, name, event_kind, trigger, action, multiplier, starts_at, expires_at,
    ticker, threshold, hold_seconds, reason, created_by
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
""".strip()

# Named columns (#260). Plans that can still matter: not yet expired, or expired
# recently enough that an activation could still be inside its hold.
SELECT_RELEVANT_PLANS_SQL = """
SELECT plan_id, name, event_kind, trigger, action, multiplier, starts_at, expires_at,
       ticker, threshold, hold_seconds, reason, created_by, cancelled_at, cancelled_by
FROM risk.event_plans
WHERE expires_at > $1
ORDER BY starts_at, plan_id
""".strip()

SELECT_ALL_PLANS_SQL = """
SELECT plan_id, name, event_kind, trigger, action, multiplier, starts_at, expires_at,
       ticker, threshold, hold_seconds, reason, created_by, cancelled_at, cancelled_by
FROM risk.event_plans
ORDER BY starts_at DESC, plan_id
LIMIT $1
""".strip()

SELECT_OPEN_ACTIVATIONS_SQL = """
SELECT plan_id, session_date, activated_at, until, observed
FROM risk.event_plan_activations
WHERE until > $1
ORDER BY activated_at
""".strip()

SELECT_PLAN_ACTIVATIONS_SQL = """
SELECT plan_id, session_date, activated_at, until, observed
FROM risk.event_plan_activations
WHERE plan_id = ANY($1::text[])
ORDER BY activated_at
""".strip()

# ON CONFLICT DO NOTHING is the idempotency guard: two sweeps seeing the same
# closed session write one row. RETURNING tells the caller which it was, so an
# activation is announced once.
INSERT_ACTIVATION_SQL = """
INSERT INTO risk.event_plan_activations (plan_id, session_date, activated_at, until, observed)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (plan_id, session_date) DO NOTHING
RETURNING plan_id
""".strip()

CANCEL_PLAN_SQL = """
UPDATE risk.event_plans
SET cancelled_at = $2, cancelled_by = $3
WHERE plan_id = $1 AND cancelled_at IS NULL
RETURNING plan_id
""".strip()

# `session_date < $4` keeps the session in progress out: `daily_bars` holds a
# partial bar for it (#279), and a return computed against a price that is not
# yet a close would fire a plan on a move that may not stand. `source` and
# `adjustment` are both pinned (#245): two feeds in one comparison is not a
# return.
SELECT_LAST_TWO_CLOSES_SQL = """
SELECT session_date, close
FROM market_data.daily_bars
WHERE ticker = $1 AND adjustment = $2 AND source = $3 AND session_date < $4
ORDER BY session_date DESC
LIMIT 2
""".strip()


class Connection(Protocol):
    async def execute(self, query: str, *args: Any) -> Any: ...
    async def fetch(self, query: str, *args: Any) -> list[Any]: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> Connection: ...
    async def __aexit__(self, *exc: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


def _plan(row: Any) -> Plan:
    hold = row["hold_seconds"]
    return Plan(
        plan_id=str(row["plan_id"]),
        name=str(row["name"]),
        event_kind=str(row["event_kind"]),
        trigger=str(row["trigger"]),
        action=str(row["action"]),
        multiplier=float(row["multiplier"]),
        starts_at=row["starts_at"],
        expires_at=row["expires_at"],
        reason=str(row["reason"]),
        created_by=str(row["created_by"]),
        ticker=None if row["ticker"] is None else str(row["ticker"]),
        threshold=None if row["threshold"] is None else float(row["threshold"]),
        hold=None if hold is None else timedelta(seconds=float(hold)),
        cancelled_at=row["cancelled_at"],
        cancelled_by=None if row["cancelled_by"] is None else str(row["cancelled_by"]),
    )


def _activation(row: Any) -> Activation:
    return Activation(
        plan_id=str(row["plan_id"]),
        session_date=row["session_date"],
        activated_at=row["activated_at"],
        until=row["until"],
        observed=float(row["observed"]),
    )


class EventPlanStore:
    """Plans, their activations, and the observations that fire them."""

    def __init__(self, pool: AsyncPool, *, source: str = DEFAULT_BAR_SOURCE) -> None:
        self._pool = pool
        self._source = source

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            for statement in (
                CREATE_RISK_SCHEMA_SQL,
                CREATE_EVENT_PLANS_TABLE_SQL,
                CREATE_EVENT_PLAN_ACTIVATIONS_TABLE_SQL,
            ):
                await conn.execute(statement)

    async def add(self, plan: Plan) -> Plan:
        checked = validate(plan)
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_PLAN_SQL,
                checked.plan_id,
                checked.name.strip(),
                checked.event_kind.strip(),
                checked.trigger,
                checked.action,
                checked.multiplier,
                checked.starts_at,
                checked.expires_at,
                None if checked.ticker is None else checked.ticker.strip().upper(),
                checked.threshold,
                None if checked.hold is None else checked.hold.total_seconds(),
                checked.reason.strip(),
                checked.created_by,
            )
        return checked

    async def cancel(self, plan_id: str, *, by: str, now: datetime) -> bool:
        """Cancel a plan. ``False`` if there was nothing live to cancel."""

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(CANCEL_PLAN_SQL, plan_id, now, by)
        return row is not None

    async def relevant(self, now: datetime) -> tuple[list[Plan], list[Activation]]:
        """Every plan and activation that could bear on ``now``.

        Plans are read back to ``now - MAX_HOLD`` rather than to ``now``: a
        trigger plan that expired yesterday can still have an activation inside
        its hold, and the activation is meaningless without its plan.
        """

        async with self._pool.acquire() as conn:
            plan_rows = await conn.fetch(SELECT_RELEVANT_PLANS_SQL, now - MAX_HOLD)
            activation_rows = await conn.fetch(SELECT_OPEN_ACTIVATIONS_SQL, now)
        return [_plan(r) for r in plan_rows], [_activation(r) for r in activation_rows]

    async def effect(self, now: datetime) -> Effect:
        """What is in force right now. One round trip; no cache.

        Not cached on purpose. The order path asks this on every buy, and a
        cached answer is stored state with a lifetime, which is the thing this
        package exists not to have.
        """

        plans, activations = await self.relevant(now)
        return effect_at(plans, activations, now)

    async def history(self, limit: int = 50) -> tuple[list[Plan], list[Activation]]:
        async with self._pool.acquire() as conn:
            plan_rows = await conn.fetch(SELECT_ALL_PLANS_SQL, limit)
            plans = [_plan(r) for r in plan_rows]
            activation_rows = await conn.fetch(
                SELECT_PLAN_ACTIVATIONS_SQL, [p.plan_id for p in plans]
            )
        return plans, [_activation(r) for r in activation_rows]

    async def record(self, activation: Activation) -> bool:
        """Store an activation. ``True`` only the first time it is seen."""

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                INSERT_ACTIVATION_SQL,
                activation.plan_id,
                activation.session_date,
                activation.activated_at,
                activation.until,
                activation.observed,
            )
        return row is not None

    async def last_completed_return(self, ticker: str, now: datetime) -> DailyReturn | None:
        """The latest *completed* session's return, or ``None`` without two closes."""

        today: date = now.astimezone(MARKET_TZ).date()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                SELECT_LAST_TWO_CLOSES_SQL,
                ticker.strip().upper(),
                BAR_ADJUSTMENT,
                self._source,
                today,
            )
        if len(rows) < 2:
            return None
        latest, prior = rows[0], rows[1]
        if not prior["close"] or float(prior["close"]) <= 0:
            return None
        return DailyReturn(
            ticker=ticker.strip().upper(),
            session_date=latest["session_date"],
            ret=float(latest["close"]) / float(prior["close"]) - 1.0,
        )


async def sweep_triggers(store: EventPlanStore, now: datetime) -> list[tuple[Plan, Activation]]:
    """Fire any trigger plan whose condition the last completed session met.

    Returns only the activations this call was first to record, so the caller
    announces each once. A ticker with no bars is skipped: the plan stays armed
    and does not fire, which is the safe direction for a control that only
    ever reduces buying, and the caller can see it in the plan listing.
    """

    plans, _ = await store.relevant(now)
    armed = [p for p in plans if not p.is_window and p.live(now) and p.ticker]
    if not armed:
        return []
    observations: dict[str, DailyReturn] = {}
    for ticker in sorted({(p.ticker or "").upper() for p in armed}):
        observation = await store.last_completed_return(ticker, now)
        if observation is not None:
            observations[ticker] = observation
    # `existing` is left empty and the table's primary key decides: a second
    # sweep's INSERT is a no-op and `record` returns False for it.
    by_id = {p.plan_id: p for p in armed}
    fired: list[tuple[Plan, Activation]] = []
    for activation in due_activations(armed, (), observations, now):
        if await store.record(activation):
            fired.append((by_id[activation.plan_id], activation))
    return fired


__all__ = [
    "BAR_ADJUSTMENT",
    "CANCEL_PLAN_SQL",
    "CREATE_EVENT_PLANS_TABLE_SQL",
    "CREATE_EVENT_PLAN_ACTIVATIONS_TABLE_SQL",
    "INSERT_ACTIVATION_SQL",
    "INSERT_PLAN_SQL",
    "SELECT_LAST_TWO_CLOSES_SQL",
    "EventPlanStore",
    "sweep_triggers",
]
