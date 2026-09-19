"""The corpus index: chunking, idempotency, and the cursor's ordering.

Qdrant ran healthy and empty for two and a half months. The tests that matter
here are the ones covering the properties that decide whether a long indexing
run can be trusted and resumed — not whether the HTTP calls are shaped right,
which only a live Qdrant can say.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from shrap.intelligence.corpus_index.chunker import (
    DEFAULT_CHUNK_CHARS,
    MIN_CHUNK_CHARS,
    chunk_text,
    estimate_chunks,
    iter_chunks,
)
from shrap.intelligence.corpus_index.indexer import (
    CorpusIndexer,
    IndexerConfig,
    point_id,
)
from shrap.intelligence.corpus_index.store import (
    SELECT_FILINGS_SQL,
    SELECT_RAW_ITEMS_SQL,
    SOURCE_FILINGS,
    CorpusDocument,
)

# --- chunking -----------------------------------------------------------------


def test_empty_text_produces_no_vectors() -> None:
    """A whitespace-only body must not become one meaningless embedding."""

    assert chunk_text("") == []
    assert chunk_text("   \n\t  ") == []


def test_a_short_document_survives_the_minimum_floor() -> None:
    """A headline is legitimate content and must stay searchable."""

    headline = "NVDA announces a new data-centre GPU"
    assert len(headline) < MIN_CHUNK_CHARS
    assert chunk_text(headline) == [headline]


def test_chunks_overlap_so_a_straddling_sentence_survives_whole() -> None:
    """Meaning crosses boundaries; without overlap it is lost in both chunks."""

    text = "".join(f"{i:04d}-" for i in range(1000))  # 5,000 chars
    chunks = chunk_text(text, chunk_chars=1000, overlap_chars=100)

    assert len(chunks) > 1
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        assert earlier[-50:] in later, "consecutive chunks do not overlap"


def test_chunks_cover_the_whole_document() -> None:
    """Nothing may be dropped: an unindexed tail is silently unsearchable."""

    text = "".join(f"{i:04d}-" for i in range(2000))
    chunks = chunk_text(text, chunk_chars=1000, overlap_chars=100)

    assert chunks[0].startswith(text[:20])
    assert text[-20:] in chunks[-1]


def test_overlap_at_or_above_the_window_is_refused() -> None:
    """Otherwise the window never advances and the indexer hangs silently."""

    with pytest.raises(ValueError, match="smaller than chunk_chars"):
        chunk_text("x" * 100, chunk_chars=100, overlap_chars=100)
    with pytest.raises(ValueError, match="smaller than chunk_chars"):
        chunk_text("x" * 100, chunk_chars=100, overlap_chars=200)


def test_a_real_sized_filing_chunks_rather_than_truncates() -> None:
    """408 KB is the firm's average filing. One vector would be the cover page."""

    chunks = chunk_text("word " * 81_600)  # ~408 KB

    assert len(chunks) > 200
    assert all(len(c) <= DEFAULT_CHUNK_CHARS for c in chunks)


def test_iter_chunks_numbers_them_in_order() -> None:
    pairs = list(iter_chunks("x" * 5000, chunk_chars=1000, overlap_chars=100))

    assert [i for i, _ in pairs] == list(range(len(pairs)))


def test_estimate_is_stated_as_approximate() -> None:
    assert estimate_chunks(0) == 0
    assert estimate_chunks(156_000_000) > 70_000


# --- idempotency --------------------------------------------------------------


def test_point_ids_are_deterministic() -> None:
    """A re-run must overwrite its chunks, never duplicate them.

    Without this, an interrupted run resumed later leaves two copies of every
    passage it had already written — each retrievable, each looking
    authoritative.
    """

    first = point_id("filings", "0000789019-26-000001", 7)
    second = point_id("filings", "0000789019-26-000001", 7)

    assert first == second


def test_point_ids_separate_documents_chunks_and_sources() -> None:
    base = point_id("filings", "acc-1", 0)

    assert point_id("filings", "acc-1", 1) != base
    assert point_id("filings", "acc-2", 0) != base
    assert point_id("research-items", "acc-1", 0) != base


def test_point_ids_are_uuids_qdrant_accepts() -> None:
    """Qdrant takes uint64 or UUID; a raw string id is rejected at write time."""

    import uuid

    uuid.UUID(point_id("filings", "acc-1", 0))  # raises if malformed


# --- provenance ---------------------------------------------------------------


def test_payload_carries_enough_to_trace_a_hit_back() -> None:
    """A retrieved passage must point at a row somebody can go and read."""

    doc = CorpusDocument(
        source=SOURCE_FILINGS,
        ref="0000789019-26-000001",
        title="Form 8-K - MICROSOFT CORP",
        url="https://www.sec.gov/Archives/edgar/data/789019/x.htm",
        body="irrelevant",
        fetched_at=datetime(2026, 9, 19, tzinfo=UTC),
        symbol="MSFT",
        doc_ts=datetime(2026, 9, 18, tzinfo=UTC),
    )

    payload = doc.payload()

    assert payload["ref"] == "0000789019-26-000001"
    assert payload["symbol"] == "MSFT"
    assert payload["url"].startswith("https://www.sec.gov/")
    assert payload["doc_ts"] == "2026-09-18T00:00:00+00:00"


def test_absent_optional_fields_are_omitted_not_nulled() -> None:
    """A null `symbol` would make filter-by-symbol match research papers."""

    doc = CorpusDocument(
        source="research-items",
        ref="arxiv:1",
        title="t",
        url="u",
        body="b",
        fetched_at=datetime(2026, 9, 19, tzinfo=UTC),
        feed="arxiv",
    )

    payload = doc.payload()

    assert "symbol" not in payload
    assert payload["feed"] == "arxiv"


