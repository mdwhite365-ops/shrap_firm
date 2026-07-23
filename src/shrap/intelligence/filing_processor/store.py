"""Persistence for the Filing Processor (spec §State / §Outputs).

Own state in the ``intelligence`` schema, plus a read-only poll against the
Tech Watcher's ``research.raw_source_items``:

- ``intelligence.filings`` — every Tier 3-matched 8-K (the denominator), keyed
  by accession, idempotent insert. ``full_text`` / ``item_codes`` /
  ``fetched_at`` are NULL until the fetch pass dereferences the filing;
  ``scored_at`` / ``verdicts`` are NULL until the score pass touches it. A row
  moves discovered → fetched → scored.
- ``intelligence.filing_cursor`` — this agent's own poll cursor over
  ``research.raw_source_items`` (last ``fetched_at`` seen), separate from the
  Tech Watcher's ingest cursor on the same table. Advanced in the same
  transaction as the pending-filing inserts so a crashed poll never
  double-advances.
- ``intelligence.filing_verdict_history`` — append-only score log per
  item-code section: prompt version, tier, model, verdict (KI-007). A re-score
  overwrites ``filings.verdicts`` but never the history.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from shrap.intelligence.filing_processor.client import item_id_from_accession
from shrap.intelligence.filing_processor.scorer import FilingVerdict

CREATE_INTELLIGENCE_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS intelligence"

CREATE_FILINGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS intelligence.filings (
    accession TEXT PRIMARY KEY,
    cik TEXT NOT NULL,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT,
    company TEXT,
    filing_url TEXT,
    filing_date TIMESTAMPTZ,
    item_codes JSONB,
    full_text TEXT,
    payload JSONB NOT NULL,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    fetched_at TIMESTAMPTZ,
    scored_at TIMESTAMPTZ,
    verdicts JSONB
)
""".strip()

CREATE_FILINGS_PENDING_FETCH_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS filings_pending_fetch_idx
ON intelligence.filings (discovered_at)
WHERE fetched_at IS NULL
""".strip()

CREATE_FILINGS_UNSCORED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS filings_unscored_idx
ON intelligence.filings (fetched_at)
WHERE fetched_at IS NOT NULL AND scored_at IS NULL
""".strip()

