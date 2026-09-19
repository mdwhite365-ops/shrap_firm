"""Chunk, embed and index the firm's stored text into Qdrant.

The pipeline the architecture asked for and nobody built: *"Writes: structured
signal records to PostgreSQL, full text to Qdrant"*
(``docs/02-architecture.md``, Intelligence and Structural Analysis). Qdrant ran
for two and a half months holding zero collections while 156 MB of filing and
paper text sat in Postgres, searchable only by exact string match or by paying
an LLM to read it.

**Point ids are deterministic.** A UUIDv5 over ``(source, ref, chunk_index)``
means re-indexing a document overwrites its chunks instead of duplicating them.
Without that, a resumed run after a crash would leave two copies of every
passage it had already written, each retrievable, each looking authoritative.
Idempotency here is what makes the run safe to interrupt.

**The cursor advances per batch, not per run**, so an interrupted pass keeps
what it finished. It advances *after* the upsert returns, and the upsert waits
for durability — the ordering matters, because a cursor ahead of the index
points at documents nothing will ever revisit.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from shrap.common.qdrant_client import DEFAULT_COLLECTION, QdrantClient
from shrap.intelligence.corpus_index.chunker import (
    DEFAULT_CHUNK_CHARS,
    DEFAULT_OVERLAP_CHARS,
    iter_chunks,
)
from shrap.intelligence.corpus_index.embedder import OllamaEmbedder
from shrap.intelligence.corpus_index.store import (
    SOURCE_FILINGS,
    SOURCE_RESEARCH,
    CorpusDocument,
    CorpusStore,
)

log = structlog.get_logger(__name__)

# A fixed namespace so the same document always produces the same point ids
# across runs, machines and rebuilds. Generated once and pinned here; changing
# it orphans every existing point rather than updating it.
POINT_NAMESPACE = uuid.UUID("6f3f1c2e-9d4a-5b8c-a7e1-2d9f4b6c8a03")

# How many documents to pull per batch. Filings average 408 KB, so a large batch
# is a large amount of text in memory at once; research items average 5.7 KB.
# Small enough for the worst case rather than tuned for the average.
DEFAULT_DOC_BATCH = 20


def point_id(source: str, ref: str, chunk_index: int) -> str:
    """Stable id for one chunk of one document."""

    return str(uuid.uuid5(POINT_NAMESPACE, f"{source}:{ref}:{chunk_index}"))


@dataclass
class IndexProgress:
    """What one pass did, for the log line and the cursor."""

    documents: int = 0
    chunks: int = 0
    skipped_empty: int = 0
    last_fetched_at: datetime | None = None
    last_ref: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self.documents == 0


@dataclass(frozen=True, slots=True)
class IndexerConfig:
    collection: str = DEFAULT_COLLECTION
    chunk_chars: int = DEFAULT_CHUNK_CHARS
    overlap_chars: int = DEFAULT_OVERLAP_CHARS
    doc_batch: int = DEFAULT_DOC_BATCH
    max_documents: int | None = None
    """Stop after this many documents. ``None`` means drain the source."""


class CorpusIndexer:
    """One source's worth of chunk → embed → upsert, resumable."""

    def __init__(
        self,
        store: CorpusStore,
        embedder: OllamaEmbedder,
        qdrant: QdrantClient,
        config: IndexerConfig | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._qdrant = qdrant
        self._config = config or IndexerConfig()

    async def ensure_ready(self) -> int:
        """Create the collection sized to whatever the model actually returns.

        Returns the dimension. Refuses rather than writes when an existing
        collection disagrees: a 768-wide vector in a 1024-wide collection is
        rejected per-request by Qdrant, but finding that out after embedding
        ten thousand chunks wastes the whole pass.
        """

        await self._store.ensure_schema()
        dimension = await self._embedder.probe_dimension()
        existing = await self._qdrant.dimension_of(self._config.collection)
        if existing is not None and existing != dimension:
            raise RuntimeError(
                f"collection {self._config.collection!r} stores {existing}-dim vectors "
                f"but {self._embedder.model} produces {dimension}-dim. "
                "Embedding model changed: index into a new collection, or drop this one "
                "deliberately — it cannot be migrated in place."
            )
        created = await self._qdrant.ensure_collection(self._config.collection, dimension=dimension)
        log.info(
            "corpus_index.collection_ready",
            collection=self._config.collection,
            dimension=dimension,
            created=created,
        )
        return dimension

    async def index_source(
        self,
        source: str,
        fetch: Callable[[datetime | None, str, int], Awaitable[list[CorpusDocument]]],
    ) -> IndexProgress:
        """Drain one source from its cursor, batch by batch."""

        progress = IndexProgress()
        since, ref = await self._store.cursor_for(source)

        while True:
            remaining = self._remaining(progress.documents)
            if remaining == 0:
                break
            batch = await fetch(since, ref, min(self._config.doc_batch, remaining))
            if not batch:
                break

            written = await self._index_batch(batch, progress)
            since = batch[-1].fetched_at
            ref = batch[-1].ref
            progress.last_fetched_at = since
            progress.last_ref = ref
            progress.documents += len(batch)

            await self._store.advance_cursor(
                source,
                last_fetched_at=since,
                last_ref=ref,
                documents=len(batch),
                points=written,
            )
            log.info(
                "corpus_index.batch",
                source=source,
                documents=progress.documents,
                chunks=progress.chunks,
                last_ref=ref,
            )
        return progress

    def _remaining(self, done: int) -> int:
        if self._config.max_documents is None:
            return self._config.doc_batch
        return max(0, self._config.max_documents - done)

    async def _index_batch(self, batch: Sequence[CorpusDocument], progress: IndexProgress) -> int:
        texts: list[str] = []
        metas: list[tuple[CorpusDocument, int]] = []
        for doc in batch:
            chunks = list(
                iter_chunks(
                    doc.body,
                    chunk_chars=self._config.chunk_chars,
                    overlap_chars=self._config.overlap_chars,
                )
            )
            if not chunks:
                # A row whose body is whitespace. Counted rather than dropped
                # silently: "indexed 0 chunks from 400 documents" should be
                # visible, because it is what a broken text column looks like.
                progress.skipped_empty += 1
                continue
            for index, chunk in chunks:
                texts.append(chunk)
                metas.append((doc, index))

        if not texts:
            return 0

        vectors = await self._embedder.embed(texts)
        points: list[dict[str, Any]] = []
        for (doc, index), vector, text in zip(metas, vectors, texts, strict=True):
            payload = doc.payload()
            payload["chunk"] = index
            # The chunk text rides along with its vector so a hit is readable
            # without a second round trip to Postgres. It is the difference
            # between a retrieval result someone can judge and a row id.
            payload["text"] = text
            points.append(
                {
                    "id": point_id(doc.source, doc.ref, index),
                    "vector": vector,
                    "payload": payload,
                }
            )
        written = await self._qdrant.upsert(points, self._config.collection)
        progress.chunks += written
        return written

    async def index_all(self) -> dict[str, IndexProgress]:
        """Both sources, filings first — they are fewer and larger."""

        return {
            SOURCE_FILINGS: await self.index_source(SOURCE_FILINGS, self._store.next_filings),
            SOURCE_RESEARCH: await self.index_source(
                SOURCE_RESEARCH, self._store.next_research_items
            ),
        }


__all__ = [
    "DEFAULT_DOC_BATCH",
    "POINT_NAMESPACE",
    "CorpusIndexer",
    "IndexProgress",
    "IndexerConfig",
    "point_id",
]