# --- the cursor ---------------------------------------------------------------


@pytest.mark.parametrize("sql", [SELECT_FILINGS_SQL, SELECT_RAW_ITEMS_SQL])
def test_cursor_pages_on_a_row_comparison_not_a_bare_timestamp(sql: str) -> None:
    """`fetched_at` is not unique — a backfill writes hundreds in one second.

    Comparing the columns separately either skips every row sharing the
    cursor's timestamp or revisits them forever. The row-comparison form is the
    only one that pages correctly.
    """

    assert ") > (" in sql, "cursor does not use a row comparison"
    assert "ORDER BY fetched_at," in sql, "ordering must match the comparison"


# --- indexing behaviour, with fakes -------------------------------------------


class _FakeStore:
    def __init__(self, docs: list[CorpusDocument]) -> None:
        self._docs = docs
        self.advanced: list[tuple[str, int, int]] = []

    async def ensure_schema(self) -> None:
        return None

    async def cursor_for(self, source: str) -> tuple[datetime | None, str]:
        return None, ""

    async def advance_cursor(
        self,
        source: str,
        *,
        last_fetched_at: datetime | None,
        last_ref: str,
        documents: int,
        points: int,
    ) -> None:
        self.advanced.append((source, documents, points))

    async def fetch(
        self, since: datetime | None, ref: str, limit: int
    ) -> list[CorpusDocument]:
        start = 0
        if ref:
            start = next(i for i, d in enumerate(self._docs) if d.ref == ref) + 1
        return self._docs[start : start + limit]


class _FakeEmbedder:
    model = "fake-embed"

    def __init__(self, dimension: int = 4) -> None:
        self._dimension = dimension
        self.embedded: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.embedded.extend(texts)
        return [[0.1] * self._dimension for _ in texts]

    async def probe_dimension(self) -> int:
        return self._dimension


class _FakeQdrant:
    def __init__(self) -> None:
        self.points: dict[str, dict] = {}

    async def dimension_of(self, collection: str) -> int | None:
        return None

    async def ensure_collection(self, collection: str, *, dimension: int) -> bool:
        return True

    async def upsert(self, points: list[dict], collection: str) -> int:
        for p in points:
            self.points[str(p["id"])] = p
        return len(points)


def _doc(ref: str, body: str) -> CorpusDocument:
    return CorpusDocument(
        source=SOURCE_FILINGS,
        ref=ref,
        title=f"doc {ref}",
        url=f"https://example/{ref}",
        body=body,
        fetched_at=datetime(2026, 9, 19, tzinfo=UTC),
    )


async def test_indexing_twice_does_not_duplicate_points() -> None:
    """The property that makes a long run safe to interrupt and resume."""

    docs = [_doc("a", "x" * 5000), _doc("b", "y" * 5000)]
    qdrant = _FakeQdrant()

    for _ in range(2):
        store = _FakeStore(docs)
        indexer = CorpusIndexer(
            store,  # type: ignore[arg-type]
            _FakeEmbedder(),  # type: ignore[arg-type]
            qdrant,  # type: ignore[arg-type]
            IndexerConfig(doc_batch=1),
        )
        await indexer.index_source(SOURCE_FILINGS, store.fetch)

    after_two_runs = len(qdrant.points)

    store = _FakeStore(docs)
    indexer = CorpusIndexer(
        store,  # type: ignore[arg-type]
        _FakeEmbedder(),  # type: ignore[arg-type]
        _FakeQdrant(),  # type: ignore[arg-type]
        IndexerConfig(doc_batch=1),
    )
    progress = await indexer.index_source(SOURCE_FILINGS, store.fetch)

    assert after_two_runs == progress.chunks, "a second pass duplicated points"


async def test_cursor_advances_per_batch_not_per_run() -> None:
    """An interrupted pass must keep what it finished."""

    docs = [_doc(r, "x" * 3000) for r in ("a", "b", "c")]
    store = _FakeStore(docs)
    indexer = CorpusIndexer(
        store,  # type: ignore[arg-type]
        _FakeEmbedder(),  # type: ignore[arg-type]
        _FakeQdrant(),  # type: ignore[arg-type]
        IndexerConfig(doc_batch=1),
    )

    await indexer.index_source(SOURCE_FILINGS, store.fetch)

    assert len(store.advanced) == 3, "cursor advanced once for the whole run"
    assert all(documents == 1 for _, documents, _ in store.advanced)


async def test_a_blank_body_is_counted_not_silently_dropped() -> None:
    """'0 chunks from 400 documents' is what a broken text column looks like."""

    docs = [_doc("a", "   "), _doc("b", "x" * 3000)]
    store = _FakeStore(docs)
    indexer = CorpusIndexer(
        store,  # type: ignore[arg-type]
        _FakeEmbedder(),  # type: ignore[arg-type]
        _FakeQdrant(),  # type: ignore[arg-type]
        IndexerConfig(doc_batch=10),
    )

    progress = await indexer.index_source(SOURCE_FILINGS, store.fetch)

    assert progress.skipped_empty == 1
    assert progress.chunks > 0


async def test_a_dimension_mismatch_refuses_before_embedding_anything() -> None:
    """Finding out after ten thousand chunks wastes the whole pass."""

    class _WrongDimension(_FakeQdrant):
        async def dimension_of(self, collection: str) -> int | None:
            return 1024

    store = _FakeStore([])
    embedder = _FakeEmbedder(dimension=768)
    indexer = CorpusIndexer(
        store,  # type: ignore[arg-type]
        embedder,  # type: ignore[arg-type]
        _WrongDimension(),  # type: ignore[arg-type]
        IndexerConfig(),
    )

    with pytest.raises(RuntimeError, match="cannot be migrated in place"):
        await indexer.ensure_ready()
