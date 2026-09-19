"""What the corpus index reads, and where it keeps its place.

Two sources, both text the firm already stores and has never been able to search
by meaning:

``intelligence.filings.full_text``          169 rows, 69 MB — 8-K bodies
``research.raw_source_items.document_text`` 15,318 rows, 87 MB — arXiv + EDGAR

**Read-only over both.** They have other owners (the Filing Processor and the
Tech Watcher), and the tier-3 rule in this repo is that a reader which creates
or migrates a table it does not own papers over an infrastructure fault. The
only table this module owns is its own cursor.

**The cursor is per-source and ordered by ``fetched_at``**, the same shape the
Filing Processor's poll cursor uses. A tie-break on the row's own identifier is
included because ``fetched_at`` is not unique — a backfill writes hundreds of
rows in the same second, and a cursor on timestamp alone would either re-index
them every pass or skip all but one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

CREATE_RESEARCH_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS research"

# Owned by this module. One row per source.
CREATE_CURSOR_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.corpus_index_cursor (
    source TEXT PRIMARY KEY,
    last_fetched_at TIMESTAMPTZ,
    last_ref TEXT,
    documents_indexed BIGINT NOT NULL DEFAULT 0,
    points_written BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip()

SELECT_CURSOR_SQL = """
SELECT last_fetched_at, last_ref FROM research.corpus_index_cursor WHERE source = $1
""".strip()

UPSERT_CURSOR_SQL = """
INSERT INTO research.corpus_index_cursor (
    source, last_fetched_at, last_ref, documents_indexed, points_written, updated_at
)
VALUES ($1, $2, $3, $4, $5, now())
ON CONFLICT (source) DO UPDATE SET
    last_fetched_at = EXCLUDED.last_fetched_at,
    last_ref = EXCLUDED.last_ref,
    documents_indexed = research.corpus_index_cursor.documents_indexed + EXCLUDED.documents_indexed,
    points_written = research.corpus_index_cursor.points_written + EXCLUDED.points_written,
    updated_at = now()
""".strip()

# `(fetched_at, accession) > ($1, $2)` as a row comparison, which is the correct
# way to page a non-unique ordering key. Comparing the columns separately would
# either drop rows sharing a timestamp with the cursor or revisit them forever.
SELECT_FILINGS_SQL = """
SELECT accession AS ref, symbol, title, company, filing_url AS url,
       filing_date, fetched_at, full_text AS body
FROM intelligence.filings
WHERE full_text IS NOT NULL
  AND fetched_at IS NOT NULL
  AND ($1::timestamptz IS NULL OR (fetched_at, accession) > ($1::timestamptz, $2::text))
ORDER BY fetched_at, accession
LIMIT $3
""".strip()

SELECT_RAW_ITEMS_SQL = """
SELECT item_id AS ref, source AS feed, title, url, external_ts, fetched_at,
       document_text AS body
FROM research.raw_source_items
WHERE document_text IS NOT NULL
  AND fetched_at IS NOT NULL
  AND ($1::timestamptz IS NULL OR (fetched_at, item_id) > ($1::timestamptz, $2::text))
