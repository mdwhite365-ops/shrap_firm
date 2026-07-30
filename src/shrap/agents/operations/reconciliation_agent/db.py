"""Narrow read interface over trading.paper_order_events for reconciliation.

Reconciliation needs one thing from the order trail: the latest known state
per broker order. This module exposes exactly that and nothing else — no
writes, no event-level access.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from shrap.agents.operations.reconciliation_agent.records import BrokerPosition, StoredOrderState

# Scoped to one account as well as one broker. ADR-0017 puts three accounts on
# `alpaca-paper`, and each Reconciliation Agent compares against ONE account's
# broker orders — so a broker-only filter would report every other account's
# orders as missing at the broker, on every pass.
#
# `account_id = $2` also excludes rows written before the column existed: they
# carry NULL and can never match, which is right because their account is
# genuinely unknown and guessing would manufacture false discrepancies.
SELECT_LATEST_ORDER_STATES_SQL = """
SELECT DISTINCT ON (broker_order_id)
    broker,
    broker_order_id,
    status,
    symbol,
    filled_quantity
FROM trading.paper_order_events
WHERE broker = $1
  AND account_id = $2
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
  AND account_id = $2
  AND broker_order_id IN (
    SELECT broker_order_id
    FROM trading.paper_order_events
    WHERE broker = $1
      AND account_id = $2
    GROUP BY broker_order_id
    HAVING min(occurred_at) >= $3
  )
ORDER BY broker_order_id, occurred_at DESC, recorded_at DESC
""".strip()


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetch(self, sql: str, *args: object) -> list[Any]: ...

    def transaction(self) -> AbstractAsyncContextManager[object]: ...


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
        self, broker: str, account_id: str, since: object | None = None
    ) -> list[StoredOrderState]:
        """Latest state per order for one ``(broker, account)``.

        ``account_id`` is required and has no default: comparing one account's
        broker orders against every account's stored orders manufactures a
        discrepancy for each order the other accounts placed.
        """

        async with self._pool.acquire() as conn:
            if since is None:
                rows = await conn.fetch(SELECT_LATEST_ORDER_STATES_SQL, broker, account_id)
            else:
                rows = await conn.fetch(
                    SELECT_LATEST_ORDER_STATES_SINCE_SQL, broker, account_id, since
                )
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

# The firm's only record of what it actually holds. Nothing stored positions
# before this: the Reconciliation Agent read the account and the orders, and the
# Risk Officer has no broker credentials of its own (ADR-0003), so portfolio
# limits had no book to measure against.
#
# One row per position per pass, keyed by pass. That makes the table append-only
# like its sibling and lets a reader take "the newest pass for this account" as
# a consistent set — rather than a per-ticker latest, which could mix two passes
# and report a position that was closed in the newer one.
#
# A flat account writes NO rows for its pass. Readers therefore cannot tell
# "flat" from "never ran" by row count alone, which is why the marker row below
# exists: every pass writes one, so the newest pass is always datable.
CREATE_POSITION_SNAPSHOTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ops.position_snapshots (
    event_id TEXT NOT NULL,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    broker TEXT NOT NULL,
    account_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    market_value DOUBLE PRECISION NOT NULL,
    side TEXT,
    PRIMARY KEY (event_id, ticker)
)
""".strip()

CREATE_POSITION_SNAPSHOTS_ACCOUNT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS position_snapshots_account_at_idx
ON ops.position_snapshots (account_id, at DESC)
""".strip()

INSERT_POSITION_SNAPSHOT_SQL = """
INSERT INTO ops.position_snapshots (
    event_id, broker, account_id, ticker, quantity, market_value, side
)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (event_id, ticker) DO NOTHING
""".strip()

# Written on every pass, including a pass that found no positions. Its ticker is
# a sentinel that cannot collide with a real symbol, and readers exclude it.
# Without it, a flat account and a dead Reconciliation Agent are the same
# absence of rows — and the Risk Officer must halt on one and trade on the other.
FLAT_MARKER_TICKER = "__FLAT__"


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


class PostgresPositionSnapshotStore:
    """Append-only store: the account's open positions, once per pass.

    The Risk Officer's only view of the book. It holds no broker credentials
    (ADR-0003) and cannot ask the venue itself, so if this write does not happen
    the portfolio limits have nothing to measure and the gate fails closed.
    """

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_OPS_SCHEMA_SQL)
            await conn.execute(CREATE_POSITION_SNAPSHOTS_TABLE_SQL)
            await conn.execute(CREATE_POSITION_SNAPSHOTS_ACCOUNT_INDEX_SQL)

    async def record(
        self,
        event_id: str,
        broker: str,
        account_id: str,
        positions: Sequence[BrokerPosition],
    ) -> None:
        """Persist one pass's positions, plus the marker that proves it ran.

        The marker is written **first**. If the process dies midway the reader
        then sees a pass with a timestamp and fewer positions than the account
        truly holds — understated exposure — which is why the whole write runs
        in one transaction and either lands complete or not at all.
        """

        if not account_id.strip():
            raise UnidentifiedAccountError(
                "position snapshot has no account id, so it cannot be attributed. "
                "Refusing to write positions the Risk Officer would read as some "
                "other account's book."
            )
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    INSERT_POSITION_SNAPSHOT_SQL,
                    event_id,
                    broker,
                    account_id,
                    FLAT_MARKER_TICKER,
                    0.0,
                    0.0,
                    None,
                )
                for position in positions:
                    await conn.execute(
                        INSERT_POSITION_SNAPSHOT_SQL,
                        event_id,
                        broker,
                        account_id,
                        position.symbol,
                        position.quantity,
                        position.market_value,
                        position.side,
                    )
