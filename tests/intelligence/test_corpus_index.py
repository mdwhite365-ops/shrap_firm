"""The corpus index: chunking, idempotency, and the cursor's ordering.

Qdrant ran healthy and empty for two and a half months. The tests that matter
here are the ones covering the properties that decide whether a long indexing
run can be trusted and resumed — not whether the HTTP calls are shaped right,
which only a live Qdrant can say.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise

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
    for earlier, later in pairwise(chunks):
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

    async def fetch(self, since: datetime | None, ref: str, limit: int) -> list[CorpusDocument]:
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


# --- request sizing, learned from a real failure ------------------------------
#
# The first real indexing run died with `httpx.WriteTimeout` after 65 documents.
# Every test above passed, because they use a fake Qdrant that accepts a list of
# any size. The indexer batches by DOCUMENT and twenty 400 KB filings produce
# ~2,000 chunks, each carrying a 768-float vector plus 2,000 characters of text
# — a ~34 MB JSON body in one PUT.
#
# The cursor design held: 65 filings and 7,254 points survived the crash and a
# re-run resumed from the right place. Nothing was lost. But the run could not
# finish, and no test could have told me so.


def test_upsert_splits_into_bounded_requests() -> None:
    """Sizing the HTTP request belongs to the layer that makes the request.

    The caller hands over however many points it produced and cannot know how
    large they are. Raising the timeout instead would only have made the same
    failure slower.
    """

    import inspect

    from shrap.common.qdrant_client import DEFAULT_UPSERT_BATCH, QdrantClient

    assert DEFAULT_UPSERT_BATCH <= 512, "a batch this large risks the 34 MB body again"

    source = inspect.getsource(QdrantClient.upsert)
    assert "range(0, len(points), batch_size)" in source, "upsert sends one request"
    assert "_write_timeout" in source, "bulk writes share the read timeout"


async def test_upsert_sends_every_point_across_batches() -> None:
    """Splitting must not drop the tail — a partial index is a silent one."""

    import httpx

    from shrap.common.qdrant_client import QdrantClient

    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(len(json.loads(request.content)["points"]))
        return httpx.Response(200, json={"result": {}, "status": "ok"})

    transport = httpx.MockTransport(handler)
    client = QdrantClient("http://qdrant:6333")

    points = [{"id": str(i), "vector": [0.1], "payload": {}} for i in range(650)]

    original = httpx.AsyncClient

    class _Patched(original):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: object) -> None:
            kwargs["transport"] = transport
            super().__init__(**kwargs)  # type: ignore[arg-type]

    httpx.AsyncClient = _Patched  # type: ignore[misc]
    try:
        sent = await client.upsert(points, batch_size=256)
    finally:
        httpx.AsyncClient = original  # type: ignore[misc]

    assert sent == 650, "upsert under-reported what it sent"
    assert seen == [256, 256, 138], f"unexpected request sizes: {seen}"
    assert sum(seen) == 650, "points were dropped between batches"


# --- concurrency, and the property it could break -----------------------------
#
# The first full run held Ollama at 120% CPU on a twelve-core box — one request
# in flight at a time, ten cores idle, because `embed` awaited each batch in
# turn. Concurrency fixes that and introduces exactly one way to be catastrophi-
# cally wrong: if results came back in completion order rather than submission
# order, vectors would attach to the wrong chunks. Nothing downstream would
# notice — the counts match, the writes succeed, and the index is quietly full
# of passages filed under other documents.


async def test_concurrent_batches_return_in_submission_order() -> None:
    """The one property concurrency could break, with the timing to break it.

    Later batches are made to finish FIRST. If `embed` returned completion
    order, this test sees the sequence reversed.
    """

    import asyncio

    from shrap.intelligence.corpus_index.embedder import OllamaEmbedder

    embedder = OllamaEmbedder(batch_size=1, concurrency=8)
    order: list[int] = []

    async def fake_batch(batch, client):  # type: ignore[no-untyped-def]
        marker = int(batch[0])
        # Invert the delays: the last submitted batch completes soonest.
        await asyncio.sleep((100 - marker) / 1000)
        order.append(marker)
        return [[float(marker)]]

    embedder._embed_batch = fake_batch  # type: ignore[assignment, method-assign]

    vectors = await embedder.embed([str(i) for i in range(20)])

    assert [v[0] for v in vectors] == [float(i) for i in range(20)], (
        "embeddings came back in completion order — vectors would attach to the wrong chunks"
    )
    assert order != sorted(order), "the test did not actually run out of order"


async def test_concurrency_is_bounded() -> None:
    """Unbounded would saturate a box that is also running the trading path."""

    import asyncio

    from shrap.intelligence.corpus_index.embedder import (
        DEFAULT_CONCURRENCY,
        OllamaEmbedder,
    )

    assert 1 < DEFAULT_CONCURRENCY <= 8, "default should speed things up, not take the box"

    embedder = OllamaEmbedder(batch_size=1, concurrency=3)
    in_flight = 0
    peak = 0

    async def fake_batch(batch, client):  # type: ignore[no-untyped-def]
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return [[1.0]]

    embedder._embed_batch = fake_batch  # type: ignore[assignment, method-assign]
    await embedder.embed([str(i) for i in range(30)])

    assert peak <= 3, f"semaphore did not bound concurrency: peak {peak}"
    assert peak > 1, "no concurrency actually happened"


# --- the corpus the first run missed ------------------------------------------
#
# The first full index returned only bank-earnings 8-Ks for "cross-sectional
# momentum factor predicts equity returns". `document_text` is populated for
# sec-edgar and nothing else, so 5,843 arXiv and q-fin papers — the literature
# that would actually serve the Hypothesis Generator — were never indexed.
# Found by querying the index, not by counting its points.


def test_research_query_reads_the_abstract_when_there_is_no_full_text() -> None:
    """Otherwise the index contains EDGAR and no research literature at all."""

    assert "coalesce(document_text, summary)" in SELECT_RAW_ITEMS_SQL
    assert "WHERE coalesce(document_text, summary) IS NOT NULL" in SELECT_RAW_ITEMS_SQL


def test_the_corpus_size_query_counts_what_the_fetch_would_index() -> None:
    """A size estimate over a different predicate than the fetch is a lie.

    It would have reported 15,318 documents while the indexer walked 21,518, or
    the reverse — and the estimate is what decides whether a run is minutes or
    hours.
    """

    from shrap.intelligence.corpus_index.store import COUNT_RAW_ITEMS_SQL

    assert "coalesce(document_text, summary)" in COUNT_RAW_ITEMS_SQL


def test_an_abstract_is_labelled_so_it_cannot_pass_for_full_text() -> None:
    """A hit on an abstract must not read as a hit on the paper."""

    assert "'abstract'" in SELECT_RAW_ITEMS_SQL
    assert "'full-text'" in SELECT_RAW_ITEMS_SQL

    doc = CorpusDocument(
        source="research-items",
        ref="arxiv:2609.05485v1",
        title="Are AI Risks Priced in the U.S. Stock Market?",
        url="https://arxiv.org/abs/2609.05485v1",
        body="We study...",
        fetched_at=datetime(2026, 9, 19, tzinfo=UTC),
        feed="arxiv-qfin",
        body_kind="abstract",
    )

    assert doc.payload()["body_kind"] == "abstract"


def test_full_text_is_the_default_so_filings_are_not_mislabelled() -> None:
    doc = CorpusDocument(
        source=SOURCE_FILINGS,
        ref="acc-1",
        title="t",
        url="u",
        body="b",
        fetched_at=datetime(2026, 9, 19, tzinfo=UTC),
    )

    assert doc.payload()["body_kind"] == "full-text"
