"""Latched kill switches: is the firm halted, and has it been halted too long?

**Why this exists.** In late October the Risk Officer's ``daily_loss`` switch
tripped and never cleared. Nothing in the code cleared it, so for over a week
every new buy on all three accounts was refused while sells flowed, each
rebalance became sell-only, and the books drained to ~30% invested. Every
component was healthy by its own measure. The Health Monitor watched Redis,
Postgres and the output tables, and the output tables were filling: the Risk
Officer was writing ``KILL_SWITCH_ACTIVE`` refusals on schedule. Nothing asked
whether the firm was *allowed to trade*.

This module asks. It reads the newest row per switch from
``risk.kill_switches`` (the authority; Redis is a cache of it) and classifies
every switch it has ever seen, so a switch that clears produces an ``ok`` and
the Health Monitor's state machine records the recovery.

``daily_loss`` active past a session boundary
    ``down``. The Risk Officer clears it at the first sweep of a new session,
    so surviving the boundary means that clear did not run. This is the
    October failure, and it halts the whole firm.

``manual`` active past a session boundary
    ``degraded``. Mike set it on purpose, so it is not a fault, but a halt that
    outlives the day it was set should be a decision someone keeps making, not
    one that was forgotten.

``strategy:<id>`` active past a session boundary
    ``degraded``. Drawdown switches never auto-clear by design (see
    :meth:`RiskOfficer.sweep`); they wait for a human ruling. This makes the
    wait visible.

Sessions are UTC dates, the convention the Risk Officer already uses. A switch
counts as latched only once ``grace`` has passed since the first boundary after
it was set, so the minutes between midnight and the Risk Officer's next sweep
never read as a fault.

What this cannot see: a halt that is not a switch. Regime caps, cluster caps or
an empty approval stream can stop trading just as completely. The firm-health
report (``scripts/firm_health.sql``) covers those from the order side.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Protocol

Status = str  # "ok" | "degraded" | "down", the Health Monitor's convention.

SWITCH_DAILY_LOSS = "daily_loss"
SWITCH_MANUAL = "manual"
STRATEGY_PREFIX = "strategy:"

DEFAULT_GRACE = timedelta(hours=1)
"""The Risk Officer sweeps every five minutes; an hour is twelve missed sweeps.
Longer than any legitimate gap, short enough that a latch is caught before the
next open."""

SELECT_LATEST_SWITCHES_SQL = """
SELECT DISTINCT ON (switch) switch, active, actor, reason, at
FROM risk.kill_switches
ORDER BY switch, id DESC
""".strip()


@dataclass(frozen=True, slots=True)
class SwitchRow:
    name: str
    active: bool
    actor: str
    reason: str
    at: datetime


@dataclass(frozen=True, slots=True)
class LatchVerdict:
    switch: str
    status: Status
    reason: str
    active: bool
    set_at: datetime
    actor: str
    switch_reason: str
    latched_hours: float | None

    def evidence(self) -> dict[str, Any]:
        return {
            "switch": self.switch,
            "active": self.active,
            "set_at": self.set_at.isoformat(),
            "actor": self.actor,
            "switch_reason": self.switch_reason,
            "latched_hours": None if self.latched_hours is None else round(self.latched_hours, 1),
            "reason": self.reason,
        }


class SwitchLatchStore(Protocol):
    async def latest(self) -> Sequence[SwitchRow]: ...


class PostgresSwitchLatchStore:
    """Reads ``risk.kill_switches``. Reader, never owner: the Risk Officer owns it."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def latest(self) -> Sequence[SwitchRow]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_LATEST_SWITCHES_SQL)
        return tuple(
            SwitchRow(
                name=str(r["switch"]),
                active=bool(r["active"]),
                actor=str(r["actor"]),
                reason=str(r["reason"]),
                at=r["at"],
            )
            for r in rows
        )


def _utc(at: datetime) -> datetime:
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


def first_boundary_after(at: datetime) -> datetime:
    """Midnight UTC following ``at``: when the session it was set in ended."""

    set_on = _utc(at).date()
    return datetime.combine(set_on + timedelta(days=1), time(0, 0), tzinfo=UTC)


def classify(row: SwitchRow, now: datetime, grace: timedelta = DEFAULT_GRACE) -> LatchVerdict:
    set_at = _utc(row.at)
    now_utc = _utc(now)

    def verdict(status: Status, reason: str, hours: float | None = None) -> LatchVerdict:
        return LatchVerdict(
            switch=row.name,
            status=status,
            reason=reason,
            active=row.active,
            set_at=set_at,
            actor=row.actor,
            switch_reason=row.reason,
            latched_hours=hours,
        )

    if not row.active:
        return verdict("ok", "inactive")

    boundary = first_boundary_after(set_at)
    if now_utc < boundary + grace:
        return verdict("ok", "active, set this session")

    hours = (now_utc - boundary).total_seconds() / 3600.0
    if row.name == SWITCH_DAILY_LOSS:
        return verdict(
            "down",
            "daily_loss survived a session boundary: the Risk Officer's session clear did "
            "not run, and every new buy firm-wide is being refused",
            hours,
        )
    if row.name == SWITCH_MANUAL:
        return verdict("degraded", "manual halt still active from an earlier session", hours)
    if row.name.startswith(STRATEGY_PREFIX):
        return verdict(
            "degraded",
            "strategy drawdown switch awaiting a ruling; it never clears on its own",
            hours,
        )
    return verdict("degraded", "unrecognised switch active from an earlier session", hours)


async def sweep(
    store: SwitchLatchStore, now: datetime, grace: timedelta = DEFAULT_GRACE
) -> list[LatchVerdict]:
    return [classify(row, now, grace) for row in await store.latest()]


__all__ = [
    "DEFAULT_GRACE",
    "SELECT_LATEST_SWITCHES_SQL",
    "LatchVerdict",
    "PostgresSwitchLatchStore",
    "SwitchLatchStore",
    "SwitchRow",
    "classify",
    "first_boundary_after",
    "sweep",
]
