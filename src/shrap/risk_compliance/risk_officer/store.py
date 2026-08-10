"""Persistence for the Risk Officer.

Owns two tables and reads four others.

**Owns** — created and migrated here:

``risk.decisions``       every approval, scale-down and veto, append-only
``risk.kill_switches``   every switch transition, append-only

**Reads, never writes** — each has another owner, and the tier-3 gate's rule
applies to all of them: a reader that creates a missing table papers over an
infrastructure fault and then answers confidently from an empty one.

``ops.position_snapshots``  Reconciliation Agent — the book
``ops.account_snapshots``   Reconciliation Agent — NAV and the equity curve
``market_data.daily_bars``  market-data backfill — prices and correlation history

Kill-switch *state* lives in Redis (``risk:switches``) because the order path
reads it on every intent and Postgres is not on that path. Postgres holds the
*history*. The two can disagree only by losing a write, and the recovery is to
rebuild the hash from the append-only log — which is why the log carries the
full state on every row rather than a delta.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

import structlog

from shrap.agents.operations.reconciliation_agent.db import FLAT_MARKER_TICKER
from shrap.risk_compliance.risk_officer.exposure import Position
from shrap.risk_compliance.risk_officer.monitor import EquityPoint
from shrap.risk_compliance.risk_officer.switches import SwitchState

log = structlog.get_logger(__name__)

CREATE_RISK_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS risk"

CREATE_DECISIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS risk.decisions (
    event_id TEXT PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    intent_event_id TEXT,
    account_id TEXT,
    ticker TEXT,
    side TEXT,
    approved BOOLEAN NOT NULL,
    reason_code TEXT NOT NULL,
    requested_quantity INTEGER,
    approved_quantity INTEGER,
    binding_limit TEXT,
    strategy_ids JSONB,
    detail JSONB
)
""".strip()

CREATE_DECISIONS_AT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS risk_decisions_at_idx ON risk.decisions (at DESC)
""".strip()

# Vetoes are the forensically interesting rows and a small fraction of the
# total, so they get their own partial index.
CREATE_DECISIONS_VETO_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS risk_decisions_veto_idx
ON risk.decisions (at DESC) WHERE NOT approved
""".strip()

