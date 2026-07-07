"""Narrow read interface over trading.paper_order_events for reconciliation.

Reconciliation needs one thing from the order trail: the latest known state
per broker order. This module exposes exactly that and nothing else — no
writes, no event-level access.
"""

from __future__ import annotations

from typing import Any, Protocol

from shrap.agents.operations.reconciliation_agent.records import StoredOrderState

SELECT_LATEST_ORDER_STATES_SQL = """
SELECT DISTINCT ON (broker_order_id)
    broker,
    broker_order_id,
    status,
    symbol,
    filled_quantity
FROM trading.paper_order_events
WHERE broker = $1
ORDER BY broker_order_id, occurred_at DESC, recorded_at DESC
""".strip()


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetch(self, sql: str, *args: object) -> list[Any]: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


class PostgresOrderEventRepository:
    """Read-only view of the latest persisted state per broker order."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def latest_order_states(self, broker: str) -> list[StoredOrderState]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_LATEST_ORDER_STATES_SQL, broker)
        return [
            StoredOrderState(
                broker=str(row["broker"]),
                broker_order_id=str(row["broker_order_id"]),
                status=_optional_str(row["status"]),
                symbol=_optional_str(row["symbol"]),
                filled_quantity=_optional_str(row["filled_quantity"]),
            )
            for row in rows
        ]


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


CREATE_OPS_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS ops"

CREATE_ACCOUNT_SNAPSHOTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ops.account_snapshots (
    event_id TEXT PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    broker TEXT NOT NULL,
    account_status TEXT,
    currency TEXT,
    cash DOUBLE PRECISION,
    equity DOUBLE PRECISION,
    buying_power DOUBLE PRECISION,
    portfolio_value DOUBLE PRECISION
)
""".strip()

CREATE_ACCOUNT_SNAPSHOTS_AT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS account_snapshots_at_idx ON ops.account_snapshots (at DESC)
""".strip()

INSERT_ACCOUNT_SNAPSHOT_SQL = """
INSERT INTO ops.account_snapshots (
    event_id, broker, account_status, currency, cash, equity, buying_power, portfolio_value
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (event_id) DO NOTHING
""".strip()


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


class PostgresAccountSnapshotStore:
    """Append-only store: one broker account snapshot per reconciliation pass."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_OPS_SCHEMA_SQL)
            await conn.execute(CREATE_ACCOUNT_SNAPSHOTS_TABLE_SQL)
            await conn.execute(CREATE_ACCOUNT_SNAPSHOTS_AT_INDEX_SQL)

    async def record(self, event_id: str, broker: str, account: dict[str, Any]) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_ACCOUNT_SNAPSHOT_SQL,
                event_id,
                broker,
                str(account.get("status")) if account.get("status") is not None else None,
                str(account.get("currency")) if account.get("currency") is not None else None,
                _float_or_none(account.get("cash")),
                _float_or_none(account.get("equity")),
                _float_or_none(account.get("buying_power")),
                _float_or_none(account.get("portfolio_value")),
            )
