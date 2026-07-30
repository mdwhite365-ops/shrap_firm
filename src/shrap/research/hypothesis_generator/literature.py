"""The contract between the literature feed and the proposer.

This module owns ``research.literature_items`` — one row per published item
that describes a testable market effect. Tech Watcher's q-fin card (the
prerequisite in ``docs/agents/research/hypothesis-generator.md``) writes rows
here; the generator reads them. The table is defined on this side of the seam
on purpose: the consumer's requirements are the contract, and a producer
written first would have had to guess them.

**Why a table and not just the event.** ``research.literature.ingested`` is a
trigger, not a record. A proposer that learned about literature only from a
stream would silently skip everything published while it was down, and Redis
Streams trim. The row is the truth; the event says a row appeared.

**``proposed_at`` is what makes a re-run cheap.** Every item the generator has
already considered carries its outcome, so running the CLI twice does not spend
a second round of model calls re-deciding settled items. It also means the
firm's record of *what it read and declined to act on* is queryable, which is
the half of a research funnel that normally evaporates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

# Emitted by Tech Watcher when an item clears the literature filter. Declared
# here so the producer and the consumer cannot drift on the name.
STREAM_LITERATURE_INGESTED = "research.literature.ingested"

# What the generator decided about an item. Stored on the row so a re-run skips
# settled work and a person can ask "what did we read and not act on".
OUTCOME_PROPOSED = "proposed"
OUTCOME_REFUSED = "refused"
OUTCOME_CAPABILITY_GAP = "capability-gap"

CREATE_LITERATURE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.literature_items (
    item_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    category TEXT,
    title TEXT NOT NULL,
    abstract TEXT NOT NULL,
    url TEXT,
    published_at TIMESTAMPTZ,
    accepted_reason TEXT,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    proposed_at TIMESTAMPTZ,
    outcome TEXT,
    outcome_detail TEXT
)
""".strip()

CREATE_LITERATURE_PENDING_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS literature_items_pending_idx
ON research.literature_items (published_at DESC)
WHERE proposed_at IS NULL
""".strip()

# Newest first: a proposer with a backlog should spend its calls on current
# literature, because the old items will still be there tomorrow and a claim
# published this week is the one nobody has tested yet.
SELECT_PENDING_LITERATURE_SQL = """
SELECT item_id, source, category, title, abstract, url, published_at
FROM research.literature_items
WHERE proposed_at IS NULL
ORDER BY published_at DESC NULLS LAST, item_id
LIMIT $1
""".strip()

UPSERT_LITERATURE_SQL = """
INSERT INTO research.literature_items (
    item_id, source, category, title, abstract, url, published_at, accepted_reason
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (item_id) DO NOTHING
""".strip()

MARK_LITERATURE_PROCESSED_SQL = """
UPDATE research.literature_items
SET proposed_at = $2, outcome = $3, outcome_detail = $4
WHERE item_id = $1
""".strip()


@dataclass(frozen=True, slots=True)
class LiteratureItem:
    """One published claim the proposer may try to turn into a strategy."""

    item_id: str
    source: str
    title: str
    abstract: str
    url: str | None = None
    published_at: datetime | None = None
    category: str | None = None

    @property
    def citation_hint(self) -> str:
        """What the model is shown as a starting point for the citation.

        A hint, never the citation itself. The ``prior`` the proposer must
        produce names authors and a year, and those come from reading the item
        — filling them in from the item's metadata would let an item with no
        attribution pass the one check that exists to catch freelancing.
        """

        parts = [self.title]
        if self.published_at is not None:
            parts.append(f"published {self.published_at.date().isoformat()}")
        if self.url:
            parts.append(self.url)
        return " — ".join(parts)


def item_from_mapping(row: Mapping[str, Any]) -> LiteratureItem:
    published = row.get("published_at")
    return LiteratureItem(
        item_id=str(row["item_id"]),
        source=str(row["source"]),
        title=str(row["title"]),
        abstract=str(row["abstract"]),
        url=None if row.get("url") is None else str(row["url"]),
        published_at=published if isinstance(published, datetime) else None,
        category=None if row.get("category") is None else str(row["category"]),
    )


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


class LiteratureStore(Protocol):
    """The slice of persistence the generator needs."""

    async def pending(self, limit: int) -> list[LiteratureItem]: ...

    async def mark_processed(self, item_id: str, outcome: str, detail: str) -> None: ...


class PostgresLiteratureStore:
    """``research.literature_items`` — read by the generator, written by Tech Watcher."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS research")
            await conn.execute(CREATE_LITERATURE_TABLE_SQL)
            await conn.execute(CREATE_LITERATURE_PENDING_INDEX_SQL)

    async def pending(self, limit: int) -> list[LiteratureItem]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_PENDING_LITERATURE_SQL, limit)
        return [item_from_mapping(row) for row in rows]

    async def record(self, item: LiteratureItem, accepted_reason: str) -> None:
        """Idempotent insert. Re-ingesting an item never resets its outcome."""

        async with self._pool.acquire() as conn:
            await conn.execute(
                UPSERT_LITERATURE_SQL,
                item.item_id,
                item.source,
                item.category,
                item.title,
                item.abstract,
                item.url,
                item.published_at,
                accepted_reason,
            )

    async def mark_processed(self, item_id: str, outcome: str, detail: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                MARK_LITERATURE_PROCESSED_SQL,
                item_id,
                datetime.now(UTC),
                outcome,
                detail[:500],
            )


__all__ = [
    "CREATE_LITERATURE_TABLE_SQL",
    "OUTCOME_CAPABILITY_GAP",
    "OUTCOME_PROPOSED",
    "OUTCOME_REFUSED",
    "STREAM_LITERATURE_INGESTED",
    "LiteratureItem",
    "LiteratureStore",
    "PostgresLiteratureStore",
    "item_from_mapping",
]
