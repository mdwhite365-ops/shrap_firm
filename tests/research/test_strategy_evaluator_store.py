"""Store-level tests: the research.evaluations writer and the read-only readers.

Fake asyncpg connection (project store-test convention) asserting the store
issues the expected SQL and the readers parse rows into the right shapes.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from typing import Any

from shrap.research.strategy_evaluator.store import (
    ADD_EVALUATIONS_ANCHOR_REQUIRED_SQL,
    CREATE_EVALUATIONS_TABLE_SQL,
    INSERT_EVALUATION_SQL,
    SELECT_DAILY_BARS_SQL,
    SELECT_LATEST_EVALUATION_AT_SQL,
    SELECT_TICKER_TIER_SQL,
    SELECT_WORLD_CHANGER_STATUS_SQL,
    PostgresEvaluationStore,
    PostgresEvaluatorReader,
)
from shrap.research.strategy_evaluator.strategy import BarSample


class FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.fetched: list[tuple[str, tuple[object, ...]]] = []
        self.fetchrow_result: dict[str, Any] | None = None
        self.fetch_result: list[dict[str, Any]] = []

    async def execute(self, sql: str, *args: object) -> object:
        self.executed.append((sql, args))
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> dict[str, Any] | None:
        return self.fetchrow_result

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        self.fetched.append((sql, args))
        return self.fetch_result


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


async def test_ensure_schema_creates_evaluations_table() -> None:
    pool = FakePool()
    store = PostgresEvaluationStore(pool)  # type: ignore[arg-type]
    await store.ensure_schema()
    assert CREATE_EVALUATIONS_TABLE_SQL in _executed_sql(pool.conn)


async def test_insert_evaluation_serializes_jsonb_and_binds_args() -> None:
    pool = FakePool()
    store = PostgresEvaluationStore(pool)  # type: ignore[arg-type]
    await store.insert_evaluation(
        evaluation_id="01EVAL",
        strategy_id="01STRAT",
        spec_hash="hash123",
        protocol_version="0.1",
        verdict="promote",
        reason="promote-criteria-met",
        anchor_required=True,
        anchor_fresh=True,
        total_trades=210,
        from_stage="hypothesis",
        to_stage="paper",
        aggregate_metrics={"sharpe": 1.4, "trade_count": 210},
        fold_metrics=[{"index": 0, "sharpe": 1.1}],
        stress_metrics={"sharpe": 0.6},
        config={"n_folds": 6},
        card_path="docs/strategies/evaluations/01STRAT/x.md",
        trigger="on-demand",
        created_at=datetime(2026, 7, 26, tzinfo=UTC),
    )
    calls = [args for sql, args in pool.conn.executed if sql == INSERT_EVALUATION_SQL]
    assert len(calls) == 1
    args = calls[0]
    assert args[0] == "01EVAL"
    assert args[4] == "promote"
    assert args[6] is True  # anchor_required
    assert args[8] == 210  # total_trades
    # aggregate/fold/stress/config are json-encoded strings for ::jsonb cast.
    assert json.loads(str(args[11]))["trade_count"] == 210
    assert json.loads(str(args[12]))[0]["index"] == 0
    assert json.loads(str(args[14]))["n_folds"] == 6


def test_insert_sql_placeholders_match_the_column_list() -> None:
    """Positional binds drift silently; a column added mid-list shifts the rest.

    Adding ``anchor_required`` in the middle of the INSERT is exactly the change
    that renumbers every later ``$N``, so the arity is asserted against the SQL
    itself rather than trusted.
    """

    columns = INSERT_EVALUATION_SQL.split("(", 1)[1].split(")", 1)[0]
    n_columns = len([c for c in columns.split(",") if c.strip()])
    placeholders = {int(m) for m in re.findall(r"\$(\d+)", INSERT_EVALUATION_SQL)}
    assert placeholders == set(range(1, n_columns + 1))


async def test_ensure_schema_backfills_anchor_required_on_existing_tables() -> None:
    """The column must arrive by ALTER, not only in the CREATE.

    ``CREATE TABLE IF NOT EXISTS`` is a no-op against the Dell's existing
    ``research.evaluations``, so a column declared only there would never appear
    in production and every insert would fail on an unknown column.
    """

    pool = FakePool()
    store = PostgresEvaluationStore(pool)  # type: ignore[arg-type]
    await store.ensure_schema()
    assert ADD_EVALUATIONS_ANCHOR_REQUIRED_SQL in _executed_sql(pool.conn)
    assert "DEFAULT TRUE" in ADD_EVALUATIONS_ANCHOR_REQUIRED_SQL


async def test_world_changer_status_reads_status() -> None:
    pool = FakePool()
    pool.conn.fetchrow_result = {"status": "promoted"}
    reader = PostgresEvaluatorReader(pool)  # type: ignore[arg-type]
    assert await reader.world_changer_status("01WC") == "promoted"
    assert "research.world_changers" in SELECT_WORLD_CHANGER_STATUS_SQL


async def test_world_changer_status_none_when_missing() -> None:
    pool = FakePool()
    pool.conn.fetchrow_result = None
    reader = PostgresEvaluatorReader(pool)  # type: ignore[arg-type]
    assert await reader.world_changer_status("missing") is None


async def test_ticker_tier_reads_tier() -> None:
    pool = FakePool()
    pool.conn.fetchrow_result = {"tier": "active"}
    reader = PostgresEvaluatorReader(pool)  # type: ignore[arg-type]
    assert await reader.ticker_tier("NVDA") == "active"
    assert "research.universe_tiers" in SELECT_TICKER_TIER_SQL


async def test_read_bars_parses_rows_to_bar_samples() -> None:
    pool = FakePool()
    pool.conn.fetch_result = [
        {
            "session_date": date(2026, 1, 2),
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "volume": 1000.0,
        }
    ]
    reader = PostgresEvaluatorReader(pool)  # type: ignore[arg-type]
    bars = await reader.read_bars("NVDA", date(2021, 1, 1), date(2026, 1, 1), "all")
    assert bars == [
        BarSample(
            session_date=date(2026, 1, 2),
            open=10.0,
            high=11.0,
            low=9.5,
            close=10.5,
            volume=1000.0,
        )
    ]
    assert "market_data.daily_bars" in SELECT_DAILY_BARS_SQL
    # adjustment is the 2nd bind arg (ticker, adjustment, start, end).
    _, args = pool.conn.fetched[0]
    assert args[0] == "NVDA"
    assert args[1] == "all"


async def test_latest_evaluation_at_reads_the_ledger_keyed_on_all_three_fields() -> None:
    """The trigger's re-evaluation floor. Keying on strategy_id alone would make
    a re-spec'd or re-protocoled strategy wait out an interval it should skip."""

    pool = FakePool()
    stamp = datetime(2026, 7, 28, 6, 0, tzinfo=UTC)
    pool.conn.fetchrow_result = {"latest": stamp}
    store = PostgresEvaluationStore(pool)  # type: ignore[arg-type]

    assert await store.latest_evaluation_at("01S", "hash", "0.1") == stamp
    assert "spec_hash = $2" in SELECT_LATEST_EVALUATION_AT_SQL
    assert "protocol_version = $3" in SELECT_LATEST_EVALUATION_AT_SQL


async def test_latest_evaluation_at_is_none_when_never_evaluated() -> None:
    pool = FakePool()
    pool.conn.fetchrow_result = None
    store = PostgresEvaluationStore(pool)  # type: ignore[arg-type]
    assert await store.latest_evaluation_at("01S", "hash", "0.1") is None


async def test_latest_evaluation_at_is_none_when_the_aggregate_is_null() -> None:
    """`max()` over zero rows returns a row containing NULL, not no row at all.

    Returning that NULL as a timestamp would crash the sweep's subtraction.
    """

    pool = FakePool()
    pool.conn.fetchrow_result = {"latest": None}
    store = PostgresEvaluationStore(pool)  # type: ignore[arg-type]
    assert await store.latest_evaluation_at("01S", "hash", "0.1") is None
