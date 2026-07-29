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

SELECT_LATEST_ORDER_STATES_SINCE_SQL = """
SELECT DISTINCT ON (broker_order_id)
    broker,
    broker_order_id,
    status,
    symbol,
    filled_quantity
FROM trading.paper_order_events
WHERE broker = $1
  AND broker_order_id IN (
    SELECT broker_order_id
    FROM trading.paper_order_events
    WHERE broker = $1
    GROUP BY broker_order_id
    HAVING min(occurred_at) >= $2
  )
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

    async def latest_order_states(
        self, broker: str, since: object | None = None
    ) -> list[StoredOrderState]:
        async with self._pool.acquire() as conn:
            if since is None:
                rows = await conn.fetch(SELECT_LATEST_ORDER_STATES_SQL, broker)
            else:
                rows = await conn.fetch(SELECT_LATEST_ORDER_STATES_SINCE_SQL, broker, since)
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
    account_id TEXT,
    account_status TEXT,
    currency TEXT,
    cash DOUBLE PRECISION,
    equity DOUBLE PRECISION,
    buying_power DOUBLE PRECISION,
    portfolio_value DOUBLE PRECISION
)
""".strip()

# ADR-0017 puts one strategy in each of three broker accounts. Until now this
# table carried only ``broker``, so three accounts would write indistinguishable
# rows and a reader taking the most recent one would get whichever account
# happened to report last.
#
# Nullable, deliberately. Rows written before this column existed have no
# account identity and none can be invented for them — a fabricated id is
# exactly the wrong answer here. Readers match on ``account_id``, so legacy rows
# are simply never selected and age out.
ALTER_ACCOUNT_SNAPSHOTS_ADD_ACCOUNT_ID_SQL = """
ALTER TABLE ops.account_snapshots
ADD COLUMN IF NOT EXISTS account_id TEXT
""".strip()

CREATE_ACCOUNT_SNAPSHOTS_AT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS account_snapshots_at_idx ON ops.account_snapshots (at DESC)
""".strip()

# The Runner's lookup is "latest equity for THIS account", so the index leads on
# account_id. The at-only index above still serves whole-firm history queries.
CREATE_ACCOUNT_SNAPSHOTS_ACCOUNT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS account_snapshots_account_at_idx
ON ops.account_snapshots (account_id, at DESC)
""".strip()

INSERT_ACCOUNT_SNAPSHOT_SQL = """
INSERT INTO ops.account_snapshots (
    event_id, broker, account_id, account_status, currency,
    cash, equity, buying_power, portfolio_value
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
ON CONFLICT (event_id) DO NOTHING
""".strip()

# The broker's own field for the account, so identity comes from the venue
# rather than from a config value someone could mistype or copy between
# containers. Alpaca returns it on GET /v2/account.
BROKER_ACCOUNT_ID_FIELD = "account_number"


class UnidentifiedAccountError(Exception):
    """A snapshot arrived with no broker account id, so it cannot be attributed.

    Recording it anyway would be worse than dropping it: the Strategy Runner
    reads the newest snapshot to size positions, so an unattributed row is a
    position sized against an unknown book. Fail closed here and let the Runner's
    own staleness refusal stop trading.
    """


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
            await conn.execute(ALTER_ACCOUNT_SNAPSHOTS_ADD_ACCOUNT_ID_SQL)
            await conn.execute(CREATE_ACCOUNT_SNAPSHOTS_AT_INDEX_SQL)
            await conn.execute(CREATE_ACCOUNT_SNAPSHOTS_ACCOUNT_INDEX_SQL)

    async def record(self, event_id: str, broker: str, account: dict[str, Any]) -> None:
        """Persist one account snapshot, attributed to its broker account.

        Raises :class:`UnidentifiedAccountError` when the broker payload carries
        no account id. The caller logs and continues — order reconciliation does
        not depend on this write, and a missing snapshot makes the Runner refuse
        to size, which is the correct end state.
        """

        account_id = _optional_str(account.get(BROKER_ACCOUNT_ID_FIELD))
        if not account_id:
            raise UnidentifiedAccountError(
                f"broker payload has no {BROKER_ACCOUNT_ID_FIELD!r}, so this snapshot "
                f"cannot be attributed to an account (keys present: {sorted(account)}). "
                "Refusing to write an unattributed equity row — the Strategy Runner "
                "sizes positions from the newest snapshot."
            )
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_ACCOUNT_SNAPSHOT_SQL,
                event_id,
                broker,
                account_id,
                str(account.get("status")) if account.get("status") is not None else None,
                str(account.get("currency")) if account.get("currency") is not None else None,
                _float_or_none(account.get("cash")),
                _float_or_none(account.get("equity")),
                _float_or_none(account.get("buying_power")),
                _float_or_none(account.get("portfolio_value")),
            )
