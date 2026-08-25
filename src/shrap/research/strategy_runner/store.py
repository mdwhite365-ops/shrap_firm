"""PostgreSQL store for the paper-strategy runner's per-target state.

One row per ``(strategy_id, ticker)`` recording the runner's last intended
target and the session it was last stamped in. This is the runner's *only*
persistent state: it owns its intended flat/invested target, not actual
positions (those remain the Reconciliation Agent's job).

The ``last_session_date`` column is the per-strategy idempotency guard — a pass
stamps every ticker it processes with the session date, so a re-delivered /
startup / catch-up market-phase event, or a restart mid-session, never triggers
a second emit (see :mod:`shrap.research.strategy_runner.engine`).

Schema and table creation follow the house ensure-schema pattern
(``CREATE ... IF NOT EXISTS`` at startup — see the market-data and evaluator
stores). The upsert is idempotent: a repeated ``(strategy_id, ticker)``
overwrites the prior target rather than inserting a duplicate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Protocol, cast

from shrap.research.strategy_runner.cadence import SESSION_SLOT
from shrap.research.strategy_runner.engine import PlannedStateWrite, TargetState

CREATE_RESEARCH_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS research"

CREATE_RUNNER_STATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.strategy_runner_state (
    strategy_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    last_target DOUBLE PRECISION NOT NULL,
    last_side TEXT,
    last_session_date DATE NOT NULL,
    last_quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (strategy_id, ticker)
)
""".strip()

# The table already exists in production, where CREATE TABLE IF NOT EXISTS is a
# no-op — so the column needs its own migration. The default is 1, not 0: every
# row written before sizing existed was opened by the fixed-1-share path, so 1 is
# what those positions actually hold. A default of 0 would strand them, because
# an exit sells the recorded quantity.
ALTER_RUNNER_STATE_ADD_QUANTITY_SQL = """
ALTER TABLE research.strategy_runner_state
ADD COLUMN IF NOT EXISTS last_quantity DOUBLE PRECISION NOT NULL DEFAULT 1
""".strip()

# The column shipped as INTEGER, when a quantity was a whole number of shares.
# #195 made quantities fractional and Postgres kept accepting the writes by
# ROUNDING them, exactly as `risk.decisions` did until #199 — a 0.0126-share
# position recorded as 0. Guarded on the current type so it is a no-op on a
# database that already carries the wider one, and so re-running it is free.
#
# Nothing sizes from this column any more (#192 made exits read the broker's
# position instead), so the impact was confined to the audit trail. It is fixed
# because a column that disagrees with the book is how three of the last five
# trading defects started.
ALTER_RUNNER_STATE_FRACTIONAL_QUANTITY_SQL = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'research' AND table_name = 'strategy_runner_state'
          AND column_name = 'last_quantity' AND data_type = 'integer') THEN
        ALTER TABLE research.strategy_runner_state
            ALTER COLUMN last_quantity TYPE DOUBLE PRECISION;
    END IF;
END $$
""".strip()

# Same reasoning as the quantity migration: the table exists in production, so
# CREATE TABLE IF NOT EXISTS will not add this. The default is `session` — every
# row written before cadence existed was stamped by a once-a-day pass, and
# `session` is exactly what that pass meant. Any other default would make those
# rows compare unequal to the slot a daily strategy computes today, and every
# one of them would trade a second time on the first pass after deploy.
ALTER_RUNNER_STATE_ADD_SLOT_SQL = """
ALTER TABLE research.strategy_runner_state
ADD COLUMN IF NOT EXISTS last_slot TEXT NOT NULL DEFAULT 'session'
""".strip()

SELECT_RUNNER_STATE_SQL = """
SELECT strategy_id, ticker, last_target, last_side, last_session_date,
       last_quantity, last_slot
FROM research.strategy_runner_state
""".strip()

UPSERT_RUNNER_STATE_SQL = """
INSERT INTO research.strategy_runner_state (
    strategy_id, ticker, last_target, last_side, last_session_date,
    last_quantity, last_slot, updated_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, now())
ON CONFLICT (strategy_id, ticker) DO UPDATE SET
    last_target = EXCLUDED.last_target,
    last_side = EXCLUDED.last_side,
    last_session_date = EXCLUDED.last_session_date,
    last_quantity = EXCLUDED.last_quantity,
    last_slot = EXCLUDED.last_slot,
    updated_at = now()
""".strip()


# Read-only over a table the Reconciliation Agent owns and writes every pass.
# The Runner needs its own account size to convert target weights into share
# counts, and ADR-0003 keeps broker credentials inside broker-facing containers
# only — so it reads the persisted snapshot rather than becoming one of them.
# Scoped to one account. ADR-0017 puts one strategy in each of three broker
# accounts, so an unscoped "newest snapshot" would return whichever account
# reported most recently and size this strategy against a book that is not its
# own. The account_id match also excludes rows written before the column existed:
# they carry NULL and can never satisfy it, which is the intended behaviour —
# a legacy row's account is genuinely unknown.
SELECT_LATEST_EQUITY_SQL = """
SELECT equity, at
FROM ops.account_snapshots
WHERE account_id = $1
  AND equity IS NOT NULL
