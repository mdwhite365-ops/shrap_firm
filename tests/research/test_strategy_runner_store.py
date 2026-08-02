"""Store-level tests for research.strategy_runner_state.

Fake asyncpg connection (project store-test convention). The fake models the
``ON CONFLICT (strategy_id, ticker) DO UPDATE`` semantics with a dict keyed by
the primary key, so upsert idempotency is actually exercised: a repeated
``(strategy_id, ticker)`` overwrites rather than duplicating.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from shrap.research.strategy_runner.engine import PlannedStateWrite, TargetState
from shrap.research.strategy_runner.store import (
    ALTER_RUNNER_STATE_ADD_QUANTITY_SQL,
    CREATE_RUNNER_STATE_TABLE_SQL,
    SELECT_LATEST_EQUITY_SQL,
    SELECT_RUNNER_STATE_SQL,
    UPSERT_RUNNER_STATE_SQL,
    PostgresStrategyRunnerStateStore,
)

SESSION = date(2026, 7, 24)


class FakeConn:
    """Models the runner-state table as a PK-keyed dict."""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.fetchrow_result: dict[str, Any] | None = None
        self.fetchrow_args: list[tuple[object, ...]] = []

    async def execute(self, sql: str, *args: object) -> object:
        self.executed.append(sql)
        if sql == UPSERT_RUNNER_STATE_SQL:
            (
                strategy_id,
                ticker,
                last_target,
                last_side,
                last_session_date,
                last_quantity,
                last_slot,
            ) = args
            self.rows[(str(strategy_id), str(ticker))] = {
                "strategy_id": strategy_id,
                "ticker": ticker,
                "last_target": last_target,
                "last_side": last_side,
                "last_session_date": last_session_date,
                "last_quantity": last_quantity,
                "last_slot": last_slot,
            }
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        if sql == SELECT_RUNNER_STATE_SQL:
            return list(self.rows.values())
        return []

    async def fetchrow(self, sql: str, *args: object) -> dict[str, Any] | None:
        self.executed.append(sql)
        self.fetchrow_args.append(args)
        return self.fetchrow_result


class FakeAcquire:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakePool:
    def __init__(self) -> None:
        self.conn = FakeConn()

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)


async def test_ensure_schema_creates_runner_state_table() -> None:
    pool = FakePool()
    store = PostgresStrategyRunnerStateStore(pool)  # type: ignore[arg-type]
    await store.ensure_schema()
    assert CREATE_RUNNER_STATE_TABLE_SQL in pool.conn.executed
    assert "PRIMARY KEY (strategy_id, ticker)" in CREATE_RUNNER_STATE_TABLE_SQL


async def test_upsert_then_read_round_trips_target_state() -> None:
    pool = FakePool()
    store = PostgresStrategyRunnerStateStore(pool)  # type: ignore[arg-type]
    await store.upsert(
        PlannedStateWrite(
            strategy_id="s1",
            ticker="NVDA",
            last_target=1.0,
            last_side="buy",
            last_session_date=SESSION,
            last_quantity=40,
        )
    )
    state = await store.read_state()
    assert state == {("s1", "NVDA"): TargetState(1.0, "buy", SESSION, 40)}


async def test_upsert_is_idempotent_on_primary_key() -> None:
    pool = FakePool()
    store = PostgresStrategyRunnerStateStore(pool)  # type: ignore[arg-type]
    first = PlannedStateWrite("s1", "NVDA", 1.0, "buy", SESSION, 40)
    second = PlannedStateWrite("s1", "NVDA", 0.0, "sell", SESSION, 0)
    await store.upsert(first)
    await store.upsert(second)
    state = await store.read_state()
    # One row (no duplicate), carrying the latest values.
    assert list(state) == [("s1", "NVDA")]
    assert state[("s1", "NVDA")] == TargetState(0.0, "sell", SESSION, 0)


async def test_read_state_handles_null_side() -> None:
    pool = FakePool()
    store = PostgresStrategyRunnerStateStore(pool)  # type: ignore[arg-type]
    await store.upsert(PlannedStateWrite("s1", "NVDA", 0.0, None, SESSION))
    state = await store.read_state()
    assert state[("s1", "NVDA")].last_side is None


# --- the last_quantity migration ---------------------------------------------


async def test_ensure_schema_migrates_the_existing_production_table() -> None:
    """CREATE TABLE IF NOT EXISTS is a no-op where the table already exists, so
    the new column needs its own ALTER or the Dell's rows never gain it."""

    pool = FakePool()
    store = PostgresStrategyRunnerStateStore(pool)  # type: ignore[arg-type]
    await store.ensure_schema()
    assert ALTER_RUNNER_STATE_ADD_QUANTITY_SQL in pool.conn.executed
    assert "ADD COLUMN IF NOT EXISTS last_quantity" in ALTER_RUNNER_STATE_ADD_QUANTITY_SQL


