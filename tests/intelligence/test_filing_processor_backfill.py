"""Tests for the Filing Processor backfill CLI's domain logic (deferred from #68).

Mirrors ``tests/intelligence/test_filing_processor.py`` idioms: fake
asyncpg pool/connection, fake Redis, fake LLM, fake EDGAR source, asserting
on the module's own SQL constants and dataclasses rather than a real DB.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from shrap.events import Envelope, EventPublisher, normalize_redis_fields
from shrap.intelligence.filing_processor.backfill import (
    BACKFILL_FEED,
    BackfillSummary,
    backfill_pass,
    parse_date_range,
)
from shrap.intelligence.filing_processor.client import parse_roster, strip_markup
from shrap.intelligence.filing_processor.service import (
    STREAM_INTELLIGENCE_SIGNAL,
    FilingRunConfig,
    fetch_pass,
    match_candidate,
    score_pass,
)
from shrap.intelligence.filing_processor.store import (
    INSERT_FILING_SQL,
    INSERT_FILING_VERDICT_HISTORY_SQL,
    SELECT_CANDIDATES_BY_ACCESSION_SQL,
    SELECT_CANDIDATES_BY_DATE_RANGE_SQL,
    SELECT_FILING_STATES_SQL,
    SELECT_PENDING_FETCH_BY_ACCESSION_SQL,
    SELECT_SCORABLE_BY_ACCESSION_SQL,
    UPSERT_FILING_CURSOR_SQL,
    CandidateRow,
    PostgresFilingStore,
)

# --- realistic EDGAR fixtures (mirrors test_filing_processor.py) ---------------

APPLE_ACCESSION = "0000320193-26-000070"
APPLE_INDEX_URL = (
    "https://www.sec.gov/Archives/edgar/data/320193/"
    "000032019326000070/0000320193-26-000070-index.htm"
)
APPLE_TITLE = "8-K - APPLE INC (0000320193) (Filer)"
NON_TIER3_URL = "https://www.sec.gov/Archives/edgar/data/999999/x/y-index.htm"

FILING_8K_BODY = (
    "<SEC-DOCUMENT><TYPE>8-K<TEXT><html><body>"
    "<p>Item 5.02 Departure of Directors or Certain Officers; Election of "
    "Directors. On July 19, 2026, the registrant's Chief Financial Officer "
    "notified the Board of an intention to resign.</p>"
    "</body></html></TEXT></SEC-DOCUMENT>"
)


# --- fakes -----------------------------------------------------------------


class FakeFilingSource:
    def __init__(self, results: list[str | Exception]) -> None:
        self._results = results
        self.calls: list[str] = []

    async def fetch_filing_text(
        self, http: object, cik: str, accession: str, timeout: float = 30.0
    ) -> str:
        self.calls.append(accession)
        result = self._results[min(len(self.calls) - 1, len(self._results) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


class FakeLLMResult:
    def __init__(self, content: str, model: str = "qwen3.5:9b-q4_K_M") -> None:
        self.content = content
        self.model = model


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        tier: str,
        prompt: str,
        system: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        think: bool | None = None,
    ) -> FakeLLMResult:
        self.calls.append({"tier": tier, "json_mode": json_mode, "think": think})
        content = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        return FakeLLMResult(content)


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
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []
        self.insert_results: list[str] = []
        self.fetch_results: dict[str, list[dict[str, Any]]] = {}
        self.fetchrow_results: dict[str, dict[str, Any] | None] = {}
        self.transactions_entered = 0

    async def execute(self, sql: str, *args: object) -> object:
        self.executed.append((sql, args))
        if sql == INSERT_FILING_SQL and self.insert_results:
            return self.insert_results.pop(0)
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> Mapping[str, Any] | None:
        return self.fetchrow_results.get(sql)

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]:
        self.fetch_calls.append((sql, args))
        return self.fetch_results.get(sql, [])

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


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.published.append((stream, fields))
        return f"{len(self.published)}-0"


# --- row/verdict builders ----------------------------------------------------


def _candidate_dict(
    accession: str, url: str | None = APPLE_INDEX_URL, title: str | None = APPLE_TITLE
) -> dict[str, Any]:
    return {
        "item_id": f"edgar:{accession}",
        "title": title,
        "url": url,
        "external_ts": datetime(2026, 7, 19, 20, 30, tzinfo=UTC),
        "fetched_at": None,
    }


def _pending_row() -> dict[str, Any]:
    return {
        "accession": APPLE_ACCESSION,
        "cik": "320193",
        "symbol": "AAPL",
        "filing_url": APPLE_INDEX_URL,
    }


def _scorable_row(item_codes: list[str]) -> dict[str, Any]:
    return {
        "accession": APPLE_ACCESSION,
        "symbol": "AAPL",
        "title": APPLE_TITLE,
        "company": "APPLE INC",
        "filing_date": datetime(2026, 7, 19, 20, 30, tzinfo=UTC),
        "item_codes": item_codes,
        "full_text": strip_markup(FILING_8K_BODY),
    }


def _verdict_json(
    materiality: int,
    category: str = "officer-change",
    summary: str = "s",
    symbols: tuple[str, ...] = ("AAPL",),
    relevant: bool = True,
    item_code: str = "5.02",
) -> str:
    return json.dumps(
        {
            "relevant": relevant,
            "symbols": list(symbols),
            "item_code": item_code,
            "category": category,
            "materiality": materiality,
            "summary": summary,
        }
    )


def _config() -> FilingRunConfig:
    return FilingRunConfig(roster=parse_roster("AAPL:320193"))


# --- parse_date_range (pure) --------------------------------------------------


def test_parse_date_range_since_only_is_open_ended() -> None:
    since, until = parse_date_range("2026-07-01", None)
    assert since == datetime(2026, 7, 1, tzinfo=UTC)
    assert until.year == 9999  # far-future sentinel, no upper bound


def test_parse_date_range_until_is_inclusive_of_its_day() -> None:
    since, until = parse_date_range("2026-07-01", "2026-07-15")
    assert since == datetime(2026, 7, 1, tzinfo=UTC)
    assert until == datetime(2026, 7, 16, tzinfo=UTC)  # exclusive upper bound


def test_parse_date_range_invalid_format_raises() -> None:
    with pytest.raises(ValueError):
        parse_date_range("07-01-2026", None)


# --- match_candidate (pure, shared with poll_pass) ----------------------------


def test_match_candidate_resolves_tier3_and_drops_non_match() -> None:
    roster = parse_roster("AAPL:320193")

    tier3 = CandidateRow(
        item_id=f"edgar:{APPLE_ACCESSION}",
        title=APPLE_TITLE,
        url=APPLE_INDEX_URL,
        filing_date=None,
        fetched_at=None,
    )
    pending = match_candidate(tier3, roster)
    assert pending is not None
    assert pending.accession == APPLE_ACCESSION
    assert pending.symbol == "AAPL"

    non_tier3 = CandidateRow(
        item_id="edgar:9999999999-26-000001",
        title=None,
        url=NON_TIER3_URL,
        filing_date=None,
        fetched_at=None,
    )
    assert match_candidate(non_tier3, roster) is None

    bad_item_id = CandidateRow(
        item_id="arxiv:2506.01234",
        title=None,
        url=APPLE_INDEX_URL,
        filing_date=None,
        fetched_at=None,
    )
    assert match_candidate(bad_item_id, roster) is None


# --- store: accession / date-range candidate selection ------------------------


async def test_select_candidates_by_accessions_queries_by_item_id() -> None:
    pool = FakePool()
    pool.conn.fetch_results[SELECT_CANDIDATES_BY_ACCESSION_SQL] = [_candidate_dict(APPLE_ACCESSION)]
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]

    rows = await store.select_candidates_by_accessions([APPLE_ACCESSION])

    assert len(rows) == 1
    assert rows[0].item_id == f"edgar:{APPLE_ACCESSION}"
    sql, args = pool.conn.fetch_calls[0]
    assert sql == SELECT_CANDIDATES_BY_ACCESSION_SQL
    assert args[0] == [f"edgar:{APPLE_ACCESSION}"]


async def test_select_candidates_by_date_range_passes_window() -> None:
    pool = FakePool()
    pool.conn.fetch_results[SELECT_CANDIDATES_BY_DATE_RANGE_SQL] = [
        _candidate_dict(APPLE_ACCESSION)
    ]
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 16, tzinfo=UTC)

    rows = await store.select_candidates_by_date_range(since, until)

    assert len(rows) == 1
    assert pool.conn.fetch_calls[0] == (SELECT_CANDIDATES_BY_DATE_RANGE_SQL, (since, until))


# --- store: filing state / scoped pending-fetch / scoped scorable -------------


async def test_select_filing_states_maps_by_accession() -> None:
    pool = FakePool()
    pool.conn.fetch_results[SELECT_FILING_STATES_SQL] = [
        {
            "accession": APPLE_ACCESSION,
            "fetched_at": datetime(2026, 7, 18, tzinfo=UTC),
            "scored_at": datetime(2026, 7, 18, 1, tzinfo=UTC),
        }
    ]
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]

    states = await store.select_filing_states([APPLE_ACCESSION])

    assert states[APPLE_ACCESSION].scored_at is not None
    assert await store.select_filing_states([]) == {}  # empty list short-circuits, no query


async def test_select_pending_fetch_by_accession_scopes_and_limits() -> None:
    pool = FakePool()
    pool.conn.fetch_results[SELECT_PENDING_FETCH_BY_ACCESSION_SQL] = [_pending_row()]
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]

    rows = await store.select_pending_fetch_by_accession([APPLE_ACCESSION], 1)

    assert len(rows) == 1
    assert pool.conn.fetch_calls[0] == (
        SELECT_PENDING_FETCH_BY_ACCESSION_SQL,
        ([APPLE_ACCESSION], 1),
    )


async def test_select_scorable_by_accession_ignores_scored_at() -> None:
    """The store method never filters on ``scored_at`` — the CLI has already
    decided which accessions belong in the set (skip vs. --rescore)."""

    pool = FakePool()
    pool.conn.fetch_results[SELECT_SCORABLE_BY_ACCESSION_SQL] = [_scorable_row(["5.02"])]
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]

    rows = await store.select_scorable_by_accession([APPLE_ACCESSION])

    assert len(rows) == 1
    assert rows[0].item_codes == ("5.02",)


# --- fetch_pass / score_pass: accessions scoping -------------------------------


async def test_fetch_pass_scoped_to_accessions_uses_by_accession_query() -> None:
    pool = FakePool()
    pool.conn.fetch_results[SELECT_PENDING_FETCH_BY_ACCESSION_SQL] = [_pending_row()]
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]
    source = FakeFilingSource([strip_markup(FILING_8K_BODY)])

    counts = await fetch_pass(
        source,
        object(),
        store,
        _config(),
        accessions=frozenset({APPLE_ACCESSION}),  # type: ignore[arg-type]
    )

    assert counts.fetched == 1
    sqls = [sql for sql, _ in pool.conn.fetch_calls]
    assert SELECT_PENDING_FETCH_BY_ACCESSION_SQL in sqls


async def test_score_pass_scoped_to_accessions_uses_scorable_query() -> None:
    pool = FakePool()
    pool.conn.fetch_results[SELECT_SCORABLE_BY_ACCESSION_SQL] = [_scorable_row(["5.02"])]
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]
    llm = FakeLLM([_verdict_json(1)])
    publisher = EventPublisher(FakeRedis())  # type: ignore[arg-type]

    counts = await score_pass(
        store, llm, publisher, _config(), accessions=frozenset({APPLE_ACCESSION})
    )

    assert counts.filings_scored == 1
    sqls = [sql for sql, _ in pool.conn.fetch_calls]
    assert SELECT_SCORABLE_BY_ACCESSION_SQL in sqls


# --- backfill_pass: end to end -------------------------------------------------


async def test_backfill_pass_accession_mode_discovers_fetches_scores_publishes() -> None:
    pool = FakePool()
    now = datetime(2026, 7, 19, 22, 0, tzinfo=UTC)
    pool.conn.fetch_results[SELECT_CANDIDATES_BY_ACCESSION_SQL] = [_candidate_dict(APPLE_ACCESSION)]
    pool.conn.insert_results = ["INSERT 0 1"]
    pool.conn.fetch_results[SELECT_FILING_STATES_SQL] = []  # nothing scored yet
    pool.conn.fetch_results[SELECT_PENDING_FETCH_BY_ACCESSION_SQL] = [_pending_row()]
    pool.conn.fetch_results[SELECT_SCORABLE_BY_ACCESSION_SQL] = [_scorable_row(["5.02"])]
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]
    source = FakeFilingSource([strip_markup(FILING_8K_BODY)])
    llm = FakeLLM([_verdict_json(1, summary="CFO resigned.")])
    redis = FakeRedis()
    publisher = EventPublisher(redis)  # type: ignore[arg-type]

    summary = await backfill_pass(
        store,
        source,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        llm,
        publisher,
        _config(),
        accessions=[APPLE_ACCESSION],
        since=None,
        until=None,
        rescore=False,
        now=now,
    )

    assert summary == BackfillSummary(discovered=1, fetched=1, scored=1, published=1, skipped=0)
    cursor_calls = [args for sql, args in pool.conn.executed if sql == UPSERT_FILING_CURSOR_SQL]
    assert cursor_calls[0][0] == BACKFILL_FEED  # never the service's own poll feed
    stream, fields = redis.published[0]
    assert stream == STREAM_INTELLIGENCE_SIGNAL
    envelope = Envelope.from_redis_fields(normalize_redis_fields(fields))
    assert envelope.payload is not None
    assert envelope.payload["item_ref"] == f"{APPLE_ACCESSION}#5.02"


async def test_backfill_pass_date_range_drops_non_tier3_scoped_to_roster() -> None:
    pool = FakePool()
    now = datetime(2026, 7, 19, 22, 0, tzinfo=UTC)
    pool.conn.fetch_results[SELECT_CANDIDATES_BY_DATE_RANGE_SQL] = [
        _candidate_dict(APPLE_ACCESSION),
        _candidate_dict("9999999999-26-000001", url=NON_TIER3_URL, title=None),
    ]
    pool.conn.insert_results = ["INSERT 0 1"]
    pool.conn.fetch_results[SELECT_FILING_STATES_SQL] = []
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]
    llm = FakeLLM([])
    publisher = EventPublisher(FakeRedis())  # type: ignore[arg-type]

    summary = await backfill_pass(
        store,
        FakeFilingSource([]),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        llm,
        publisher,
        _config(),
        accessions=None,
        since=datetime(2026, 7, 1, tzinfo=UTC),
        until=None,
        rescore=False,
        now=now,
    )

    assert summary.discovered == 1  # only the Tier 3 match — dropped here, not upstream
    inserts = [args for sql, args in pool.conn.executed if sql == INSERT_FILING_SQL]
    assert len(inserts) == 1
    assert inserts[0][0] == APPLE_ACCESSION


async def test_backfill_pass_skips_already_scored_by_default() -> None:
    pool = FakePool()
    now = datetime(2026, 7, 19, 22, 0, tzinfo=UTC)
    pool.conn.fetch_results[SELECT_CANDIDATES_BY_ACCESSION_SQL] = [_candidate_dict(APPLE_ACCESSION)]
    pool.conn.insert_results = ["INSERT 0 0"]  # already recorded by the live service
    pool.conn.fetch_results[SELECT_FILING_STATES_SQL] = [
        {
            "accession": APPLE_ACCESSION,
            "fetched_at": datetime(2026, 7, 18, tzinfo=UTC),
            "scored_at": datetime(2026, 7, 18, 1, tzinfo=UTC),
        }
    ]
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]
    llm = FakeLLM([])
    publisher = EventPublisher(FakeRedis())  # type: ignore[arg-type]

    summary = await backfill_pass(
        store,
        FakeFilingSource([]),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        llm,
        publisher,
        _config(),
        accessions=[APPLE_ACCESSION],
        since=None,
        until=None,
        rescore=False,
        now=now,
    )

    assert summary == BackfillSummary(discovered=1, fetched=0, scored=0, published=0, skipped=1)
    assert llm.calls == []  # never touched the LLM for an already-scored filing
    sqls = [sql for sql, _ in pool.conn.fetch_calls]
    assert SELECT_PENDING_FETCH_BY_ACCESSION_SQL not in sqls
    assert SELECT_SCORABLE_BY_ACCESSION_SQL not in sqls


async def test_backfill_pass_rescore_bypasses_skip_and_appends_history() -> None:
    pool = FakePool()
    now = datetime(2026, 7, 19, 22, 0, tzinfo=UTC)
    pool.conn.fetch_results[SELECT_CANDIDATES_BY_ACCESSION_SQL] = [_candidate_dict(APPLE_ACCESSION)]
    pool.conn.insert_results = ["INSERT 0 0"]
    pool.conn.fetch_results[SELECT_SCORABLE_BY_ACCESSION_SQL] = [_scorable_row(["5.02"])]
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]
    llm = FakeLLM([_verdict_json(1, summary="second read")])
    redis = FakeRedis()
    publisher = EventPublisher(redis)  # type: ignore[arg-type]

    summary = await backfill_pass(
        store,
        FakeFilingSource([]),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        llm,
        publisher,
        _config(),
        accessions=[APPLE_ACCESSION],
        since=None,
        until=None,
        rescore=True,
        now=now,
    )

    assert summary.skipped == 0  # --rescore bypasses the skip decision entirely
    assert summary.scored == 1
    history = [args for sql, args in pool.conn.executed if sql == INSERT_FILING_VERDICT_HISTORY_SQL]
    assert len(history) == 1  # an INSERT-only table — appended, never overwritten (KI-007)
    # the rescore path never needs the skip decision, so it never queries filing state
    sqls = [sql for sql, _ in pool.conn.fetch_calls]
    assert SELECT_FILING_STATES_SQL not in sqls


async def test_backfill_pass_unresolved_accession_not_counted_discovered() -> None:
    pool = FakePool()
    now = datetime(2026, 7, 19, 22, 0, tzinfo=UTC)
    pool.conn.fetch_results[SELECT_CANDIDATES_BY_ACCESSION_SQL] = []  # Tech Watcher never saw it
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]
    llm = FakeLLM([])
    publisher = EventPublisher(FakeRedis())  # type: ignore[arg-type]

    summary = await backfill_pass(
        store,
        FakeFilingSource([]),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        llm,
        publisher,
        _config(),
        accessions=["0000999999-26-000099"],
        since=None,
        until=None,
        rescore=False,
        now=now,
    )

    assert summary == BackfillSummary(discovered=0, fetched=0, scored=0, published=0, skipped=0)


async def test_backfill_pass_requires_accession_or_since() -> None:
    pool = FakePool()
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]
    llm = FakeLLM([])
    publisher = EventPublisher(FakeRedis())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="either --accession or --since"):
        await backfill_pass(
            store,
            FakeFilingSource([]),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            llm,
            publisher,
            _config(),
            accessions=None,
            since=None,
            until=None,
            rescore=False,
            now=datetime.now(UTC),
        )


# --- BackfillSummary -----------------------------------------------------------


def test_backfill_summary_render_format() -> None:
    summary = BackfillSummary(discovered=2, fetched=1, scored=1, published=1, skipped=1)
    assert summary.render() == "discovered=2 fetched=1 scored=1 published=1 skipped=1"