CREATE_FILING_CURSOR_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS intelligence.filing_cursor (
    feed TEXT PRIMARY KEY,
    last_fetched_at TIMESTAMPTZ,
    items_seen BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip()

CREATE_FILING_VERDICT_HISTORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS intelligence.filing_verdict_history (
    accession TEXT NOT NULL,
    item_code TEXT NOT NULL,
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

CREATE_FILING_VERDICT_HISTORY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS filing_verdict_history_item_idx
ON intelligence.filing_verdict_history (accession, item_code, decided_at DESC)
""".strip()

# Read-only poll against the Tech Watcher's table (never mutated here).
SELECT_CANDIDATE_FILINGS_SQL = """
SELECT item_id, title, url, external_ts, fetched_at
FROM research.raw_source_items
WHERE source = 'sec-edgar' AND kind = '8-K' AND fetched_at > $1
ORDER BY fetched_at
LIMIT $2
""".strip()

# Backfill CLI: explicit accession list / filing-date range instead of the
# cursor. Same read-only table, same source/kind filter.
SELECT_CANDIDATES_BY_ACCESSION_SQL = """
SELECT item_id, title, url, external_ts, fetched_at
FROM research.raw_source_items
WHERE source = 'sec-edgar' AND kind = '8-K' AND item_id = ANY($1)
""".strip()

SELECT_CANDIDATES_BY_DATE_RANGE_SQL = """
SELECT item_id, title, url, external_ts, fetched_at
FROM research.raw_source_items
WHERE source = 'sec-edgar' AND kind = '8-K' AND external_ts >= $1 AND external_ts < $2
ORDER BY external_ts
""".strip()

SELECT_FILING_CURSOR_SQL = """
SELECT last_fetched_at FROM intelligence.filing_cursor WHERE feed = $1
""".strip()

UPSERT_FILING_CURSOR_SQL = """
INSERT INTO intelligence.filing_cursor (feed, last_fetched_at, items_seen, updated_at)
VALUES ($1, $2, $3, $4)
ON CONFLICT (feed) DO UPDATE SET
    last_fetched_at = GREATEST(
        COALESCE(EXCLUDED.last_fetched_at, intelligence.filing_cursor.last_fetched_at),
        COALESCE(intelligence.filing_cursor.last_fetched_at, EXCLUDED.last_fetched_at)
    ),
    items_seen = intelligence.filing_cursor.items_seen + EXCLUDED.items_seen,
    updated_at = EXCLUDED.updated_at
""".strip()

INSERT_FILING_SQL = """
INSERT INTO intelligence.filings (
    accession, cik, symbol, source, title, company, filing_url, filing_date, payload, discovered_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
ON CONFLICT (accession) DO NOTHING
""".strip()

SELECT_PENDING_FETCH_SQL = """
SELECT accession, cik, symbol, filing_url
FROM intelligence.filings
WHERE fetched_at IS NULL
ORDER BY discovered_at
LIMIT $1
""".strip()

# Backfill CLI: pending-fetch filings scoped to an explicit accession list.
SELECT_PENDING_FETCH_BY_ACCESSION_SQL = """
SELECT accession, cik, symbol, filing_url
FROM intelligence.filings
WHERE fetched_at IS NULL AND accession = ANY($1)
ORDER BY discovered_at
LIMIT $2
""".strip()

# Backfill CLI: current fetch/score progress per accession (skip decision).
SELECT_FILING_STATES_SQL = """
SELECT accession, fetched_at, scored_at
FROM intelligence.filings
WHERE accession = ANY($1)
""".strip()

MARK_FILING_FETCHED_SQL = """
UPDATE intelligence.filings
SET full_text = $2, item_codes = $3::jsonb, fetched_at = $4
WHERE accession = $1
""".strip()

SELECT_UNSCORED_FILINGS_SQL = """
SELECT accession, symbol, title, company, filing_date, item_codes, full_text
FROM intelligence.filings
WHERE fetched_at IS NOT NULL AND scored_at IS NULL
ORDER BY fetched_at
LIMIT $1
""".strip()

# Backfill CLI: fetched filings scoped to an explicit accession list,
# regardless of ``scored_at`` — the CLI has already decided, via --rescore,
# which accessions belong in this set (store never re-derives the skip).
SELECT_SCORABLE_BY_ACCESSION_SQL = """
SELECT accession, symbol, title, company, filing_date, item_codes, full_text
FROM intelligence.filings
WHERE fetched_at IS NOT NULL AND accession = ANY($1)
ORDER BY fetched_at
""".strip()

INSERT_FILING_VERDICT_HISTORY_SQL = """
INSERT INTO intelligence.filing_verdict_history (
    accession, item_code, prompt_version, tier, model,
    relevant, category, materiality, summary, symbols, decided_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)
""".strip()

MARK_FILING_SCORED_SQL = """
UPDATE intelligence.filings
SET scored_at = $2, verdicts = $3::jsonb
WHERE accession = $1
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
class CandidateRow:
    """One ``research.raw_source_items`` 8-K row seen by the poll pass."""

    item_id: str
    title: str | None
    url: str | None
    filing_date: datetime | None
    fetched_at: datetime | None


@dataclass(frozen=True, slots=True)
class PendingFiling:
    """A Tier 3-matched 8-K recorded before its full text is fetched."""

    accession: str
    cik: str
    symbol: str
    title: str | None
    company: str | None
    filing_url: str | None
    filing_date: datetime | None
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PendingFetch:
    """A stored filing awaiting its full-text fetch."""

    accession: str
    cik: str
    symbol: str
    filing_url: str | None


@dataclass(frozen=True, slots=True)
class ScorableFiling:
    """The slice of a fetched filing the scorer needs."""

    accession: str
    symbol: str
    title: str | None
    company: str | None
    filing_date: datetime | None
    item_codes: tuple[str, ...]
    full_text: str


@dataclass(frozen=True, slots=True)
class FilingState:
    """Current fetch/score progress for one accession (backfill skip decision)."""

    accession: str
    fetched_at: datetime | None
    scored_at: datetime | None


def _decode_str_list(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ()
    if isinstance(value, list):
        return tuple(s for s in value if isinstance(s, str))
    return ()


def _as_dt(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _as_str(value: object) -> str | None:
    return None if value is None else str(value)


def _candidate_rows(rows: Sequence[Mapping[str, Any]]) -> list[CandidateRow]:
    return [
        CandidateRow(
            item_id=str(row["item_id"]),
            title=_as_str(row["title"]),
            url=_as_str(row["url"]),
            filing_date=_as_dt(row["external_ts"]),
            fetched_at=_as_dt(row["fetched_at"]),
        )
        for row in rows
    ]


def _pending_fetch_rows(rows: Sequence[Mapping[str, Any]]) -> list[PendingFetch]:
    return [
        PendingFetch(
            accession=str(row["accession"]),
            cik=str(row["cik"]),
            symbol=str(row["symbol"]),
            filing_url=_as_str(row["filing_url"]),
        )
        for row in rows
    ]


def _scorable_filing_rows(rows: Sequence[Mapping[str, Any]]) -> list[ScorableFiling]:
    return [
        ScorableFiling(
            accession=str(row["accession"]),
            symbol=str(row["symbol"]),
            title=_as_str(row["title"]),
            company=_as_str(row["company"]),
            filing_date=_as_dt(row["filing_date"]),
            item_codes=_decode_str_list(row["item_codes"]),
            full_text="" if row["full_text"] is None else str(row["full_text"]),
        )
        for row in rows
    ]


class PostgresFilingStore:
    """Filing sink with a poll cursor over the Tech Watcher's raw items."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_INTELLIGENCE_SCHEMA_SQL)
            await conn.execute(CREATE_FILINGS_TABLE_SQL)
            await conn.execute(CREATE_FILINGS_PENDING_FETCH_INDEX_SQL)
            await conn.execute(CREATE_FILINGS_UNSCORED_INDEX_SQL)
            await conn.execute(CREATE_FILING_CURSOR_TABLE_SQL)
            await conn.execute(CREATE_FILING_VERDICT_HISTORY_TABLE_SQL)
            await conn.execute(CREATE_FILING_VERDICT_HISTORY_INDEX_SQL)

    async def cursor_ts(self, feed: str) -> datetime | None:
        """Return the newest ``fetched_at`` this poll has advanced past, if any."""

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_FILING_CURSOR_SQL, feed)
        if row is None:
            return None
        return _as_dt(row["last_fetched_at"])

    async def select_candidates(self, since: datetime, limit: int) -> list[CandidateRow]:
        """Read Tech Watcher 8-K rows with ``fetched_at`` after the cursor."""

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_CANDIDATE_FILINGS_SQL, since, limit)
        return _candidate_rows(rows)

    async def select_candidates_by_accessions(
        self, accessions: Sequence[str]
    ) -> list[CandidateRow]:
        """Read Tech Watcher 8-K rows for an explicit accession list (backfill)."""

        item_ids = [item_id_from_accession(a) for a in accessions]
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_CANDIDATES_BY_ACCESSION_SQL, item_ids)
        return _candidate_rows(rows)

    async def select_candidates_by_date_range(
        self, since: datetime, until: datetime
    ) -> list[CandidateRow]:
        """Read Tech Watcher 8-K rows filed in ``[since, until)`` (backfill)."""

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_CANDIDATES_BY_DATE_RANGE_SQL, since, until)
        return _candidate_rows(rows)

    async def record_and_advance(
        self,
        feed: str,
        pendings: Sequence[PendingFiling],
        last_fetched_at: datetime | None,
        seen: int,
        now: datetime,
    ) -> int:
        """Insert matched pending filings and advance the poll cursor atomically.

        Returns the number of genuinely new filings (re-seen accessions hit the
        ON CONFLICT and count zero). The cursor advances past every row seen
        this pass, matched or not, so a re-poll never re-scans the market-wide
        backlog (spec Processing step 1).
        """

        inserted = 0
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for pending in pendings:
                    result = await conn.execute(
                        INSERT_FILING_SQL,
                        pending.accession,
                        pending.cik,
                        pending.symbol,
                        "sec-edgar",
                        pending.title,
                        pending.company,
                        pending.filing_url,
                        pending.filing_date,
                        json.dumps(pending.payload, separators=(",", ":"), default=str),
                        now,
                    )
                    if str(result).endswith(" 1"):
                        inserted += 1
                await conn.execute(UPSERT_FILING_CURSOR_SQL, feed, last_fetched_at, seen, now)
        return inserted

    async def select_pending_fetch(self, limit: int) -> list[PendingFetch]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_PENDING_FETCH_SQL, limit)
        return _pending_fetch_rows(rows)

    async def select_pending_fetch_by_accession(
        self, accessions: Sequence[str], limit: int
    ) -> list[PendingFetch]:
        """Pending-fetch filings scoped to an explicit accession list (backfill)."""

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_PENDING_FETCH_BY_ACCESSION_SQL, list(accessions), limit)
        return _pending_fetch_rows(rows)

    async def select_filing_states(self, accessions: Sequence[str]) -> dict[str, FilingState]:
        """Current fetch/score progress per accession (backfill skip decision)."""

        if not accessions:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_FILING_STATES_SQL, list(accessions))
        return {
            str(row["accession"]): FilingState(
                accession=str(row["accession"]),
                fetched_at=_as_dt(row["fetched_at"]),
                scored_at=_as_dt(row["scored_at"]),
            )
            for row in rows
        }

    async def mark_fetched(
        self,
        accession: str,
        full_text: str,
        item_codes: Sequence[str],
        fetched_at: datetime,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                MARK_FILING_FETCHED_SQL,
                accession,
                full_text,
                json.dumps(list(item_codes), separators=(",", ":")),
                fetched_at,
            )

    async def select_unscored(self, limit: int) -> list[ScorableFiling]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_UNSCORED_FILINGS_SQL, limit)
        return _scorable_filing_rows(rows)

    async def select_scorable_by_accession(self, accessions: Sequence[str]) -> list[ScorableFiling]:
        """Fetched filings scoped to an explicit accession list, regardless of
        ``scored_at`` (backfill CLI; see :data:`SELECT_SCORABLE_BY_ACCESSION_SQL`)."""

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_SCORABLE_BY_ACCESSION_SQL, list(accessions))
        return _scorable_filing_rows(rows)

    async def append_verdict(
        self,
        verdict: FilingVerdict,
        prompt_version: int,
        tier: str,
        model: str,
        decided_at: datetime,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_FILING_VERDICT_HISTORY_SQL,
                verdict.accession,
                verdict.item_code,
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
        accession: str,
        verdicts: Sequence[dict[str, Any]],
        scored_at: datetime,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                MARK_FILING_SCORED_SQL,
                accession,
                scored_at,
                json.dumps(list(verdicts), separators=(",", ":")),
            )


__all__ = [
    "INSERT_FILING_SQL",
    "INSERT_FILING_VERDICT_HISTORY_SQL",
    "MARK_FILING_FETCHED_SQL",
    "MARK_FILING_SCORED_SQL",
    "SELECT_CANDIDATES_BY_ACCESSION_SQL",
    "SELECT_CANDIDATES_BY_DATE_RANGE_SQL",
    "SELECT_CANDIDATE_FILINGS_SQL",
    "SELECT_FILING_STATES_SQL",
    "SELECT_PENDING_FETCH_BY_ACCESSION_SQL",
    "SELECT_PENDING_FETCH_SQL",
    "SELECT_SCORABLE_BY_ACCESSION_SQL",
    "SELECT_UNSCORED_FILINGS_SQL",
    "UPSERT_FILING_CURSOR_SQL",
    "CandidateRow",
    "FilingState",
    "PendingFetch",
    "PendingFiling",
    "PostgresFilingStore",
    "ScorableFiling",
]