def test_the_migration_backfills_one_share_not_zero() -> None:
    """Every pre-sizing row was opened by the fixed-1-share path, so 1 is what
    those positions hold. Defaulting to 0 would strand them: an exit sells the
    recorded quantity, and a recorded 0 never closes."""

    assert "DEFAULT 1" in ALTER_RUNNER_STATE_ADD_QUANTITY_SQL


# --- account equity, for notional sizing --------------------------------------


async def test_latest_equity_reads_the_reconciliation_agent_s_snapshot() -> None:
    """The Runner learns its account size from a table it does not own.

    ADR-0003 keeps broker credentials inside broker-facing containers, and the
    Runner is not one of them — so equity comes from ops.account_snapshots,
    which the Reconciliation Agent writes every pass.
    """

    stamp = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    pool = FakePool()
    pool.conn.fetchrow_result = {"equity": 10_000.0, "at": stamp}
    store = PostgresStrategyRunnerStateStore(pool)  # type: ignore[arg-type]

    assert await store.latest_equity("PA123ABC") == (10_000.0, stamp)
    assert "ops.account_snapshots" in SELECT_LATEST_EQUITY_SQL
    assert "ORDER BY at DESC" in SELECT_LATEST_EQUITY_SQL


async def test_latest_equity_is_none_when_no_snapshot_exists() -> None:
    """Returns None rather than a default; the caller refuses to size on it."""

    pool = FakePool()
    pool.conn.fetchrow_result = None
    store = PostgresStrategyRunnerStateStore(pool)  # type: ignore[arg-type]
    assert await store.latest_equity("PA123ABC") == (None, None)


async def test_equity_is_scoped_to_one_account() -> None:
    """ADR-0017 gives each strategy its own broker account.

    An unscoped "newest snapshot" returns whichever account reported most
    recently — a plausible number from the wrong book, which is the worst kind
    of wrong because nothing about it looks broken.
    """

    pool = FakePool()
    pool.conn.fetchrow_result = {"equity": 10_000.0, "at": datetime(2026, 7, 29, tzinfo=UTC)}
    store = PostgresStrategyRunnerStateStore(pool)  # type: ignore[arg-type]

    await store.latest_equity("PA3ABCDEF")
    assert "account_id = $1" in SELECT_LATEST_EQUITY_SQL
    assert pool.conn.fetchrow_args[-1] == ("PA3ABCDEF",)


def test_legacy_rows_without_an_account_can_never_be_selected() -> None:
    """Snapshots written before the column existed carry NULL, and no account
    can be invented for them. `account_id = $1` never matches NULL, so they are
    excluded by construction rather than by a backfill that would fabricate
    identity."""

    assert "account_id = $1" in SELECT_LATEST_EQUITY_SQL


async def test_the_equity_query_skips_null_rows() -> None:
    """A snapshot row can exist with a null equity; ordering by `at` alone would
    return it and read as 'no equity' when a usable earlier row exists."""

    assert "equity IS NOT NULL" in SELECT_LATEST_EQUITY_SQL