CREATE_KILL_SWITCHES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS risk.kill_switches (
    id BIGSERIAL PRIMARY KEY,
    switch TEXT NOT NULL,
    active BOOLEAN NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip()

CREATE_KILL_SWITCHES_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS risk_kill_switches_switch_idx
ON risk.kill_switches (switch, id DESC)
""".strip()

INSERT_DECISION_SQL = """
INSERT INTO risk.decisions (
    event_id, intent_event_id, account_id, ticker, side, approved, reason_code,
    requested_quantity, approved_quantity, binding_limit, strategy_ids, detail
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
ON CONFLICT (event_id) DO NOTHING
""".strip()

INSERT_SWITCH_SQL = """
INSERT INTO risk.kill_switches (switch, active, actor, reason, at)
VALUES ($1, $2, $3, $4, $5)
""".strip()

# The newest row per switch. DISTINCT ON is the Postgres idiom and keeps the
# rebuild a single query rather than one per switch.
SELECT_SWITCH_STATE_SQL = """
SELECT DISTINCT ON (switch) switch, active, actor, reason, at
FROM risk.kill_switches
ORDER BY switch, id DESC
""".strip()

# The newest *pass*, not the newest row per ticker. Keying on event_id keeps the
# result a single consistent snapshot: a per-ticker latest could mix two passes
# and report a position the newer pass shows as closed.
#
# The flat-marker row is returned rather than filtered in SQL, because it
# carries the timestamp that proves the pass happened. A flat account's snapshot
# is otherwise indistinguishable from no snapshot at all, and those two must
# lead to opposite behaviour. `latest_positions` drops it after reading `at`.
SELECT_LATEST_POSITIONS_SQL = """
SELECT ticker, quantity, market_value, at
FROM ops.position_snapshots
WHERE account_id = $1
  AND event_id = (
    SELECT event_id
    FROM ops.position_snapshots
    WHERE account_id = $1
    ORDER BY at DESC
    LIMIT 1
  )
""".strip()

SELECT_EQUITY_SERIES_SQL = """
SELECT at, equity
FROM ops.account_snapshots
WHERE account_id = $1 AND equity IS NOT NULL AND at >= $2
ORDER BY at
""".strip()

SELECT_LATEST_CLOSE_SQL = """
SELECT close
FROM market_data.daily_bars
WHERE ticker = $1
ORDER BY session_date DESC
LIMIT 1
""".strip()

SELECT_CLOSES_SQL = """
SELECT close
FROM (
    SELECT close, session_date
    FROM market_data.daily_bars
    WHERE ticker = $1
    ORDER BY session_date DESC
    LIMIT $2
) recent
ORDER BY session_date
""".strip()


class Connection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]: ...

    async def fetchrow(self, sql: str, *args: object) -> Mapping[str, Any] | None: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> Connection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class Pool(Protocol):
    def acquire(self) -> AcquireContext: ...


@dataclass(frozen=True, slots=True)
class DecisionRow:
    """One risk decision, as persisted."""

    event_id: str
    intent_event_id: str | None
    account_id: str | None
    ticker: str | None
    side: str | None
    approved: bool
    reason_code: str
    requested_quantity: float | None
    approved_quantity: float | None
    binding_limit: str | None
    strategy_ids: list[str]
    detail: dict[str, Any]


class RiskStore:
    """Postgres access for the Risk Officer."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        """Create the two tables this agent owns. Never creates the ones it reads."""

        async with self._pool.acquire() as conn:
            for statement in (
                CREATE_RISK_SCHEMA_SQL,
                CREATE_DECISIONS_TABLE_SQL,
                CREATE_DECISIONS_AT_INDEX_SQL,
                CREATE_DECISIONS_VETO_INDEX_SQL,
                CREATE_KILL_SWITCHES_TABLE_SQL,
                CREATE_KILL_SWITCHES_INDEX_SQL,
            ):
                await conn.execute(statement)

    # --- decisions ------------------------------------------------------------

    async def record_decision(self, row: DecisionRow) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_DECISION_SQL,
                row.event_id,
                row.intent_event_id,
                row.account_id,
                row.ticker,
                row.side,
                row.approved,
                row.reason_code,
                row.requested_quantity,
                row.approved_quantity,
                row.binding_limit,
                json.dumps(row.strategy_ids),
                json.dumps(row.detail),
            )

    # --- kill switches --------------------------------------------------------

    async def record_switch(self, state: SwitchState) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_SWITCH_SQL,
                state.name,
                state.active,
                state.actor,
                state.reason,
                state.at,
            )

    async def load_switch_states(self) -> tuple[SwitchState, ...]:
        """Rebuild current switch state from the append-only log."""

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_SWITCH_STATE_SQL)
        return tuple(
            SwitchState(
                name=str(row["switch"]),
                active=bool(row["active"]),
                actor=str(row["actor"]),
                reason=str(row["reason"]),
                at=row["at"],
            )
            for row in rows
        )

    # --- reads ----------------------------------------------------------------

    async def latest_positions(
        self, account_id: str
    ) -> tuple[tuple[Position, ...], datetime | None]:
        """The newest position snapshot for one account, and when it was taken.

        An empty tuple with a timestamp is a flat account. An empty tuple with
        ``None`` is an account nobody has measured — the caller must treat those
        differently (see ``exposure.assert_positions_usable``).

        The flat-marker row is dropped here, after its timestamp has been read.
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_LATEST_POSITIONS_SQL, account_id)
        if not rows:
            return (), None
        positions = tuple(
            Position(
                ticker=str(row["ticker"]).upper(),
                quantity=float(row["quantity"]),
                market_value=float(row["market_value"]),
            )
            for row in rows
            if str(row["ticker"]) != FLAT_MARKER_TICKER
        )
        return positions, rows[0]["at"]

    async def equity_series(self, account_id: str, since: date) -> tuple[EquityPoint, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_EQUITY_SERIES_SQL, account_id, since)
        return tuple(
            EquityPoint(at=row["at"], equity=float(row["equity"]))
            for row in rows
            if row["equity"] is not None
        )

    async def latest_close(self, ticker: str) -> float | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_LATEST_CLOSE_SQL, ticker.strip().upper())
        if row is None or row["close"] is None:
            return None
        return float(row["close"])

    async def closes(self, ticker: str, limit: int = 90) -> tuple[float, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_CLOSES_SQL, ticker.strip().upper(), limit)
        return tuple(float(row["close"]) for row in rows if row["close"] is not None)

    async def price_history(
        self, tickers: Sequence[str], limit: int = 90
    ) -> dict[str, tuple[float, ...]]:
        """Closes for several names, for the correlation clustering.

        A ticker with no bars is returned as an empty series rather than
        omitted, so the clusterer sees "no history" and merges it defensively
        instead of silently skipping it.
        """

        return {t.strip().upper(): await self.closes(t, limit) for t in tickers}


__all__ = [
    "CREATE_DECISIONS_TABLE_SQL",
    "CREATE_KILL_SWITCHES_TABLE_SQL",
    "CREATE_RISK_SCHEMA_SQL",
    "INSERT_DECISION_SQL",
    "INSERT_SWITCH_SQL",
    "SELECT_CLOSES_SQL",
    "SELECT_EQUITY_SERIES_SQL",
    "SELECT_LATEST_CLOSE_SQL",
    "SELECT_LATEST_POSITIONS_SQL",
    "SELECT_SWITCH_STATE_SQL",
    "AcquireContext",
    "Connection",
    "DecisionRow",
    "Pool",
    "RiskStore",
]
