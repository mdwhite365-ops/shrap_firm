"""Persistence for infrastructure graphs (ADR-0007; Infra Mapper spec §State).

Four tables, all under the ``research`` schema, created with the house
idempotent ensure-schema pattern (``CREATE ... IF NOT EXISTS``):

- ``research.graphs`` — one header row per world-changer graph. The anchor
  ``world_changer_id`` references ``research.world_changers(candidate_id)``
  (the world-changer's primary key is ``candidate_id``; the Evaluator and the
  seed strategy already anchor on that value).
- ``research.graph_nodes`` — current node state, one row per
  ``(graph_id, ticker, layer_role)`` triple.
- ``research.graph_node_history`` — append-only status/confidence transitions
  for audit. Rebuilding a node's timeline = replaying its history rows.
- ``research.graph_node_evidence`` — append-only evidence rows (which primary
  source placed a ticker in a layer, and when).

The store is the sole writer. Layer-role validity is enforced in code against
``docs/research/layer-role-taxonomy.md`` (the taxonomy grows with Mike's
updates), so ``layer_role`` is a plain ``TEXT`` column here, not a DB CHECK.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Protocol

# Graph header statuses.
GRAPH_PENDING_REVIEW = "pending-review"
GRAPH_ACTIVE = "active"
GRAPH_RETIRED = "retired"

# Node confidence (ordinal — never a calibrated probability, per the spec).
CONFIDENCE_LOW = "low"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_HIGH = "high"
CONFIDENCE_LEVELS = (CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH)

# Critical-path status — the Cisco-1999 field.
CRITICAL_ON_PATH = "on-critical-path"
CRITICAL_SUBSTITUTABLE = "enabling-but-substitutable"
CRITICAL_COMMODIFIED = "commodified-or-at-risk"
CRITICAL_DOWNSTREAM = "downstream-beneficiary"
CRITICAL_PATH_STATUSES = (
    CRITICAL_ON_PATH,
    CRITICAL_SUBSTITUTABLE,
    CRITICAL_COMMODIFIED,
    CRITICAL_DOWNSTREAM,
)

# Node lifecycle statuses.
NODE_PENDING_REVIEW = "pending-review"
NODE_ACTIVE = "active"
NODE_DOWNGRADED = "downgraded"
NODE_REMOVED = "removed"
NODE_STALE_EVIDENCE = "stale-evidence"
NODE_STATUSES = (
    NODE_PENDING_REVIEW,
    NODE_ACTIVE,
    NODE_DOWNGRADED,
    NODE_REMOVED,
    NODE_STALE_EVIDENCE,
)

CREATE_RESEARCH_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS research"

CREATE_GRAPHS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.graphs (
    graph_id TEXT PRIMARY KEY,
    world_changer_id TEXT NOT NULL REFERENCES research.world_changers (candidate_id),
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending-review', 'active', 'retired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip()

CREATE_GRAPHS_WORLD_CHANGER_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS graphs_world_changer_idx
ON research.graphs (world_changer_id)
""".strip()

CREATE_GRAPH_NODES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.graph_nodes (
    graph_id TEXT NOT NULL REFERENCES research.graphs (graph_id),
    ticker TEXT NOT NULL,
    layer_role TEXT NOT NULL,
    confidence TEXT NOT NULL CHECK (confidence IN ('low', 'medium', 'high')),
    critical_path_status TEXT NOT NULL CHECK (critical_path_status IN (
        'on-critical-path', 'enabling-but-substitutable',
        'commodified-or-at-risk', 'downstream-beneficiary'
    )),
    last_confirmed_evidence_ref TEXT,
    kill_criteria JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'pending-review', 'active', 'downgraded', 'removed', 'stale-evidence'
    )),
    entered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (graph_id, ticker, layer_role)
)
""".strip()

CREATE_GRAPH_NODES_STATUS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS graph_nodes_status_idx
ON research.graph_nodes (graph_id, status)
""".strip()