ORDER BY fetched_at, item_id
LIMIT $3
""".strip()

COUNT_FILINGS_SQL = """
SELECT count(*) AS n, coalesce(sum(length(full_text)), 0) AS chars
FROM intelligence.filings WHERE full_text IS NOT NULL
""".strip()

COUNT_RAW_ITEMS_SQL = """
SELECT count(*) AS n, coalesce(sum(length(document_text)), 0) AS chars
FROM research.raw_source_items WHERE document_text IS NOT NULL
""".strip()

SOURCE_FILINGS = "filings"
SOURCE_RESEARCH = "research-items"


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    """One document to index, normalised across the two sources.

    ``ref`` is the source's own identifier — an accession number or a Tech
    Watcher item id — so a retrieved chunk points back at a row someone can go
    and read, not at an opaque index key.
    """

    source: str
    ref: str
    title: str
    url: str
    body: str
    fetched_at: datetime
    symbol: str | None = None
    feed: str | None = None
    doc_ts: datetime | None = None

    def payload(self) -> dict[str, Any]:
        """What travels with every vector from this document."""

        out: dict[str, Any] = {
            "source": self.source,
            "ref": self.ref,
            "title": self.title[:500],
            "url": self.url,
        }
        if self.symbol:
            out["symbol"] = self.symbol
        if self.feed:
            out["feed"] = self.feed
        if self.doc_ts is not None:
            out["doc_ts"] = self.doc_ts.isoformat()
        return out


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]: ...

    async def fetchrow(self, sql: str, *args: object) -> Mapping[str, Any] | None: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class CorpusStore:
    """Reads the two corpora; owns only the cursor."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_RESEARCH_SCHEMA_SQL)
            await conn.execute(CREATE_CURSOR_TABLE_SQL)

    async def cursor_for(self, source: str) -> tuple[datetime | None, str]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_CURSOR_SQL, source)
        if row is None:
            return None, ""
        return _as_utc(row["last_fetched_at"]), str(row["last_ref"] or "")

    async def advance_cursor(
        self,
        source: str,
        *,
        last_fetched_at: datetime | None,
        last_ref: str,
        documents: int,
        points: int,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                UPSERT_CURSOR_SQL, source, last_fetched_at, last_ref, documents, points
            )

    async def corpus_size(self) -> dict[str, tuple[int, int]]:
        """``{source: (documents, characters)}`` — for the pre-run estimate."""

        async with self._pool.acquire() as conn:
            filings = await conn.fetchrow(COUNT_FILINGS_SQL)
            items = await conn.fetchrow(COUNT_RAW_ITEMS_SQL)
        return {
            SOURCE_FILINGS: (int(filings["n"]), int(filings["chars"])) if filings else (0, 0),
            SOURCE_RESEARCH: (int(items["n"]), int(items["chars"])) if items else (0, 0),
        }

    async def next_filings(
        self, since: datetime | None, ref: str, limit: int
    ) -> list[CorpusDocument]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_FILINGS_SQL, since, ref, limit)
        return [
            CorpusDocument(
                source=SOURCE_FILINGS,
                ref=str(r["ref"]),
                title=str(r["title"] or r["company"] or r["ref"]),
                url=str(r["url"] or ""),
                body=str(r["body"] or ""),
                fetched_at=_as_utc(r["fetched_at"]) or datetime.now(UTC),
                symbol=str(r["symbol"]) if r["symbol"] else None,
                doc_ts=_as_utc(r["filing_date"]),
            )
            for r in rows
        ]

    async def next_research_items(
        self, since: datetime | None, ref: str, limit: int
    ) -> list[CorpusDocument]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_RAW_ITEMS_SQL, since, ref, limit)
        return [
            CorpusDocument(
                source=SOURCE_RESEARCH,
                ref=str(r["ref"]),
                title=str(r["title"] or r["ref"]),
                url=str(r["url"] or ""),
                body=str(r["body"] or ""),
                fetched_at=_as_utc(r["fetched_at"]) or datetime.now(UTC),
                feed=str(r["feed"]) if r["feed"] else None,
                doc_ts=_as_utc(r["external_ts"]),
            )
            for r in rows
        ]


__all__ = [
    "COUNT_FILINGS_SQL",
    "COUNT_RAW_ITEMS_SQL",
    "CREATE_CURSOR_TABLE_SQL",
    "SELECT_FILINGS_SQL",
    "SELECT_RAW_ITEMS_SQL",
    "SOURCE_FILINGS",
    "SOURCE_RESEARCH",
    "UPSERT_CURSOR_SQL",
    "AsyncPool",
    "CorpusDocument",
    "CorpusStore",
]
