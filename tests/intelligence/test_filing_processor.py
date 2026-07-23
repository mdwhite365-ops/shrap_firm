"""Tests for the Filing Processor: EDGAR parsing, store SQL, scoring, service."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from shrap.events import Envelope, EventPublisher, normalize_redis_fields
from shrap.intelligence.filing_processor.client import (
    EdgarFilingClient,
    FilingFetchError,
    accession_from_item_id,
    derive_company,
    derive_headline,
    extract_item_codes,
    filing_txt_url,
    parse_cik,
    parse_roster,
    split_item_sections,
    strip_markup,
)
from shrap.intelligence.filing_processor.scorer import (
    FILING_PROMPT_VERSION,
    FilingVerdict,
    parse_filing_response,
)
from shrap.intelligence.filing_processor.service import (
    STREAM_INTELLIGENCE_SIGNAL,
    FilingRunConfig,
    fetch_pass,
    poll_pass,
    score_pass,
)
from shrap.intelligence.filing_processor.store import (
    INSERT_FILING_SQL,
    INSERT_FILING_VERDICT_HISTORY_SQL,
    MARK_FILING_FETCHED_SQL,
    MARK_FILING_SCORED_SQL,
    SELECT_CANDIDATE_FILINGS_SQL,
    SELECT_FILING_CURSOR_SQL,
    SELECT_PENDING_FETCH_SQL,
    SELECT_UNSCORED_FILINGS_SQL,
    UPSERT_FILING_CURSOR_SQL,
    PostgresFilingStore,
)
from shrap.intelligence.market_phase import interval_for_phase, read_latest_phase

# --- realistic EDGAR fixtures --------------------------------------------------

APPLE_ACCESSION = "0000320193-26-000070"
APPLE_INDEX_URL = (
    "https://www.sec.gov/Archives/edgar/data/320193/"
    "000032019326000070/0000320193-26-000070-index.htm"
)
APPLE_TITLE = "8-K - APPLE INC (0000320193) (Filer)"

FILING_8K_BODY = (
    "<SEC-DOCUMENT><TYPE>8-K<TEXT><html><body>"
    "<p>Item 5.02 Departure of Directors or Certain Officers; Election of "
    "Directors. On July 19, 2026, the registrant's Chief Financial Officer "
    "notified the Board of an intention to resign.</p>"
    "<p>Item 9.01 Financial Statements and Exhibits. Exhibit 99.1 press "
    "release.</p>"
    "</body></html></TEXT></SEC-DOCUMENT>"
)


# --- fakes ---------------------------------------------------------------------


class FakeEdgarResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class FakeEdgarHTTP:
    def __init__(self, response: FakeEdgarResponse) -> None:
        self._response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get(
        self, url: str, *, params: dict[str, str], headers: dict[str, str], timeout: float
    ) -> FakeEdgarResponse:
        self.calls.append((url, headers))
        return self._response


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
        return FakeLLMResult(content, model=f"model-for-{tier}")


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
    def __init__(self, phase_entries: list[tuple[str, dict[str, str]]] | None = None) -> None:
        self.published: list[tuple[str, dict[str, str]]] = []
        self._phase_entries = phase_entries or []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.published.append((stream, fields))
        return f"{len(self.published)}-0"

    async def xrevrange(
        self, name: str, max: str = "+", min: str = "-", count: int | None = None
    ) -> list[tuple[str, dict[str, str]]]:
        return self._phase_entries


# --- client: CIK / accession / URL --------------------------------------------


def test_parse_cik_from_archives_url() -> None:
    assert parse_cik(APPLE_INDEX_URL) == "320193"
    assert parse_cik("https://www.sec.gov/Archives/edgar/data/0000320193/x/y.htm") == "320193"
    assert parse_cik(None) is None
    assert parse_cik("https://example.com/no-cik-here") is None


def test_accession_from_item_id() -> None:
    assert accession_from_item_id(f"edgar:{APPLE_ACCESSION}") == APPLE_ACCESSION
    assert accession_from_item_id("arxiv:2506.01234") is None
    assert accession_from_item_id("edgar:") is None


def test_filing_txt_url_builds_full_submission_path() -> None:
    assert filing_txt_url("320193", APPLE_ACCESSION) == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000070/0000320193-26-000070.txt"
    )


def test_parse_roster_keys_by_cik_and_resolves_ticker() -> None:
    roster = parse_roster("AAPL:320193, NVDA:1045810 ,,bad-pair, :999")
    assert len(roster) == 2
    assert roster.ticker_for("320193") == "AAPL"
    assert roster.ticker_for("0000320193") == "AAPL"  # leading zeros normalized
    assert roster.ticker_for("1045810") == "NVDA"
    assert roster.ticker_for("999999") is None
    assert roster.ticker_for(None) is None


def test_derive_company_and_headline() -> None:
    assert derive_company(APPLE_TITLE) == "APPLE INC"
    assert derive_company(None) is None
    assert derive_headline("5.02", "APPLE INC") == "8-K Item 5.02 — APPLE INC"
    assert derive_headline("8.01", None) == "8-K Item 8.01 — filer"


# --- client: item-code extraction / section split ------------------------------


def test_strip_markup_and_extract_item_codes() -> None:
    text = strip_markup(FILING_8K_BODY)
    assert "<" not in text
    assert extract_item_codes(text) == ["5.02", "9.01"]


def test_extract_item_codes_dedupes_preserving_order() -> None:
    assert extract_item_codes("Item 2.02 ... Item 8.01 ... Item 2.02 again") == ["2.02", "8.01"]


def test_split_item_sections_partitions_by_header() -> None:
    text = strip_markup(FILING_8K_BODY)
    sections = split_item_sections(text)
    assert set(sections) == {"5.02", "9.01"}
    assert "Chief Financial Officer" in sections["5.02"]
    assert "Chief Financial Officer" not in sections["9.01"]
    assert "press release" in sections["9.01"]


# --- client: EDGAR fetch -------------------------------------------------------


async def test_fetch_filing_text_returns_plain_text() -> None:
    client = EdgarFilingClient("shrap-firm/0.1 filing-processor (mike@example.com)")
    http = FakeEdgarHTTP(FakeEdgarResponse(200, FILING_8K_BODY))

    text = await client.fetch_filing_text(http, "320193", APPLE_ACCESSION)  # type: ignore[arg-type]

    assert "Item 5.02" in text
    url, headers = http.calls[0]
    assert url.endswith("0000320193-26-000070.txt")
    assert "filing-processor" in headers["User-Agent"]


async def test_fetch_filing_text_non_200_raises_with_status() -> None:
    client = EdgarFilingClient("ua")
    http = FakeEdgarHTTP(FakeEdgarResponse(429, ""))

    with pytest.raises(FilingFetchError) as exc:
        await client.fetch_filing_text(http, "320193", APPLE_ACCESSION)  # type: ignore[arg-type]
    assert exc.value.status_code == 429


# --- scorer parsing ------------------------------------------------------------


def _verdict_json(
    materiality: int,
    category: str = "other-events",
    summary: str = "s",
    symbols: tuple[str, ...] = ("AAPL",),
    relevant: bool = True,
    item_code: str = "8.01",
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


def test_parse_filing_response_valid_material() -> None:
    verdict = parse_filing_response(
        APPLE_ACCESSION,
        "5.02",
        _verdict_json(2, category="officer-change", summary="CFO resigned."),
        ("AAPL",),
    )
    assert verdict == FilingVerdict(
        APPLE_ACCESSION, "5.02", True, ("AAPL",), "officer-change", 2, "CFO resigned."
    )


def test_parse_filing_response_unparseable_drops_to_zero() -> None:
    verdict = parse_filing_response(APPLE_ACCESSION, "8.01", "not json at all", ("AAPL",))
    assert verdict.materiality == 0
    assert verdict.relevant is False
    assert verdict.category == "other"
    assert verdict.item_code == "8.01"  # our extracted code, not the model's
    assert "unparseable" in verdict.summary


def test_parse_filing_response_unknown_category_and_symbol_fallback() -> None:
    verdict = parse_filing_response(
        APPLE_ACCESSION,
        "2.02",
        '{"relevant": true, "symbols": ["ZZZZ"], "category": "made-up", "materiality": 9}',
        ("AAPL",),
    )
    assert verdict.category == "other"
    assert verdict.materiality == 3  # clamped
    assert verdict.symbols == ("AAPL",)  # invented symbol dropped, fell back to roster


# --- phase → interval ----------------------------------------------------------


def test_interval_for_phase_maps_active_and_idle() -> None:
    assert interval_for_phase("open", 600.0, 3600.0) == 600.0
    assert interval_for_phase("after-hours", 600.0, 3600.0) == 600.0
    assert interval_for_phase("overnight", 600.0, 3600.0) == 3600.0
    assert interval_for_phase("closed-day", 600.0, 3600.0) == 3600.0
    assert interval_for_phase(None, 600.0, 3600.0) == 600.0  # fallback


async def test_read_latest_phase_reads_envelope_payload() -> None:
    envelope = Envelope.new(
        produced_by="market-phase@host",
        schema_version="1.0.0",
        payload={"phase": "open", "session_date": "2026-07-19"},
    )
    entries = [("1-0", envelope.to_redis_fields())]
    phase = await read_latest_phase(FakeRedis(phase_entries=entries))
    assert phase == "open"


# --- poll pass -----------------------------------------------------------------


def _candidate_row(item_id: str, url: str | None, fetched_at: datetime) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "title": APPLE_TITLE,
        "url": url,
        "external_ts": datetime(2026, 7, 19, 20, 30, tzinfo=UTC),
        "fetched_at": fetched_at,
    }


def _roster_config() -> FilingRunConfig:
    return FilingRunConfig(roster=parse_roster("AAPL:320193,NVDA:1045810"))


async def test_poll_pass_records_tier3_matches_and_advances_cursor() -> None:
    pool = FakePool()
    older = datetime(2026, 7, 19, 20, 0, tzinfo=UTC)
    newer = datetime(2026, 7, 19, 21, 0, tzinfo=UTC)
    non_tier3_url = "https://www.sec.gov/Archives/edgar/data/999999/x/y-index.htm"
    pool.conn.fetch_results[SELECT_CANDIDATE_FILINGS_SQL] = [
        _candidate_row(f"edgar:{APPLE_ACCESSION}", APPLE_INDEX_URL, older),
        _candidate_row("edgar:9999999999-26-000001", non_tier3_url, newer),
    ]
    pool.conn.insert_results = ["INSERT 0 1"]  # only the Tier 3 match is inserted
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]

    counts = await poll_pass(store, _roster_config(), datetime(2026, 7, 19, 22, tzinfo=UTC))

    assert counts.seen == 2
    assert counts.matched == 1  # non-Tier-3 dropped here, not upstream
    assert counts.recorded == 1
    assert pool.conn.transactions_entered == 1
    inserts = [args for sql, args in pool.conn.executed if sql == INSERT_FILING_SQL]
    assert len(inserts) == 1
    assert inserts[0][0] == APPLE_ACCESSION  # accession from item_id
    assert inserts[0][2] == "AAPL"  # resolved ticker
    cursor_calls = [args for sql, args in pool.conn.executed if sql == UPSERT_FILING_CURSOR_SQL]
    assert len(cursor_calls) == 1
    _feed, last_fetched_at, seen, _now = cursor_calls[0]
    assert last_fetched_at == newer  # advanced past every row seen, matched or not
    assert seen == 2


# --- fetch pass ----------------------------------------------------------------


def _pending_row() -> dict[str, Any]:
    return {
        "accession": APPLE_ACCESSION,
        "cik": "320193",
        "symbol": "AAPL",
        "filing_url": APPLE_INDEX_URL,
    }


async def test_fetch_pass_fetches_and_extracts_item_codes() -> None:
    pool = FakePool()
    pool.conn.fetch_results[SELECT_PENDING_FETCH_SQL] = [_pending_row()]
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]
    source = FakeFilingSource([strip_markup(FILING_8K_BODY)])
    config = FilingRunConfig(roster=parse_roster("AAPL:320193"))

    counts = await fetch_pass(source, object(), store, config)  # type: ignore[arg-type]

    assert counts.fetched == 1
    assert counts.failed == 0
    marks = [args for sql, args in pool.conn.executed if sql == MARK_FILING_FETCHED_SQL]
    assert len(marks) == 1
    accession, _full_text, item_codes_json, _fetched_at = marks[0]
    assert accession == APPLE_ACCESSION
    assert json.loads(item_codes_json) == ["5.02", "9.01"]


async def test_fetch_pass_backs_off_on_rate_limit() -> None:
    pool = FakePool()
    pool.conn.fetch_results[SELECT_PENDING_FETCH_SQL] = [_pending_row(), _pending_row()]
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]
    source = FakeFilingSource([FilingFetchError("u", 429)])
    config = FilingRunConfig(roster=parse_roster("AAPL:320193"))

    counts = await fetch_pass(source, object(), store, config)  # type: ignore[arg-type]

    assert counts.fetched == 0
    assert counts.failed == 1
    assert len(source.calls) == 1  # broke out of the pass rather than hammering EDGAR
    assert not [sql for sql, _ in pool.conn.executed if sql == MARK_FILING_FETCHED_SQL]


# --- score pass ----------------------------------------------------------------


def _unscored_row(item_codes: list[str]) -> dict[str, Any]:
    return {
        "accession": APPLE_ACCESSION,
        "symbol": "AAPL",
        "title": APPLE_TITLE,
        "company": "APPLE INC",
        "filing_date": datetime(2026, 7, 19, 20, 30, tzinfo=UTC),
        "item_codes": item_codes,
        "full_text": strip_markup(FILING_8K_BODY),
    }


def _config() -> FilingRunConfig:
    return FilingRunConfig(roster=parse_roster("AAPL:320193"))


async def test_score_pass_materiality_zero_stored_not_published() -> None:
    pool = FakePool()
    pool.conn.fetch_results[SELECT_UNSCORED_FILINGS_SQL] = [_unscored_row(["9.01"])]
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]
    llm = FakeLLM([_verdict_json(0, summary="boilerplate", symbols=(), relevant=False)])
    redis = FakeRedis()
    publisher = EventPublisher(redis)  # type: ignore[arg-type]

    counts = await score_pass(store, llm, publisher, _config())  # type: ignore[arg-type]

    assert counts.items_scored == 1
    assert counts.published == 0
    marked = [args for sql, args in pool.conn.executed if sql == MARK_FILING_SCORED_SQL]
    assert len(marked) == 1  # filing marked scored even though nothing published
    assert redis.published == []


async def test_score_pass_history_before_mark_and_think_false() -> None:
    pool = FakePool()
    pool.conn.fetch_results[SELECT_UNSCORED_FILINGS_SQL] = [_unscored_row(["8.01"])]
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]
    llm = FakeLLM([_verdict_json(1, category="other-events")])
    publisher = EventPublisher(FakeRedis())  # type: ignore[arg-type]

    await score_pass(store, llm, publisher, _config())  # type: ignore[arg-type]

    assert llm.calls[0]["json_mode"] is True
    assert llm.calls[0]["think"] is False  # bulk classification never thinks out loud
    executed_sql = [sql for sql, _ in pool.conn.executed]
    assert executed_sql.index(INSERT_FILING_VERDICT_HISTORY_SQL) < executed_sql.index(
        MARK_FILING_SCORED_SQL
    )
    history = [args for sql, args in pool.conn.executed if sql == INSERT_FILING_VERDICT_HISTORY_SQL]
    assert history[0][2] == FILING_PROMPT_VERSION  # prompt_version stamped
    assert history[0][1] == "8.01"  # item_code stamped


async def test_score_pass_publishes_envelope_conformant_signal() -> None:
    pool = FakePool()
    pool.conn.fetch_results[SELECT_UNSCORED_FILINGS_SQL] = [_unscored_row(["5.02"])]
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]
    llm = FakeLLM([_verdict_json(1, category="officer-change", summary="CFO resigned.")])
    redis = FakeRedis()
    publisher = EventPublisher(redis)  # type: ignore[arg-type]

    counts = await score_pass(store, llm, publisher, _config())  # type: ignore[arg-type]

    assert counts.published == 1
    stream, fields = redis.published[0]
    assert stream == STREAM_INTELLIGENCE_SIGNAL
    envelope = Envelope.from_redis_fields(normalize_redis_fields(fields))  # round-trips
    assert envelope.payload is not None
    payload = envelope.payload
    assert payload["signal_type"] == "filing"
    assert payload["symbols"] == ["AAPL"]
    assert payload["category"] == "officer-change"
    assert payload["materiality"] == 1
    assert payload["item_code"] == "5.02"
    assert payload["source"] == "sec-edgar"
    assert payload["item_ref"] == f"{APPLE_ACCESSION}#5.02"
    assert payload["headline"] == "8-K Item 5.02 — APPLE INC"
    assert payload["published_at"] == "2026-07-19T20:30:00+00:00"


async def test_score_pass_escalates_material_item_appends_second_history_row() -> None:
    pool = FakePool()
    pool.conn.fetch_results[SELECT_UNSCORED_FILINGS_SQL] = [_unscored_row(["2.02"])]
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]
    llm = FakeLLM(
        [
            _verdict_json(2, category="results", summary="local read"),
            _verdict_json(3, category="results", summary="cloud read"),
        ]
    )
    redis = FakeRedis()
    publisher = EventPublisher(redis)  # type: ignore[arg-type]

    counts = await score_pass(store, llm, publisher, _config())  # type: ignore[arg-type]

    assert counts.escalated == 1
    assert len(llm.calls) == 2
    assert llm.calls[0]["tier"] == "local-classification"
    assert llm.calls[1]["tier"] == "cloud-default"
    history = [args for sql, args in pool.conn.executed if sql == INSERT_FILING_VERDICT_HISTORY_SQL]
    assert len(history) == 2  # both the local and the escalation verdict logged
    _stream, fields = redis.published[0]
    payload = Envelope.from_redis_fields(normalize_redis_fields(fields)).payload
    assert payload is not None
    assert payload["materiality"] == 3  # higher (cloud) verdict wins for publishing
    assert payload["summary"] == "cloud read"


# --- store cursor read ---------------------------------------------------------


async def test_cursor_ts_reads_last_fetched_at() -> None:
    pool = FakePool()
    ts = datetime(2026, 7, 19, 21, 0, tzinfo=UTC)
    pool.conn.fetchrow_results[SELECT_FILING_CURSOR_SQL] = {"last_fetched_at": ts}
    store = PostgresFilingStore(pool)  # type: ignore[arg-type]

    assert await store.cursor_ts("sec-edgar-8k") == ts


async def test_cursor_ts_none_when_no_row() -> None:
    store = PostgresFilingStore(FakePool())  # type: ignore[arg-type]
    assert await store.cursor_ts("sec-edgar-8k") is None


# --- settings ------------------------------------------------------------------


def test_settings_parse_env_and_mask_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    from shrap.agents.intelligence.filing_processor.config import Settings

    monkeypatch.setenv("FILING_PROCESSOR_ROSTER", "aapl:320193, nvda:1045810")
    settings = Settings()

    config = settings.run_config()
    assert config.roster.ticker_for("320193") == "AAPL"
    assert config.roster.ticker_for("1045810") == "NVDA"
    redacted = settings.redacted()
    assert redacted["postgres_dsn"] == "***"
    assert "filing-processor" in str(redacted["sec_user_agent"])
    assert settings.produced_by().startswith("filing-processor@")