CREATE_GRAPH_NODE_HISTORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.graph_node_history (
    history_id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    layer_role TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    from_confidence TEXT,
    to_confidence TEXT,
    reason TEXT NOT NULL,
    at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip()

CREATE_GRAPH_NODE_HISTORY_NODE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS graph_node_history_node_idx
ON research.graph_node_history (graph_id, ticker, layer_role, at)
""".strip()

CREATE_GRAPH_NODE_EVIDENCE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.graph_node_evidence (
    evidence_id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    layer_role TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    source_class TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL
)
""".strip()

CREATE_GRAPH_NODE_EVIDENCE_NODE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS graph_node_evidence_node_idx
ON research.graph_node_evidence (graph_id, ticker, layer_role, observed_at DESC)
""".strip()

INSERT_GRAPH_SQL = """
INSERT INTO research.graphs (graph_id, world_changer_id, title, status)
VALUES ($1, $2, $3, $4)
""".strip()

SELECT_GRAPH_BY_WORLD_CHANGER_SQL = """
SELECT graph_id, world_changer_id, title, status, created_at, updated_at
FROM research.graphs
WHERE world_changer_id = $1
""".strip()

SELECT_GRAPHS_SQL = """
SELECT graph_id, world_changer_id, title, status, created_at, updated_at
FROM research.graphs
ORDER BY created_at
""".strip()

UPSERT_NODE_SQL = """
INSERT INTO research.graph_nodes (
    graph_id, ticker, layer_role, confidence, critical_path_status,
    last_confirmed_evidence_ref, kill_criteria, status
)
VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
ON CONFLICT (graph_id, ticker, layer_role) DO UPDATE SET
    confidence = EXCLUDED.confidence,
    critical_path_status = EXCLUDED.critical_path_status,
    last_confirmed_evidence_ref = EXCLUDED.last_confirmed_evidence_ref,
    kill_criteria = EXCLUDED.kill_criteria,
    status = EXCLUDED.status,
    updated_at = now()
""".strip()

SELECT_NODES_FOR_GRAPH_SQL = """
SELECT graph_id, ticker, layer_role, confidence, critical_path_status,
       last_confirmed_evidence_ref, kill_criteria, status, entered_at, updated_at
FROM research.graph_nodes
WHERE graph_id = $1
ORDER BY layer_role, ticker
""".strip()

SELECT_ACTIVE_NODES_SQL = """
SELECT graph_id, ticker, layer_role, confidence, critical_path_status,
       last_confirmed_evidence_ref, kill_criteria, status, entered_at, updated_at
FROM research.graph_nodes
WHERE status = 'active'
ORDER BY graph_id, layer_role, ticker
""".strip()

UPDATE_NODE_STATUS_SQL = """
UPDATE research.graph_nodes
SET status = $4, updated_at = now()
WHERE graph_id = $1 AND ticker = $2 AND layer_role = $3
""".strip()

INSERT_NODE_HISTORY_SQL = """
INSERT INTO research.graph_node_history (
    history_id, graph_id, ticker, layer_role,
    from_status, to_status, from_confidence, to_confidence, reason
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
""".strip()

INSERT_NODE_EVIDENCE_SQL = """
INSERT INTO research.graph_node_evidence (
    evidence_id, graph_id, ticker, layer_role, evidence_ref, source_class, observed_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7)
""".strip()

# Newest evidence per node — the clock the staleness pass runs on. The node row's
# last_confirmed_evidence_ref is free text with no date, so age comes from here.
SELECT_LATEST_EVIDENCE_SQL = """
SELECT ticker, layer_role, MAX(observed_at) AS latest_observed_at
FROM research.graph_node_evidence
WHERE graph_id = $1
GROUP BY ticker, layer_role
""".strip()

SELECT_EVIDENCE_FOR_GRAPH_SQL = """
SELECT evidence_id, graph_id, ticker, layer_role, evidence_ref, source_class, observed_at
FROM research.graph_node_evidence
WHERE graph_id = $1
ORDER BY ticker, layer_role, observed_at
""".strip()

# Deliberate exception to the append-only rule on graph_node_evidence. Staleness
# reads MAX(observed_at), so a row stamped too *fresh* can never be corrected by
# appending — the wrong row keeps winning the max. Correcting a false observation
# date therefore requires an in-place update. Callers must target a single
# evidence_id and record the correction in graph_node_history.
UPDATE_EVIDENCE_OBSERVED_AT_SQL = """
UPDATE research.graph_node_evidence
SET observed_at = $2
WHERE evidence_id = $1
""".strip()


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetchrow(self, sql: str, *args: object) -> Mapping[str, Any] | None: ...

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]: ...

    def transaction(self) -> AbstractAsyncContextManager[object]: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


class PostgresGraphStore:
    """Sole writer of the infrastructure-graph tables."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_RESEARCH_SCHEMA_SQL)
            await conn.execute(CREATE_GRAPHS_TABLE_SQL)
            await conn.execute(CREATE_GRAPHS_WORLD_CHANGER_INDEX_SQL)
            await conn.execute(CREATE_GRAPH_NODES_TABLE_SQL)
            await conn.execute(CREATE_GRAPH_NODES_STATUS_INDEX_SQL)
            await conn.execute(CREATE_GRAPH_NODE_HISTORY_TABLE_SQL)
            await conn.execute(CREATE_GRAPH_NODE_HISTORY_NODE_INDEX_SQL)
            await conn.execute(CREATE_GRAPH_NODE_EVIDENCE_TABLE_SQL)
            await conn.execute(CREATE_GRAPH_NODE_EVIDENCE_NODE_INDEX_SQL)

    async def insert_graph(
        self, *, graph_id: str, world_changer_id: str, title: str, status: str
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(INSERT_GRAPH_SQL, graph_id, world_changer_id, title, status)

    async def get_graph_by_world_changer(self, world_changer_id: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_GRAPH_BY_WORLD_CHANGER_SQL, world_changer_id)
        return None if row is None else dict(row)

    async def list_graphs(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_GRAPHS_SQL)
        return [dict(row) for row in rows]

    async def upsert_node(
        self,
        *,
        graph_id: str,
        ticker: str,
        layer_role: str,
        confidence: str,
        critical_path_status: str,
        last_confirmed_evidence_ref: str | None,
        kill_criteria: Sequence[str],
        status: str,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                UPSERT_NODE_SQL,
                graph_id,
                ticker,
                layer_role,
                confidence,
                critical_path_status,
                last_confirmed_evidence_ref,
                json.dumps(list(kill_criteria), separators=(",", ":")),
                status,
            )

    async def nodes_for_graph(self, graph_id: str) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_NODES_FOR_GRAPH_SQL, graph_id)
        return [dict(row) for row in rows]

    async def active_nodes(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_ACTIVE_NODES_SQL)
        return [dict(row) for row in rows]

    async def set_node_status(
        self, *, graph_id: str, ticker: str, layer_role: str, status: str
    ) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                UPDATE_NODE_STATUS_SQL, graph_id, ticker, layer_role, status
            )
        return str(result).endswith(" 1")

    async def record_history(
        self,
        *,
        history_id: str,
        graph_id: str,
        ticker: str,
        layer_role: str,
        from_status: str | None,
        to_status: str,
        from_confidence: str | None,
        to_confidence: str | None,
        reason: str,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_NODE_HISTORY_SQL,
                history_id,
                graph_id,
                ticker,
                layer_role,
                from_status,
                to_status,
                from_confidence,
                to_confidence,
                reason,
            )

    async def latest_evidence_at(self, graph_id: str) -> dict[tuple[str, str], datetime]:
        """Newest evidence timestamp per ``(ticker, layer_role)`` in one graph."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_LATEST_EVIDENCE_SQL, graph_id)
        return {
            (str(row["ticker"]), str(row["layer_role"])): row["latest_observed_at"] for row in rows
        }

    async def evidence_for_graph(self, graph_id: str) -> list[dict[str, Any]]:
        """Every evidence row in one graph, oldest first per node."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_EVIDENCE_FOR_GRAPH_SQL, graph_id)
        return [dict(row) for row in rows]

    async def correct_evidence_observed_at(
        self, *, evidence_id: str, observed_at: datetime
    ) -> bool:
        """Correct one evidence row's observation date.

        The documented exception to append-only — see
        ``UPDATE_EVIDENCE_OBSERVED_AT_SQL``. Only for repairing a date that was
        recorded wrong, never for recording new evidence.
        """
        async with self._pool.acquire() as conn:
            result = await conn.execute(UPDATE_EVIDENCE_OBSERVED_AT_SQL, evidence_id, observed_at)
        return str(result).endswith(" 1")

    async def insert_evidence(
        self,
        *,
        evidence_id: str,
        graph_id: str,
        ticker: str,
        layer_role: str,
        evidence_ref: str,
        source_class: str,
        observed_at: datetime,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_NODE_EVIDENCE_SQL,
                evidence_id,
                graph_id,
                ticker,
                layer_role,
                evidence_ref,
                source_class,
                observed_at,
            )


__all__ = [
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LEVELS",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "CREATE_GRAPHS_TABLE_SQL",
    "CREATE_GRAPH_NODES_TABLE_SQL",
    "CREATE_GRAPH_NODE_EVIDENCE_TABLE_SQL",
    "CREATE_GRAPH_NODE_HISTORY_TABLE_SQL",
    "CRITICAL_COMMODIFIED",
    "CRITICAL_DOWNSTREAM",
    "CRITICAL_ON_PATH",
    "CRITICAL_PATH_STATUSES",
    "CRITICAL_SUBSTITUTABLE",
    "GRAPH_ACTIVE",
    "GRAPH_PENDING_REVIEW",
    "GRAPH_RETIRED",
    "NODE_ACTIVE",
    "NODE_DOWNGRADED",
    "NODE_PENDING_REVIEW",
    "NODE_REMOVED",
    "NODE_STALE_EVIDENCE",
    "NODE_STATUSES",
    "SELECT_EVIDENCE_FOR_GRAPH_SQL",
    "SELECT_LATEST_EVIDENCE_SQL",
    "UPDATE_EVIDENCE_OBSERVED_AT_SQL",
    "AsyncPool",
    "PostgresGraphStore",
]
