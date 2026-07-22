"""Tests for the News Analyzer: client parsing, store SQL, scoring, service."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from shrap.events import Envelope, EventPublisher, normalize_redis_fields
from shrap.intelligence.news_analyzer.client import AlpacaNewsClient, parse_news_item
from shrap.intelligence.news_analyzer.scorer import (
    NEWS_PROMPT_VERSION,
    MaterialityVerdict,
    parse_news_response,
)
from shrap.intelligence.news_analyzer.service import (
    STREAM_INTELLIGENCE_SIGNAL,
    NewsRunConfig,
    fetch_pass,
    interval_for_phase,
    read_latest_phase,
    score_pass,
)
from shrap.intelligence.news_analyzer.store import (
    INSERT_NEWS_ITEM_SQL,
    INSERT_NEWS_VERDICT_HISTORY_SQL,
    MARK_SCORED_SQL,
    SELECT_UNSCORED_SQL,
    UPSERT_NEWS_CURSOR_SQL,
    PostgresNewsStore,
    ScorableItem,
)

# --- realistic Alpaca news fixture ---------------------------------------------

ALPACA_NEWS_BODY: dict[str, Any] = {
    "news": [
        {
            "id": 24843171,
            "headline": "NVIDIA Reports Record Q2 Revenue, Raises Full-Year Guidance",
            "author": "Benzinga Staff",
            "created_at": "2026-07-19T20:15:00Z",
            "updated_at": "2026-07-19T20:20:00Z",
            "summary": "NVIDIA posted record quarterly revenue and lifted its outlook.",
            "content": "<p>Full article body that the seed does not store.</p>",
            "url": "https://www.benzinga.com/news/nvda-q2",
            "symbols": ["NVDA", "AAPL"],
            "source": "benzinga",
        }
    ],
    "next_page_token": None,
}


# --- fakes ---------------------------------------------------------------------


class FakeNewsResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._body


class FakeNewsHTTP:
    def __init__(self, bodies: list[dict[str, Any]]) -> None:
        self._bodies = bodies
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get(self, url: str, headers: dict[str, str]) -> FakeNewsResponse:
        self.calls.append((url, headers))
        return FakeNewsResponse(self._bodies[len(self.calls) - 1])


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
        if sql == INSERT_NEWS_ITEM_SQL and self.insert_results:
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


class FakeNewsSource:
    def __init__(self, items: list[Any]) -> None:
        self._items = items
        self.starts: list[str] = []

    async def get_news(
        self, http_client: object, symbols: list[str], start: str, limit: int = 50
    ) -> list[Any]:
        self.starts.append(start)
        return self._items


def _from_scorable(item: ScorableItem) -> Any:
    """A minimal NewsItem-shaped object for upsert tests."""

    from shrap.intelligence.news_analyzer.client import NewsItem

    return NewsItem(
        item_id=item.item_id,
        headline=item.headline,
        summary=item.summary,
        author=None,
        url=None,
        news_source="benzinga",
        symbols=item.symbols,
        published_at=item.published_at,
        updated_at=None,
        payload={"id": item.item_id},
    )


# --- client parsing ------------------------------------------------------------


def test_parse_news_item_normalizes_fields() -> None:
    item = parse_news_item(ALPACA_NEWS_BODY["news"][0])

    assert item.item_id == "24843171"
    assert item.headline.startswith("NVIDIA Reports Record Q2")
    assert item.symbols == ("NVDA", "AAPL")
    assert item.news_source == "benzinga"
    assert item.summary is not None and item.summary.startswith("NVIDIA posted")
    assert item.published_at is not None and item.published_at.year == 2026
    assert item.url == "https://www.benzinga.com/news/nvda-q2"


def test_parse_news_item_missing_headline_raises() -> None:
    with pytest.raises(ValueError, match="headline"):
        parse_news_item({"id": 1, "symbols": ["NVDA"]})


async def test_client_get_news_parses_and_builds_url() -> None:
    from shrap.intelligence.market_data import AlpacaMarketDataSettings

    settings = AlpacaMarketDataSettings(api_key="k", secret_key="s")  # type: ignore[arg-type]
    client = AlpacaNewsClient(settings)
    http = FakeNewsHTTP([ALPACA_NEWS_BODY])

    items = await client.get_news(http, symbols=["NVDA", "AAPL"], start="2026-07-19T00:00:00+00:00")  # type: ignore[arg-type]

    assert len(items) == 1
    assert items[0].item_id == "24843171"
    url, headers = http.calls[0]
    assert "symbols=AAPL,NVDA" in url  # symbols sorted
    assert "sort=asc" in url
    assert headers["APCA-API-KEY-ID"] == "k"


# --- store ---------------------------------------------------------------------


def _item(item_id: str, ts: datetime | None) -> Any:
    return _from_scorable(
        ScorableItem(
            item_id=item_id, headline="h", summary=None, symbols=("NVDA",), published_at=ts
        )
    )


async def test_upsert_items_counts_new_rows_and_advances_cursor_atomically() -> None:
    pool = FakePool()
    pool.conn.insert_results = ["INSERT 0 1", "INSERT 0 0", "INSERT 0 1"]
    store = PostgresNewsStore(pool)  # type: ignore[arg-type]
    fetched_at = datetime(2026, 7, 19, 21, 0, tzinfo=UTC)
    older = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    newer = datetime(2026, 7, 19, 20, 0, tzinfo=UTC)

    inserted = await store.upsert_items(
        "alpaca-news",
        [_item("a", newer), _item("b", None), _item("c", older)],
        fetched_at,
    )

    assert inserted == 2  # the duplicate counted zero
    assert pool.conn.transactions_entered == 1
    cursor_calls = [args for sql, args in pool.conn.executed if sql == UPSERT_NEWS_CURSOR_SQL]
    assert len(cursor_calls) == 1
    feed, newest_ts, last_item_id, items_seen, updated_at = cursor_calls[0]
    assert feed == "alpaca-news"
    assert newest_ts == newer  # max published_at among genuinely inserted rows
    assert last_item_id == "c"
    assert items_seen == 2
    assert updated_at == fetched_at


# --- scorer parsing ------------------------------------------------------------


def test_parse_news_response_valid_material() -> None:
    verdict = parse_news_response(
        "1",
        '{"relevant": true, "symbols": ["NVDA"], "category": "earnings", '
        '"materiality": 2, "summary": "Record revenue."}',
        ("NVDA", "AAPL"),
    )
    assert verdict == MaterialityVerdict("1", True, ("NVDA",), "earnings", 2, "Record revenue.")


def test_parse_news_response_unparseable_drops_to_zero() -> None:
    verdict = parse_news_response("1", "the model rambled instead of JSON", ("NVDA",))
    assert verdict.materiality == 0
    assert verdict.relevant is False
    assert verdict.category == "other"
    assert "unparseable" in verdict.summary


def test_parse_news_response_unknown_category_and_symbols_fallback() -> None:
    verdict = parse_news_response(
        "1",
        '{"relevant": true, "symbols": ["ZZZZ"], "category": "made-up", "materiality": 5}',
        ("NVDA",),
    )
    assert verdict.category == "other"
    assert verdict.materiality == 3  # clamped
    assert verdict.symbols == ("NVDA",)  # invented symbol dropped, fell back to item's


# --- phase → interval ----------------------------------------------------------


def test_interval_for_phase_maps_active_and_idle() -> None:
    assert interval_for_phase("pre-open", 600.0, 3600.0) == 600.0
    assert interval_for_phase("open", 600.0, 3600.0) == 600.0
    assert interval_for_phase("after-hours", 600.0, 3600.0) == 600.0
    assert interval_for_phase("overnight", 600.0, 3600.0) == 3600.0
    assert interval_for_phase("closed-day", 600.0, 3600.0) == 3600.0
    assert interval_for_phase(None, 600.0, 3600.0) == 600.0  # fallback
    assert interval_for_phase("something-new", 600.0, 3600.0) == 600.0  # unknown → active


async def test_read_latest_phase_empty_stream_returns_none() -> None:
    phase = await read_latest_phase(FakeRedis(phase_entries=[]))
    assert phase is None


async def test_read_latest_phase_reads_envelope_payload() -> None:
    envelope = Envelope.new(
        produced_by="market-phase@host",
        schema_version="1.0.0",
        payload={"phase": "open", "session_date": "2026-07-19"},
    )
    entries = [("1-0", envelope.to_redis_fields())]
    phase = await read_latest_phase(FakeRedis(phase_entries=entries))
    assert phase == "open"


# --- score pass ----------------------------------------------------------------


def _unscored_row(item_id: str, symbols: list[str]) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "headline": "NVIDIA earnings",
        "summary": "A summary.",
        "symbols": symbols,
        "published_at": datetime(2026, 7, 19, 20, 15, tzinfo=UTC),
    }


def _config() -> NewsRunConfig:
    return NewsRunConfig(symbols=("NVDA",))


def _verdict_json(
    materiality: int,
    category: str = "other",
    summary: str = "s",
    symbols: tuple[str, ...] = ("NVDA",),
    relevant: bool = True,
) -> str:
    return json.dumps(
        {
            "relevant": relevant,
            "symbols": list(symbols),
            "category": category,
            "materiality": materiality,
            "summary": summary,
        }
    )


async def test_score_pass_materiality_zero_stored_not_published() -> None:
    pool = FakePool()
    pool.conn.fetch_results[SELECT_UNSCORED_SQL] = [_unscored_row("1", ["NVDA"])]
    store = PostgresNewsStore(pool)  # type: ignore[arg-type]
    llm = FakeLLM([_verdict_json(0, summary="noise", symbols=(), relevant=False)])
    redis = FakeRedis()
    publisher = EventPublisher(redis)  # type: ignore[arg-type]

    counts = await score_pass(store, llm, publisher, _config())  # type: ignore[arg-type]

    assert counts.scored == 1
    assert counts.published == 0
    marked = [args for sql, args in pool.conn.executed if sql == MARK_SCORED_SQL]
    assert len(marked) == 1  # stored (marked) even though not published
    assert redis.published == []  # nothing on intelligence.signal


async def test_score_pass_history_before_mark_and_bulk_think_false() -> None:
    pool = FakePool()
    pool.conn.fetch_results[SELECT_UNSCORED_SQL] = [_unscored_row("1", ["NVDA"])]
    store = PostgresNewsStore(pool)  # type: ignore[arg-type]
    llm = FakeLLM([_verdict_json(1, category="product")])
    publisher = EventPublisher(FakeRedis())  # type: ignore[arg-type]

    await score_pass(store, llm, publisher, _config())  # type: ignore[arg-type]

    assert llm.calls[0]["json_mode"] is True
    assert llm.calls[0]["think"] is False  # bulk classification never thinks out loud
    executed_sql = [sql for sql, _ in pool.conn.executed]
    assert executed_sql.index(INSERT_NEWS_VERDICT_HISTORY_SQL) < executed_sql.index(MARK_SCORED_SQL)
    history = [args for sql, args in pool.conn.executed if sql == INSERT_NEWS_VERDICT_HISTORY_SQL]
    assert history[0][1] == NEWS_PROMPT_VERSION  # prompt_version stamped


async def test_score_pass_publishes_envelope_conformant_signal() -> None:
    pool = FakePool()
    pool.conn.fetch_results[SELECT_UNSCORED_SQL] = [_unscored_row("24843171", ["NVDA"])]
    store = PostgresNewsStore(pool)  # type: ignore[arg-type]
    llm = FakeLLM([_verdict_json(1, category="earnings", summary="Record revenue.")])
    redis = FakeRedis()
    publisher = EventPublisher(redis)  # type: ignore[arg-type]

    counts = await score_pass(store, llm, publisher, _config())  # type: ignore[arg-type]

    assert counts.published == 1
    assert len(redis.published) == 1
    stream, fields = redis.published[0]
    assert stream == STREAM_INTELLIGENCE_SIGNAL
    envelope = Envelope.from_redis_fields(normalize_redis_fields(fields))  # round-trips
    assert envelope.payload is not None
    payload = envelope.payload
    assert payload["signal_type"] == "news"
    assert payload["symbols"] == ["NVDA"]
    assert payload["category"] == "earnings"
    assert payload["materiality"] == 1
    assert payload["source"] == "alpaca-news"
    assert payload["item_ref"] == "24843171"
    assert payload["published_at"] == "2026-07-19T20:15:00+00:00"


async def test_score_pass_escalates_material_item_appends_second_history_row() -> None:
    pool = FakePool()
    pool.conn.fetch_results[SELECT_UNSCORED_SQL] = [_unscored_row("1", ["NVDA"])]
    store = PostgresNewsStore(pool)  # type: ignore[arg-type]
    llm = FakeLLM(
        [
            _verdict_json(2, category="ma", summary="local read"),
            _verdict_json(3, category="ma", summary="cloud read"),
        ]
    )
    redis = FakeRedis()
    publisher = EventPublisher(redis)  # type: ignore[arg-type]

    counts = await score_pass(store, llm, publisher, _config())  # type: ignore[arg-type]

    assert counts.escalated == 1
    assert len(llm.calls) == 2
    assert llm.calls[0]["tier"] == "local-classification"
    assert llm.calls[1]["tier"] == "cloud-default"
    history = [args for sql, args in pool.conn.executed if sql == INSERT_NEWS_VERDICT_HISTORY_SQL]
    assert len(history) == 2  # both the local and the escalation verdict logged
    # Higher verdict wins for publishing: cloud read (materiality 3, tighter summary).
    _stream, fields = redis.published[0]
    payload = Envelope.from_redis_fields(normalize_redis_fields(fields)).payload
    assert payload is not None
    assert payload["materiality"] == 3
    assert payload["summary"] == "cloud read"


# --- fetch pass ----------------------------------------------------------------


async def test_fetch_pass_uses_lookback_when_no_cursor() -> None:
    pool = FakePool()
    pool.conn.insert_results = ["INSERT 0 1"]
    store = PostgresNewsStore(pool)  # type: ignore[arg-type]
    source = FakeNewsSource([_item("1", datetime(2026, 7, 19, tzinfo=UTC))])
    config = NewsRunConfig(symbols=("NVDA",), lookback_days=3)
    now = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)

    result = await fetch_pass(source, object(), store, config, now)  # type: ignore[arg-type]

    assert result.fetched == 1
    assert result.inserted == 1
    # No cursor row → start is now - lookback_days.
    assert source.starts[0] == "2026-07-16T12:00:00+00:00"


# --- settings ------------------------------------------------------------------


def test_settings_parse_env_and_mask_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    from shrap.agents.intelligence.news_analyzer.config import Settings

    monkeypatch.setenv("NEWS_ANALYZER_ALPACA_API_KEY", "key")
    monkeypatch.setenv("NEWS_ANALYZER_ALPACA_SECRET_KEY", "secret")
    monkeypatch.setenv("NEWS_ANALYZER_SYMBOLS", "nvda, aapl , tsla")
    settings = Settings()

    assert settings.symbol_list() == ("NVDA", "AAPL", "TSLA")
    assert settings.run_config().symbols == ("NVDA", "AAPL", "TSLA")
    redacted = settings.redacted()
    assert redacted["postgres_dsn"] == "***"
    assert redacted["alpaca"] == {  # secrets masked, never raw values
        "api_key": "***",
        "secret_key": "***",
        "endpoint": "https://data.alpaca.markets/",
        "mode": "data-readonly",
    }
    assert settings.produced_by().startswith("news-analyzer@")
