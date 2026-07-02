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
