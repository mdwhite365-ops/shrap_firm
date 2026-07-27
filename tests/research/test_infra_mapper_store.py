"""Store-level tests for the infrastructure-graph schema (fake asyncpg).

Assert the store issues the expected SQL and that the DDL carries the
critical-path / confidence / status constraints the Infra Mapper spec requires.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from shrap.research.infra_mapper.store import (
    CREATE_GRAPH_NODE_EVIDENCE_TABLE_SQL,
    CREATE_GRAPH_NODE_HISTORY_TABLE_SQL,
    CREATE_GRAPH_NODES_TABLE_SQL,
    CREATE_GRAPHS_TABLE_SQL,
    GRAPH_ACTIVE,
    INSERT_NODE_EVIDENCE_SQL,
    INSERT_NODE_HISTORY_SQL,
    NODE_ACTIVE,
    NODE_STALE_EVIDENCE,
    UPSERT_NODE_SQL,
    PostgresGraphStore,
)


class FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.fetchrow_result: dict[str, Any] | None = None
        self.fetch_result: list[dict[str, Any]] = []

    async def execute(self, sql: str, *args: object) -> object:
        self.executed.append((sql, args))
        if sql.startswith("UPDATE"):
            return "UPDATE 1"
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> dict[str, Any] | None:
        return self.fetchrow_result

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
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


async def test_ensure_schema_creates_all_four_tables() -> None:
    pool = FakePool()
    store = PostgresGraphStore(pool)  # type: ignore[arg-type]
    await store.ensure_schema()

    sql = _executed_sql(pool.conn)
    assert CREATE_GRAPHS_TABLE_SQL in sql
    assert CREATE_GRAPH_NODES_TABLE_SQL in sql
    assert CREATE_GRAPH_NODE_HISTORY_TABLE_SQL in sql
    assert CREATE_GRAPH_NODE_EVIDENCE_TABLE_SQL in sql


def test_ddl_carries_the_required_check_constraints() -> None:
    # The graph anchors on the world-changer primary key (candidate_id).
    assert "REFERENCES research.world_changers (candidate_id)" in CREATE_GRAPHS_TABLE_SQL
    # Cisco-1999 field + ordinal confidence + node lifecycle are constrained.
    assert "critical_path_status TEXT NOT NULL CHECK" in CREATE_GRAPH_NODES_TABLE_SQL
    assert "on-critical-path" in CREATE_GRAPH_NODES_TABLE_SQL
    assert "confidence IN ('low', 'medium', 'high')" in CREATE_GRAPH_NODES_TABLE_SQL
    assert "stale-evidence" in CREATE_GRAPH_NODES_TABLE_SQL


async def test_insert_graph_and_lookup_by_world_changer() -> None:
    pool = FakePool()
    store = PostgresGraphStore(pool)  # type: ignore[arg-type]
    await store.insert_graph(
        graph_id="g1",
        world_changer_id="01KXVVPXDMB4HS1QNRPQWRP1RX",
        title="Fission cost-curve",
        status=GRAPH_ACTIVE,
    )
    calls = [
        args for sql, args in pool.conn.executed if sql.startswith("INSERT INTO research.graphs")
    ]
    assert len(calls) == 1
    assert calls[0] == ("g1", "01KXVVPXDMB4HS1QNRPQWRP1RX", "Fission cost-curve", GRAPH_ACTIVE)


async def test_upsert_node_serializes_kill_criteria_as_jsonb() -> None:
    pool = FakePool()
    store = PostgresGraphStore(pool)  # type: ignore[arg-type]
    await store.upsert_node(
        graph_id="g1",
        ticker="XLE",
        layer_role="downstream-beneficiary",
        confidence="low",
        critical_path_status="downstream-beneficiary",
        last_confirmed_evidence_ref="ref",
        kill_criteria=["thesis broken", "liquidity loss"],
        status=NODE_ACTIVE,
    )
    calls = [args for sql, args in pool.conn.executed if sql == UPSERT_NODE_SQL]
    assert len(calls) == 1
    # kill_criteria is arg 7 (0-indexed 6), serialized to a JSON string.
    assert calls[0][6] == '["thesis broken","liquidity loss"]'
    assert "ON CONFLICT (graph_id, ticker, layer_role) DO UPDATE" in UPSERT_NODE_SQL


async def test_set_node_status_reports_single_row_update() -> None:
    pool = FakePool()
    store = PostgresGraphStore(pool)  # type: ignore[arg-type]
    ok = await store.set_node_status(
        graph_id="g1", ticker="XLE", layer_role="downstream-beneficiary", status=NODE_STALE_EVIDENCE
    )
    assert ok is True


async def test_history_and_evidence_are_append_only_inserts() -> None:
    pool = FakePool()
    store = PostgresGraphStore(pool)  # type: ignore[arg-type]
    await store.record_history(
        history_id="h1",
        graph_id="g1",
        ticker="XLE",
        layer_role="downstream-beneficiary",
        from_status=NODE_ACTIVE,
        to_status=NODE_STALE_EVIDENCE,
        from_confidence="low",
        to_confidence="low",
        reason="evidence older than freshness threshold",
    )
    await store.insert_evidence(
        evidence_id="e1",
        graph_id="g1",
        ticker="XLE",
        layer_role="downstream-beneficiary",
        evidence_ref="https://example.test/filing",
        source_class="gov:doe",
        observed_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    sql = _executed_sql(pool.conn)
    assert INSERT_NODE_HISTORY_SQL in sql
    assert INSERT_NODE_EVIDENCE_SQL in sql
    # Both are plain INSERTs (no UPDATE/DELETE) — append-only.
    assert not any(s.startswith(("UPDATE research.graph_node_history", "DELETE")) for s in sql)
