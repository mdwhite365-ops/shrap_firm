"""Store-level tests: DDL/SQL wiring and the Pre-Trade Checker contract.

These use a fake asyncpg connection (the project's store-test convention) to
assert the store issues the expected SQL, and cross-check that the tier table
this store owns satisfies the Pre-Trade Checker's read query and tier literal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from shrap.research.universe_curator.store import (
    COUNT_ACTIVE_SQL,
    CREATE_UNIVERSE_STAGING_TABLE_SQL,
    CREATE_UNIVERSE_TIERS_TABLE_SQL,
    DELETE_TICKER_SQL,
    DISPOSITION_APPROVED,
    INSERT_WATCH_SQL,
    RESOLVE_STAGING_SQL,
    TIER_ACTIVE,
    UPSERT_ACTIVE_SQL,
    PostgresUniverseStore,
)
from shrap.risk_compliance.tier3_membership import SELECT_TIER_SQL, TIER3_ACTIVE_TIER


class FakeTransaction:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> object:
        self._conn.transactions_entered += 1
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.fetchrow_result: dict[str, Any] | None = None
        self.fetch_result: list[dict[str, Any]] = []
        self.transactions_entered = 0

    async def execute(self, sql: str, *args: object) -> object:
        self.executed.append((sql, args))
        if sql.startswith("DELETE"):
            return "DELETE 1"
        if sql.startswith("UPDATE"):
            return "UPDATE 1"
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> dict[str, Any] | None:
        return self.fetchrow_result

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        return self.fetch_result

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)


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


def _executed_sql(conn: FakeConn) -> list[str]:
    return [sql for sql, _ in conn.executed]


async def test_ensure_schema_creates_both_tables() -> None:
    pool = FakePool()
    store = PostgresUniverseStore(pool)  # type: ignore[arg-type]
    await store.ensure_schema()

    sql = _executed_sql(pool.conn)
    assert CREATE_UNIVERSE_TIERS_TABLE_SQL in sql
    assert CREATE_UNIVERSE_STAGING_TABLE_SQL in sql


async def test_insert_watch_uses_watch_literal() -> None:
    pool = FakePool()
    store = PostgresUniverseStore(pool)  # type: ignore[arg-type]
    await store.insert_watch(
        ticker="RKLB",
        cik=None,
        mechanism="structural-finding",
        evidence_ref="ref",
        entered_at=datetime(2026, 7, 23, tzinfo=UTC),
        expiry=None,
        falsifier="x",
    )
    calls = [args for sql, args in pool.conn.executed if sql == INSERT_WATCH_SQL]
    assert len(calls) == 1
    assert calls[0][0] == "RKLB"
    assert "'watch'" in INSERT_WATCH_SQL


async def test_active_count_reads_count() -> None:
    pool = FakePool()
    pool.conn.fetchrow_result = {"n": 7}
    store = PostgresUniverseStore(pool)  # type: ignore[arg-type]
    assert await store.active_count() == 7
    assert "WHERE tier = 'active'" in COUNT_ACTIVE_SQL


async def test_delete_and_update_parse_row_counts() -> None:
    pool = FakePool()
    store = PostgresUniverseStore(pool)  # type: ignore[arg-type]
    assert await store.delete_ticker("RKLB") is True
    assert await store.update_watch_expiry("RKLB", datetime(2027, 1, 1, tzinfo=UTC)) is True
    assert any(sql == DELETE_TICKER_SQL for sql, _ in pool.conn.executed)


async def test_apply_promotion_runs_in_one_transaction() -> None:
    pool = FakePool()
    store = PostgresUniverseStore(pool)  # type: ignore[arg-type]
    now = datetime(2026, 7, 23, tzinfo=UTC)
    await store.apply_promotion(
        ticker="RKLB",
        cik=None,
        mechanism="structural-finding",
        evidence_ref="ref",
        entered_at=now,
        profile_path="docs/universe/rklb.md",
        staging_id="01S",
        note="ok",
        resolved_at=now,
        evict_ticker="OLD",
    )
    assert pool.conn.transactions_entered == 1
    sql = _executed_sql(pool.conn)
    assert DELETE_TICKER_SQL in sql
    assert UPSERT_ACTIVE_SQL in sql
    assert RESOLVE_STAGING_SQL in sql
    # the resolve call marks it approved
    resolve = next(args for s, args in pool.conn.executed if s == RESOLVE_STAGING_SQL)
    assert resolve[1] == DISPOSITION_APPROVED


async def test_apply_eviction_runs_in_one_transaction() -> None:
    pool = FakePool()
    store = PostgresUniverseStore(pool)  # type: ignore[arg-type]
    now = datetime(2026, 7, 23, tzinfo=UTC)
    await store.apply_eviction(ticker="OLD", staging_id="01S", note="thesis broke", resolved_at=now)
    assert pool.conn.transactions_entered == 1
    sql = _executed_sql(pool.conn)
    assert DELETE_TICKER_SQL in sql
    assert RESOLVE_STAGING_SQL in sql


async def test_strategies_referencing_returns_empty_on_missing_table() -> None:
    class RaisingConn(FakeConn):
        async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
            raise RuntimeError('relation "research.strategies" does not exist')

    class RaisingPool:
        def __init__(self) -> None:
            self.conn = RaisingConn()

        def acquire(self) -> FakeAcquire:
            return FakeAcquire(self.conn)

    store = PostgresUniverseStore(RaisingPool())  # type: ignore[arg-type]
    assert await store.strategies_referencing("AAPL", ["paper"]) == []


def test_tier_literal_matches_pre_trade_checker() -> None:
    # The tradeable literal this store writes MUST equal the one the Pre-Trade
    # Checker gate pins, or every order would be rejected.
    assert TIER_ACTIVE == TIER3_ACTIVE_TIER == "active"


def test_schema_satisfies_pre_trade_checker_query() -> None:
    # The gate runs: SELECT tier FROM research.universe_tiers WHERE ticker = $1
    assert "research.universe_tiers" in SELECT_TIER_SQL
    assert "research.universe_tiers" in CREATE_UNIVERSE_TIERS_TABLE_SQL
    assert "ticker TEXT PRIMARY KEY" in CREATE_UNIVERSE_TIERS_TABLE_SQL
    assert "tier TEXT NOT NULL" in CREATE_UNIVERSE_TIERS_TABLE_SQL
    assert "CHECK (tier IN ('watch', 'active'))" in CREATE_UNIVERSE_TIERS_TABLE_SQL
