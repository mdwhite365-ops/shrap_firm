"""Persistence for the Tech Watcher ingest pass.

Two tables (Tech Watcher spec §State):

- ``research.raw_source_items`` — every ingested item, append-only via
  idempotent insert on ``item_id``. ``filtered_at`` / ``synthesized_at``
  are NULL until the later pipeline slices touch the item.
- ``research.ingest_cursors`` — one row per source with the newest external
  timestamp and running item count, updated **in the same transaction** as
  the batch insert so a crashed mid-batch run never double-advances.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Protocol

from shrap.research.tech_watcher.sources import RawSourceItem

CREATE_RESEARCH_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS research"

CREATE_RAW_ITEMS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.raw_source_items (
    item_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    kind TEXT,
    title TEXT NOT NULL,
    summary TEXT,
    url TEXT,
    external_ts TIMESTAMPTZ,
    payload JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    filtered_at TIMESTAMPTZ,
    filter_result JSONB,
    synthesized_at TIMESTAMPTZ
)
""".strip()

# Idempotent migration for tables created by the slice-A deploy.
ADD_FILTER_RESULT_COLUMN_SQL = """
ALTER TABLE research.raw_source_items
ADD COLUMN IF NOT EXISTS filter_result JSONB
""".strip()

CREATE_RAW_ITEMS_SOURCE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS raw_source_items_source_idx
ON research.raw_source_items (source, external_ts DESC)
""".strip()

CREATE_RAW_ITEMS_UNPROCESSED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS raw_source_items_unfiltered_idx
ON research.raw_source_items (fetched_at)
WHERE filtered_at IS NULL
""".strip()

CREATE_INGEST_CURSORS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.ingest_cursors (
    source TEXT PRIMARY KEY,
    last_external_ts TIMESTAMPTZ,
    last_item_id TEXT,
    items_seen BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip()

INSERT_RAW_ITEM_SQL = """
INSERT INTO research.raw_source_items (
    item_id, source, kind, title, summary, url, external_ts, payload, fetched_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
ON CONFLICT (item_id) DO NOTHING
""".strip()

UPSERT_CURSOR_SQL = """
INSERT INTO research.ingest_cursors (
    source, last_external_ts, last_item_id, items_seen, updated_at
)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (source) DO UPDATE SET
    last_external_ts = GREATEST(
        COALESCE(EXCLUDED.last_external_ts, research.ingest_cursors.last_external_ts),
        COALESCE(research.ingest_cursors.last_external_ts, EXCLUDED.last_external_ts)
    ),
    last_item_id = EXCLUDED.last_item_id,
    items_seen = research.ingest_cursors.items_seen + EXCLUDED.items_seen,
    updated_at = EXCLUDED.updated_at
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


class PostgresRawItemStore:
    """Idempotent raw-item sink with atomically advanced per-source cursors."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_RESEARCH_SCHEMA_SQL)
            await conn.execute(CREATE_RAW_ITEMS_TABLE_SQL)
            await conn.execute(CREATE_RAW_ITEMS_SOURCE_INDEX_SQL)
            await conn.execute(CREATE_RAW_ITEMS_UNPROCESSED_INDEX_SQL)
            await conn.execute(ADD_FILTER_RESULT_COLUMN_SQL)
            await conn.execute(CREATE_INGEST_CURSORS_TABLE_SQL)

    async def upsert_batch(
        self, source: str, items: Sequence[RawSourceItem], fetched_at: datetime
    ) -> int:
        """Insert a batch and advance the source cursor in one transaction.

        Returns the number of genuinely new rows (re-delivered items hit the
        ON CONFLICT and count zero).
        """

        inserted = 0
        newest_ts: datetime | None = None
        last_item_id: str | None = None
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for item in items:
                    result = await conn.execute(
                        INSERT_RAW_ITEM_SQL,
                        item.item_id,
                        item.source,
                        item.kind,
                        item.title,
                        item.summary,
                        item.url,
                        item.external_ts,
                        json.dumps(item.payload, separators=(",", ":")),
                        fetched_at,
                    )
                    if str(result).endswith(" 1"):
                        inserted += 1
                        last_item_id = item.item_id
                        if item.external_ts is not None and (
                            newest_ts is None or item.external_ts > newest_ts
                        ):
                            newest_ts = item.external_ts
                await conn.execute(
                    UPSERT_CURSOR_SQL,
                    source,
                    newest_ts,
                    last_item_id,
                    inserted,
                    fetched_at,
                )
        return inserted
