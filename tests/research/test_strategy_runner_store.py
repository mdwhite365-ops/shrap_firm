"""Store-level tests for research.strategy_runner_state.

Fake asyncpg connection (project store-test convention). The fake models the
``ON CONFLICT (strategy_id, ticker) DO UPDATE`` semantics with a dict keyed by
the primary key, so upsert idempotency is actually exercised: a repeated
``(strategy_id, ticker)`` overwrites rather than duplicating.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from shrap.research.strategy_runner.engine import PlannedStateWrite, TargetState
from shrap.research.strategy_runner.store import (
    CREATE_RUNNER_STATE_TABLE_SQL,
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

    async def execute(self, sql: str, *args: object) -> object:
        self.executed.append(sql)
        if sql == UPSERT_RUNNER_STATE_SQL:
            strategy_id, ticker, last_target, last_side, last_session_date = args
            self.rows[(str(strategy_id), str(ticker))] = {
                "strategy_id": strategy_id,
                "ticker": ticker,
                "last_target": last_target,
                "last_side": last_side,
                "last_session_date": last_session_date,
            }
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        if sql == SELECT_RUNNER_STATE_SQL:
            return list(self.rows.values())
        return []


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
        )
    )
    state = await store.read_state()
    assert state == {("s1", "NVDA"): TargetState(1.0, "buy", SESSION)}


async def test_upsert_is_idempotent_on_primary_key() -> None:
    pool = FakePool()
    store = PostgresStrategyRunnerStateStore(pool)  # type: ignore[arg-type]
    first = PlannedStateWrite("s1", "NVDA", 1.0, "buy", SESSION)
    second = PlannedStateWrite("s1", "NVDA", 0.0, "sell", SESSION)
    await store.upsert(first)
    await store.upsert(second)
    state = await store.read_state()
    # One row (no duplicate), carrying the latest values.
    assert list(state) == [("s1", "NVDA")]
    assert state[("s1", "NVDA")] == TargetState(0.0, "sell", SESSION)


async def test_read_state_handles_null_side() -> None:
    pool = FakePool()
    store = PostgresStrategyRunnerStateStore(pool)  # type: ignore[arg-type]
    await store.upsert(PlannedStateWrite("s1", "NVDA", 0.0, None, SESSION))
    state = await store.read_state()
    assert state[("s1", "NVDA")].last_side is None
