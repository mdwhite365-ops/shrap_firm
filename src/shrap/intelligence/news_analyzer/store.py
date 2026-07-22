"""Persistence for the News Analyzer (spec §State).

Three tables in the ``intelligence`` schema:

- ``intelligence.news_items`` — every fetched item (the full denominator),
  keyed by the Alpaca news id, idempotent upsert. ``scored_at`` / ``verdict``
  are NULL until the scoring pass touches the item.
- ``intelligence.news_cursor`` — one row per feed with the newest external
  timestamp and running item count, advanced **in the same transaction** as
  the batch upsert so a crashed mid-batch run never double-advances.
- ``intelligence.news_verdict_history`` — append-only score log: prompt
  version, tier, model, and the verdict fields, stamped per verdict (KI-007).
  A re-score overwrites ``news_items.verdict`` but never the history.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from shrap.intelligence.news_analyzer.client import NewsItem
from shrap.intelligence.news_analyzer.scorer import MaterialityVerdict

CREATE_INTELLIGENCE_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS intelligence"

CREATE_NEWS_ITEMS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS intelligence.news_items (
    item_id TEXT PRIMARY KEY,
    headline TEXT NOT NULL,
    summary TEXT,
    author TEXT,
    url TEXT,
    news_source TEXT,
    symbols JSONB NOT NULL,
    published_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    payload JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    scored_at TIMESTAMPTZ,
    verdict JSONB
)
""".strip()

CREATE_NEWS_ITEMS_UNSCORED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS news_items_unscored_idx
ON intelligence.news_items (fetched_at)
WHERE scored_at IS NULL
""".strip()

CREATE_NEWS_CURSOR_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS intelligence.news_cursor (
    feed TEXT PRIMARY KEY,
    last_external_ts TIMESTAMPTZ,
    last_item_id TEXT,
    items_seen BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip()

CREATE_NEWS_VERDICT_HISTORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS intelligence.news_verdict_history (
    item_id TEXT NOT NULL,
    prompt_version INTEGER NOT NULL,
    tier TEXT NOT NULL,
    model TEXT NOT NULL,
    relevant BOOLEAN NOT NULL,
    category TEXT NOT NULL,
    materiality INTEGER NOT NULL,
    summary TEXT,
    symbols JSONB NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL
)
""".strip()

CREATE_NEWS_VERDICT_HISTORY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS news_verdict_history_item_idx
ON intelligence.news_verdict_history (item_id, decided_at DESC)
""".strip()

INSERT_NEWS_ITEM_SQL = """
INSERT INTO intelligence.news_items (
    item_id, headline, summary, author, url, news_source,
    symbols, published_at, updated_at, payload, fetched_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10::jsonb, $11)
ON CONFLICT (item_id) DO NOTHING
""".strip()

UPSERT_NEWS_CURSOR_SQL = """
INSERT INTO intelligence.news_cursor (
    feed, last_external_ts, last_item_id, items_seen, updated_at
)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (feed) DO UPDATE SET
    last_external_ts = GREATEST(
        COALESCE(EXCLUDED.last_external_ts, intelligence.news_cursor.last_external_ts),
        COALESCE(intelligence.news_cursor.last_external_ts, EXCLUDED.last_external_ts)
    ),
    last_item_id = EXCLUDED.last_item_id,
    items_seen = intelligence.news_cursor.items_seen + EXCLUDED.items_seen,
    updated_at = EXCLUDED.updated_at
