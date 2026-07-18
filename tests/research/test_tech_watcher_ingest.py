"""Tests for the Tech Watcher ingest slice: sources, store, service pass."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from shrap.research.tech_watcher.service import (
    STREAM_HEALTH_ANOMALY,
    STREAM_INGESTION_HEARTBEAT,
    ingest_pass,
)
from shrap.research.tech_watcher.sources import (
    ArxivSource,
    EdgarSource,
    RawSourceItem,
    SourceError,
)
from shrap.research.tech_watcher.store import (
    INSERT_RAW_ITEM_SQL,
    UPSERT_CURSOR_SQL,
    PostgresRawItemStore,
)

EDGAR_FEED = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Latest Filings</title>
<entry>
<title>8-K - ACME CORP (0000123456) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/acme-index.htm"/>
<summary type="html">Filed: 2026-07-17</summary>
<updated>2026-07-17T16:05:10-04:00</updated>
<id>urn:tag:sec.gov,2008:accession-number=0000123456-26-000042</id>
</entry>
</feed>
"""

ARXIV_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry>
<id>http://arxiv.org/abs/2607.01234v1</id>
<title>Scaling Laws for  Photonic
 Interconnects</title>
<summary>We study photonic interconnect scaling.</summary>
<published>2026-07-16T17:00:00Z</published>
<link href="http://arxiv.org/abs/2607.01234v1" rel="alternate" type="text/html"/>
<arxiv:primary_category term="cs.LG"/>
</entry>
</feed>
"""


class FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class FakeHTTP:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = responses
        self.requests: list[tuple[str, dict[str, str]]] = []

    async def get(
        self, url: str, *, params: dict[str, str], headers: dict[str, str], timeout: float
    ) -> FakeResponse:
        self.requests.append((url, params))
        return self._responses[len(self.requests) - 1]


# --- sources -------------------------------------------------------------------


async def test_edgar_parses_entry_and_extracts_accession() -> None:
    http = FakeHTTP([FakeResponse(200, EDGAR_FEED)])
    source = EdgarSource(user_agent="test-agent (test@example.com)", forms=("8-K",))

    items = await source.fetch(http)

    assert len(items) == 1
    item = items[0]
    assert item.item_id == "edgar:0000123456-26-000042"
    assert item.source == "sec-edgar"
    assert item.kind == "8-K"
    assert item.title.startswith("8-K - ACME CORP")
    assert item.url == "https://www.sec.gov/Archives/acme-index.htm"
    assert item.external_ts is not None and item.external_ts.year == 2026
    _url, params = http.requests[0]
    assert params["type"] == "8-K"
    assert params["output"] == "atom"


async def test_edgar_dedupes_across_form_queries() -> None:
    http = FakeHTTP([FakeResponse(200, EDGAR_FEED), FakeResponse(200, EDGAR_FEED)])
    source = EdgarSource(user_agent="test-agent", forms=("10-K", "8-K"))

    items = await source.fetch(http)

    assert len(http.requests) == 2
    assert len(items) == 1  # same accession from both queries


async def test_edgar_non_200_raises_source_error() -> None:
    http = FakeHTTP([FakeResponse(403, "forbidden")])
    source = EdgarSource(user_agent="test-agent", forms=("8-K",))

    with pytest.raises(SourceError, match="403"):
        await source.fetch(http)


async def test_arxiv_parses_entry_with_category_and_whitespace_normalized() -> None:
    http = FakeHTTP([FakeResponse(200, ARXIV_FEED)])
    source = ArxivSource(categories=("cs.AI", "cs.LG"))

    items = await source.fetch(http)

    assert len(items) == 1
    item = items[0]
    assert item.item_id == "arxiv:2607.01234v1"
    assert item.kind == "cs.LG"
    assert item.title == "Scaling Laws for Photonic Interconnects"
    assert item.summary == "We study photonic interconnect scaling."
    _url, params = http.requests[0]
    assert params["search_query"] == "cat:cs.AI OR cat:cs.LG"


async def test_arxiv_garbage_body_raises_source_error() -> None:
    http = FakeHTTP([FakeResponse(200, "not xml at all")])
    source = ArxivSource(categories=("cs.AI",))

    with pytest.raises(SourceError, match="not parseable"):
        await source.fetch(http)


# --- store ---------------------------------------------------------------------


class FakeTransaction:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> object:
        self._conn.transactions_entered += 1
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.insert_results: list[str] = []
        self.transactions_entered = 0

    async def execute(self, sql: str, *args: object) -> object:
        self.executed.append((sql, args))
        if sql == INSERT_RAW_ITEM_SQL and self.insert_results:
            return self.insert_results.pop(0)
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> dict[str, Any] | None:
        return None

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        return []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)


class FakeAcquire:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakePool:
    def __init__(self) -> None:
        self.conn = FakeConn()

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)


def _item(item_id: str, ts: datetime | None) -> RawSourceItem:
    return RawSourceItem(
        item_id=item_id,
        source="sec-edgar",
        kind="8-K",
        title="t",
        summary=None,
        url=None,
        external_ts=ts,
        payload={"k": "v"},
    )


async def test_upsert_batch_counts_new_rows_and_advances_cursor_atomically() -> None:
    pool = FakePool()
    pool.conn.insert_results = ["INSERT 0 1", "INSERT 0 0", "INSERT 0 1"]
    store = PostgresRawItemStore(pool)  # type: ignore[arg-type]
    fetched_at = datetime(2026, 7, 17, 21, 0, tzinfo=UTC)
    older = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    newer = datetime(2026, 7, 17, 20, 0, tzinfo=UTC)

    inserted = await store.upsert_batch(
        "sec-edgar",
        [_item("edgar:a", newer), _item("edgar:b", None), _item("edgar:c", older)],
        fetched_at,
    )

    assert inserted == 2  # the duplicate counted zero
    assert pool.conn.transactions_entered == 1
    cursor_calls = [args for sql, args in pool.conn.executed if sql == UPSERT_CURSOR_SQL]
    assert len(cursor_calls) == 1
    source, newest_ts, last_item_id, items_seen, updated_at = cursor_calls[0]
    assert source == "sec-edgar"
    assert newest_ts == newer  # max external_ts among genuinely inserted rows
    assert last_item_id == "edgar:c"
    assert items_seen == 2
    assert updated_at == fetched_at


async def test_upsert_batch_all_duplicates_still_touches_cursor_with_zero() -> None:
    pool = FakePool()
    pool.conn.insert_results = ["INSERT 0 0"]
    store = PostgresRawItemStore(pool)  # type: ignore[arg-type]

    inserted = await store.upsert_batch(
        "arxiv", [_item("arxiv:x", None)], datetime(2026, 7, 17, tzinfo=UTC)
    )

    assert inserted == 0
    cursor_calls = [args for sql, args in pool.conn.executed if sql == UPSERT_CURSOR_SQL]
    assert cursor_calls[0][3] == 0


# --- service pass --------------------------------------------------------------


class FakeSource:
    def __init__(self, name: str, items: list[RawSourceItem] | None, error: bool = False) -> None:
        self._name = name
        self._items = items or []
        self._error = error

    @property
    def name(self) -> str:
        return self._name

    async def fetch(self, http: object, timeout: float = 30.0) -> list[RawSourceItem]:
        if self._error:
            raise SourceError(f"{self._name} unavailable")
        return self._items


class FakeStore:
    def __init__(self) -> None:
        self.batches: list[tuple[str, int]] = []

    async def upsert_batch(self, source: str, items: object, fetched_at: object) -> int:
        n = len(items)  # type: ignore[arg-type]
        self.batches.append((source, n))
        return n


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[str] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.published.append(stream)
        return f"{len(self.published)}-0"


async def test_ingest_pass_publishes_heartbeat_per_healthy_source() -> None:
    store = FakeStore()
    redis = FakeRedis()
    sources = [
        FakeSource("sec-edgar", [_item("edgar:a", None)]),
        FakeSource("arxiv", [_item("arxiv:b", None)]),
    ]

    counts = await ingest_pass(sources, http=object(), store=store, redis=redis)  # type: ignore[arg-type]

    assert counts == {"sec-edgar": 1, "arxiv": 1}
    assert redis.published == [STREAM_INGESTION_HEARTBEAT, STREAM_INGESTION_HEARTBEAT]
    assert store.batches == [("sec-edgar", 1), ("arxiv", 1)]


async def test_ingest_pass_isolates_a_failing_source_and_continues() -> None:
    store = FakeStore()
    redis = FakeRedis()
    sources = [
        FakeSource("sec-edgar", None, error=True),
        FakeSource("arxiv", [_item("arxiv:b", None)]),
    ]

    counts = await ingest_pass(sources, http=object(), store=store, redis=redis)  # type: ignore[arg-type]

    assert counts == {"arxiv": 1}  # edgar absent, not zero — it failed, not empty
    assert redis.published == [STREAM_HEALTH_ANOMALY, STREAM_INGESTION_HEARTBEAT]
    assert store.batches == [("arxiv", 1)]


# --- settings ------------------------------------------------------------------


def test_settings_parse_env_and_split_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    from shrap.agents.research.tech_watcher.config import Settings

    monkeypatch.setenv("TECH_WATCHER_EDGAR_FORMS", "8-K, 10-K")
    monkeypatch.setenv("TECH_WATCHER_INTERVAL_SECONDS", "600")
    settings = Settings()

    assert settings.edgar_forms_tuple() == ("8-K", "10-K")
    assert settings.arxiv_categories_tuple() == ("cs.AI", "cs.LG", "cond-mat", "q-bio.NC")
    assert settings.interval_seconds == 600.0
    assert settings.redacted()["postgres_dsn"] == "***"
