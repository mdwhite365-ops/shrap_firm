"""Candidate persistence: world_changers, evidence, and batch records.

Three tables (Tech Watcher spec §State / §Outputs):

- ``research.world_changers`` — every candidate ever synthesized, including
  the rejected ones (status ``rejected``) — the survivorship-bias rule says
  the kill graveyard IS the denominator. Statuses in this slice:
  ``proposed`` / ``seen-not-proposed`` / ``rejected``. Mike's promote/kill
  workflow (→ ``promoted`` / ``killed``) is a later card.
- ``research.world_changer_evidence`` — append-only item references per
  candidate.
- ``research.tech_watcher_batches`` — one row per synthesis batch with the
  audit fields (model, counts); also the due-check clock for the daily pass.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Protocol

CREATE_WORLD_CHANGERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.world_changers (
    candidate_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    archetype TEXT NOT NULL,
    status TEXT NOT NULL,
    thesis TEXT NOT NULL,
    confidence TEXT NOT NULL,
    expected_impact_horizon TEXT NOT NULL,
    kill_criteria JSONB NOT NULL,
    falsifier_horizon TEXT,
    dependency_graph_seed JSONB,
    source_classes JSONB NOT NULL,
    score JSONB,
    rejection_reason TEXT,
    llm_model TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    raw_response JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip()

CREATE_WORLD_CHANGERS_STATUS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS world_changers_status_idx
ON research.world_changers (status, created_at DESC)
""".strip()

# Idempotent migration for tables created by the synthesis-slice deploy:
# Mike's promote/kill decisions carry a timestamp and a preserved note.
ADD_DECIDED_AT_COLUMN_SQL = """
ALTER TABLE research.world_changers
ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ
""".strip()

ADD_DECISION_NOTE_COLUMN_SQL = """
ALTER TABLE research.world_changers
ADD COLUMN IF NOT EXISTS decision_note TEXT
""".strip()

CREATE_EVIDENCE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.world_changer_evidence (
    candidate_id TEXT NOT NULL REFERENCES research.world_changers (candidate_id),
    item_id TEXT NOT NULL,
    source_class TEXT NOT NULL,
    note TEXT,
    PRIMARY KEY (candidate_id, item_id)
)
""".strip()

CREATE_BATCHES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.tech_watcher_batches (
    batch_id TEXT PRIMARY KEY,
    ran_at TIMESTAMPTZ NOT NULL,
    items_filtered INTEGER NOT NULL,
    items_relevant INTEGER NOT NULL,
    clusters INTEGER NOT NULL,
    clusters_promotable INTEGER NOT NULL,
    candidates_proposed INTEGER NOT NULL,
    candidates_rejected INTEGER NOT NULL,
    llm_model TEXT NOT NULL
)
""".strip()

INSERT_CANDIDATE_SQL = """
INSERT INTO research.world_changers (
    candidate_id, name, archetype, status, thesis, confidence,
    expected_impact_horizon, kill_criteria, falsifier_horizon,
    dependency_graph_seed, source_classes, score, rejection_reason,
    llm_model, batch_id, raw_response, created_at
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9,
    $10::jsonb, $11::jsonb, $12::jsonb, $13, $14, $15, $16::jsonb, $17
)
""".strip()

INSERT_EVIDENCE_SQL = """
INSERT INTO research.world_changer_evidence (candidate_id, item_id, source_class, note)
VALUES ($1, $2, $3, $4)
ON CONFLICT (candidate_id, item_id) DO NOTHING
""".strip()

INSERT_BATCH_SQL = """
INSERT INTO research.tech_watcher_batches (
    batch_id, ran_at, items_filtered, items_relevant, clusters,
    clusters_promotable, candidates_proposed, candidates_rejected, llm_model
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
""".strip()

SELECT_LAST_BATCH_SQL = """
SELECT ran_at FROM research.tech_watcher_batches ORDER BY ran_at DESC LIMIT 1
""".strip()

SELECT_CANDIDATES_BY_STATUS_SQL = """
SELECT
    candidate_id, name, archetype, status, thesis, confidence,
    expected_impact_horizon, kill_criteria, falsifier_horizon,
    source_classes, rejection_reason, decided_at, decision_note, created_at
FROM research.world_changers
WHERE status = ANY($1::text[])
ORDER BY created_at DESC
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


class PostgresCandidateStore:
    """Sink for synthesized candidates, their evidence, and batch records."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_WORLD_CHANGERS_TABLE_SQL)
            await conn.execute(CREATE_WORLD_CHANGERS_STATUS_INDEX_SQL)
            await conn.execute(ADD_DECIDED_AT_COLUMN_SQL)
            await conn.execute(ADD_DECISION_NOTE_COLUMN_SQL)
            await conn.execute(CREATE_EVIDENCE_TABLE_SQL)
            await conn.execute(CREATE_BATCHES_TABLE_SQL)

    async def last_batch_at(self) -> datetime | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_LAST_BATCH_SQL)
        if row is None:
            return None
        value = row["ran_at"]
        return value if isinstance(value, datetime) else None

    async def insert_candidate(
        self,
        *,
        candidate_id: str,
        name: str,
        archetype: str,
        status: str,
        thesis: str,
        confidence: str,
        expected_impact_horizon: str,
        kill_criteria: list[Any],
        falsifier_horizon: str | None,
        dependency_graph_seed: list[Any] | None,
        source_classes: list[str],
        score: dict[str, Any] | None,
        rejection_reason: str | None,
        llm_model: str,
        batch_id: str,
        raw_response: dict[str, Any],
        created_at: datetime,
        evidence: Sequence[tuple[str, str, str | None]] = (),
    ) -> None:
        """Insert one candidate plus its evidence rows in one transaction.

        ``evidence`` rows are ``(item_id, source_class, note)``.
        """

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    INSERT_CANDIDATE_SQL,
                    candidate_id,
                    name,
                    archetype,
                    status,
                    thesis,
                    confidence,
                    expected_impact_horizon,
                    json.dumps(kill_criteria, separators=(",", ":")),
                    falsifier_horizon,
                    _json_or_none(dependency_graph_seed),
                    json.dumps(source_classes, separators=(",", ":")),
                    _json_or_none(score),
                    rejection_reason,
                    llm_model,
                    batch_id,
                    json.dumps(raw_response, separators=(",", ":")),
                    created_at,
                )
                for item_id, source_class, note in evidence:
                    await conn.execute(
                        INSERT_EVIDENCE_SQL, candidate_id, item_id, source_class, note
                    )

    async def insert_batch(
        self,
        *,
        batch_id: str,
        ran_at: datetime,
        items_filtered: int,
        items_relevant: int,
        clusters: int,
        clusters_promotable: int,
        candidates_proposed: int,
        candidates_rejected: int,
        llm_model: str,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_BATCH_SQL,
                batch_id,
                ran_at,
                items_filtered,
                items_relevant,
                clusters,
                clusters_promotable,
                candidates_proposed,
                candidates_rejected,
                llm_model,
            )

    async def candidates_by_status(self, statuses: Sequence[str]) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_CANDIDATES_BY_STATUS_SQL, list(statuses))
        return [dict(row) for row in rows]


def _json_or_none(value: object) -> str | None:
    if value is None:
        return None
    return json.dumps(value, separators=(",", ":"))