""".strip()

SELECT_NEWS_CURSOR_SQL = """
SELECT last_external_ts FROM intelligence.news_cursor WHERE feed = $1
""".strip()

SELECT_UNSCORED_SQL = """
SELECT item_id, headline, summary, symbols, published_at
FROM intelligence.news_items
WHERE scored_at IS NULL
ORDER BY fetched_at
LIMIT $1
""".strip()

INSERT_NEWS_VERDICT_HISTORY_SQL = """
INSERT INTO intelligence.news_verdict_history (
    item_id, prompt_version, tier, model, relevant,
    category, materiality, summary, symbols, decided_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
""".strip()

MARK_SCORED_SQL = """
UPDATE intelligence.news_items
SET scored_at = $2, verdict = $3::jsonb
WHERE item_id = $1
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


@dataclass(frozen=True, slots=True)
class ScorableItem:
    """The slice of a stored news item the scorer needs."""

    item_id: str
    headline: str
    summary: str | None
    symbols: tuple[str, ...]
    published_at: datetime | None


def _decode_symbols(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ()
    if isinstance(value, list):
        return tuple(s for s in value if isinstance(s, str))
    return ()


class PostgresNewsStore:
    """Idempotent news-item sink with an atomically advanced feed cursor."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_INTELLIGENCE_SCHEMA_SQL)
            await conn.execute(CREATE_NEWS_ITEMS_TABLE_SQL)
            await conn.execute(CREATE_NEWS_ITEMS_UNSCORED_INDEX_SQL)
            await conn.execute(CREATE_NEWS_CURSOR_TABLE_SQL)
            await conn.execute(CREATE_NEWS_VERDICT_HISTORY_TABLE_SQL)
            await conn.execute(CREATE_NEWS_VERDICT_HISTORY_INDEX_SQL)

    async def cursor_ts(self, feed: str) -> datetime | None:
        """Return the newest external timestamp seen for ``feed``, if any."""

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_NEWS_CURSOR_SQL, feed)
        if row is None:
            return None
        value = row["last_external_ts"]
        return value if isinstance(value, datetime) else None

    async def upsert_items(self, feed: str, items: Sequence[NewsItem], fetched_at: datetime) -> int:
        """Insert a batch and advance the feed cursor in one transaction.

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
                        INSERT_NEWS_ITEM_SQL,
                        item.item_id,
                        item.headline,
                        item.summary,
                        item.author,
                        item.url,
                        item.news_source,
                        json.dumps(list(item.symbols), separators=(",", ":")),
                        item.published_at,
                        item.updated_at,
                        json.dumps(item.payload, separators=(",", ":"), default=str),
                        fetched_at,
                    )
                    if str(result).endswith(" 1"):
                        inserted += 1
                        last_item_id = item.item_id
                        if item.published_at is not None and (
                            newest_ts is None or item.published_at > newest_ts
                        ):
                            newest_ts = item.published_at
                await conn.execute(
                    UPSERT_NEWS_CURSOR_SQL,
                    feed,
                    newest_ts,
                    last_item_id,
                    inserted,
                    fetched_at,
                )
        return inserted

    async def select_unscored(self, limit: int) -> list[ScorableItem]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_UNSCORED_SQL, limit)
        return [
            ScorableItem(
                item_id=str(row["item_id"]),
                headline=str(row["headline"]),
                summary=None if row["summary"] is None else str(row["summary"]),
                symbols=_decode_symbols(row["symbols"]),
                published_at=(
                    row["published_at"] if isinstance(row["published_at"], datetime) else None
                ),
            )
            for row in rows
        ]

    async def append_verdict(
        self,
        verdict: MaterialityVerdict,
        prompt_version: int,
        tier: str,
        model: str,
        decided_at: datetime,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_NEWS_VERDICT_HISTORY_SQL,
                verdict.item_id,
                prompt_version,
                tier,
                model,
                verdict.relevant,
                verdict.category,
                verdict.materiality,
                verdict.summary,
                json.dumps(list(verdict.symbols), separators=(",", ":")),
                decided_at,
            )

    async def mark_scored(
        self,
        verdict: MaterialityVerdict,
        prompt_version: int,
        model: str,
        scored_at: datetime,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                MARK_SCORED_SQL,
                verdict.item_id,
                scored_at,
                json.dumps(
                    {
                        "relevant": verdict.relevant,
                        "symbols": list(verdict.symbols),
                        "category": verdict.category,
                        "materiality": verdict.materiality,
                        "summary": verdict.summary,
                        "model": model,
                        "prompt_version": prompt_version,
                    },
                    separators=(",", ":"),
                ),
            )


__all__ = [
    "INSERT_NEWS_ITEM_SQL",
    "INSERT_NEWS_VERDICT_HISTORY_SQL",
    "MARK_SCORED_SQL",
    "SELECT_UNSCORED_SQL",
    "UPSERT_NEWS_CURSOR_SQL",
    "PostgresNewsStore",
    "ScorableItem",
]
