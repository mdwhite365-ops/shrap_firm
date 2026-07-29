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
from typing import Any, Protocol

from shrap.research.strategy_runner.engine import PlannedStateWrite, TargetState

CREATE_RESEARCH_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS research"

CREATE_RUNNER_STATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.strategy_runner_state (
    strategy_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    last_target DOUBLE PRECISION NOT NULL,
    last_side TEXT,
    last_session_date DATE NOT NULL,
    last_quantity INTEGER NOT NULL DEFAULT 0,
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
ADD COLUMN IF NOT EXISTS last_quantity INTEGER NOT NULL DEFAULT 1
""".strip()

SELECT_RUNNER_STATE_SQL = """
SELECT strategy_id, ticker, last_target, last_side, last_session_date, last_quantity
FROM research.strategy_runner_state
""".strip()

UPSERT_RUNNER_STATE_SQL = """
INSERT INTO research.strategy_runner_state (
    strategy_id, ticker, last_target, last_side, last_session_date, last_quantity, updated_at
)
VALUES ($1, $2, $3, $4, $5, $6, now())
ON CONFLICT (strategy_id, ticker) DO UPDATE SET
    last_target = EXCLUDED.last_target,
    last_side = EXCLUDED.last_side,
    last_session_date = EXCLUDED.last_session_date,
    last_quantity = EXCLUDED.last_quantity,
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
            state[(strategy_id, ticker)] = TargetState(
                last_target=float(row["last_target"]),
                last_side=None if last_side is None else str(last_side),
                last_session_date=_as_date(row["last_session_date"]),
                last_quantity=0 if last_quantity is None else int(last_quantity),
            )
        return state

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
            )


def _as_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value)
    return None


__all__ = [
    "ALTER_RUNNER_STATE_ADD_QUANTITY_SQL",
    "CREATE_RUNNER_STATE_TABLE_SQL",
    "SELECT_LATEST_EQUITY_SQL",
    "SELECT_RUNNER_STATE_SQL",
    "UPSERT_RUNNER_STATE_SQL",
    "AsyncPool",
    "PostgresStrategyRunnerStateStore",
]