ORDER BY at DESC
LIMIT 1
""".strip()


# The sentinel the Reconciliation Agent writes on every pass, including a pass
# that found nothing. Without it a flat account and a dead agent are the same
# absence of rows, and those must produce opposite behaviour here: trade, or
# refuse to.
FLAT_MARKER_TICKER = "__FLAT__"

# Positions are read as the newest PASS, not the newest row per ticker. A
# per-ticker latest can mix two passes and report a position the newer pass
# shows as closed — which is precisely the class of error this whole card
# exists to remove.
SELECT_LATEST_POSITIONS_SQL = """
WITH newest AS (
    SELECT event_id, at
    FROM ops.position_snapshots
    WHERE account_id = $1
    ORDER BY at DESC
    LIMIT 1
)
SELECT p.ticker, p.quantity, n.at
FROM ops.position_snapshots p
JOIN newest n ON n.event_id = p.event_id
WHERE p.account_id = $1
""".strip()


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetchrow(self, sql: str, *args: object) -> Mapping[str, Any] | None: ...

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


class PostgresStrategyRunnerStateStore:
    """Owner of ``research.strategy_runner_state``: read all, idempotent upsert."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_RESEARCH_SCHEMA_SQL)
            await conn.execute(CREATE_RUNNER_STATE_TABLE_SQL)
            await conn.execute(ALTER_RUNNER_STATE_ADD_QUANTITY_SQL)
            await conn.execute(ALTER_RUNNER_STATE_FRACTIONAL_QUANTITY_SQL)
            await conn.execute(ALTER_RUNNER_STATE_ADD_SLOT_SQL)

    async def read_state(self) -> dict[tuple[str, str], TargetState]:
        """Read every stored target, keyed by ``(strategy_id, ticker)``.

        The table holds one row per active-paper (strategy, ticker), so a full
        read is cheap and avoids array-binding a per-strategy id list.
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_RUNNER_STATE_SQL)
        state: dict[tuple[str, str], TargetState] = {}
        for row in rows:
            strategy_id = str(row["strategy_id"])
            ticker = str(row["ticker"])
            last_side = row["last_side"]
            last_quantity = row["last_quantity"]
            last_slot = row["last_slot"]
            state[(strategy_id, ticker)] = TargetState(
                last_target=float(row["last_target"]),
                last_side=None if last_side is None else str(last_side),
                last_session_date=_as_date(row["last_session_date"]),
                # float(), not int(). The column is DOUBLE PRECISION and
                # TargetState declares a float; casting here would put the
                # narrowing back one layer down from where it was removed.
                last_quantity=0.0 if last_quantity is None else float(last_quantity),
                last_slot=SESSION_SLOT if last_slot is None else str(last_slot),
            )
        return state

    async def latest_positions(self, account_id: str) -> tuple[dict[str, float], datetime | None]:
        """What ``account_id`` actually holds, per the newest reconciliation pass.

        Returns ``({}, None)`` when no pass has ever run for this account — the
        caller must refuse to plan, because "no rows" cannot be read as "flat".
        Returns ``({}, at)`` for a genuinely flat account: the pass ran, wrote
        its marker, and found nothing.

        The marker row is excluded from the mapping but is what makes the
        timestamp trustworthy. Tickers with a zero quantity are dropped too — a
        closed position is not a held one, and leaving it in would make an exit
        fire against nothing, which is the bug this method exists to end.
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_LATEST_POSITIONS_SQL, account_id)
        if not rows:
            return {}, None
        observed_at = cast(datetime, rows[0]["at"])
        held: dict[str, float] = {}
        for row in rows:
            ticker = str(row["ticker"])
            if ticker == FLAT_MARKER_TICKER:
                continue
            quantity = float(row["quantity"])
            if quantity != 0.0:
                held[ticker.upper()] = quantity
        return held, observed_at

    async def latest_equity(self, account_id: str) -> tuple[float | None, datetime | None]:
        """Most recent equity for ``account_id``, and when it was observed.

        ``account_id`` is required and has no default. Under ADR-0017 each
        strategy trades its own broker account, and sizing against the wrong
        one is silent — the number is plausible, just not this book's.

        Returns ``(None, None)`` when that account has no snapshot — the caller
        refuses to size rather than defaulting, because an unknown account size
        is worse than not trading (see ``sizing.assert_equity_usable``).

        A missing ``ops.account_snapshots`` table is an infrastructure fault
        surfaced to the caller, not papered over here: the Reconciliation Agent
        owns that table and this store never creates it.
        """

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_LATEST_EQUITY_SQL, account_id)
        if row is None:
            return None, None
        equity = row["equity"]
        at = row["at"]
        return (
            float(equity) if isinstance(equity, (int, float)) else None,
            at if isinstance(at, datetime) else None,
        )

    async def upsert(self, write: PlannedStateWrite) -> None:
        """Persist one ``(strategy_id, ticker)`` target; idempotent on the PK."""

        async with self._pool.acquire() as conn:
            await conn.execute(
                UPSERT_RUNNER_STATE_SQL,
                write.strategy_id,
                write.ticker,
                write.last_target,
                write.last_side,
                write.last_session_date,
                write.last_quantity,
                write.last_slot,
            )


def _as_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value)
    return None


__all__ = [
    "ALTER_RUNNER_STATE_ADD_QUANTITY_SQL",
    "ALTER_RUNNER_STATE_ADD_SLOT_SQL",
    "ALTER_RUNNER_STATE_FRACTIONAL_QUANTITY_SQL",
    "CREATE_RUNNER_STATE_TABLE_SQL",
    "SELECT_LATEST_EQUITY_SQL",
    "SELECT_RUNNER_STATE_SQL",
    "UPSERT_RUNNER_STATE_SQL",
    "AsyncPool",
    "PostgresStrategyRunnerStateStore",
]
