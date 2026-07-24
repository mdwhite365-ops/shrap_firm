"""Tests for the historical daily-bars store, Alpaca fetch, and backfill CLI core.

Mirrors the existing store/client idioms: a fake ``AsyncHttpClient`` returning
JSON page bodies (see ``tests/intelligence/test_regime_agent.py``) and a fake
asyncpg pool/connection recording executed SQL (see
``tests/intelligence/test_filing_processor_backfill.py``). No real DB or HTTP.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from shrap.intelligence.market_data import AlpacaMarketDataSettings
from shrap.market_data.backfill import (
    BackfillSummary,
    backfill_tickers,
    parse_tickers,
    read_tickers_file,
    resolve_tickers,
    resolve_window,
)
from shrap.market_data.client import AlpacaDailyBarsClient
from shrap.market_data.store import (
    UPSERT_DAILY_BAR_SQL,
    DailyBarRow,
    PostgresDailyBarStore,
)

# --- fakes ---------------------------------------------------------------------


class FakeResponse:
    def __init__(self, body: Any) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._body


class FakeHttpClient:
    def __init__(self, bodies: list[Any]) -> None:
        self._bodies = bodies
        self.urls: list[str] = []

    async def get(self, url: str, headers: dict[str, str]) -> FakeResponse:
        self.urls.append(url)
        return FakeResponse(self._bodies.pop(0))


class FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, sql: str, *args: object) -> object:
        self.executed.append((sql, args))
        return "OK"


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


class FakeBarsClient:
    """Records get_daily_bars calls and returns a preset row list per ticker."""

    def __init__(self, rows_by_ticker: dict[str, list[DailyBarRow]]) -> None:
        self._rows = rows_by_ticker
        self.calls: list[tuple[str, str, str]] = []

    async def get_daily_bars(
        self,
        http_client: object,
        ticker: str,
        start_day: str,
        end_day: str,
        *,
        limit: int = 10000,
    ) -> list[DailyBarRow]:
        self.calls.append((ticker, start_day, end_day))
        return self._rows.get(ticker, [])


def _settings() -> AlpacaMarketDataSettings:
    return AlpacaMarketDataSettings(
        api_key="data-key",
        secret_key=SecretStr("data-secret"),
        endpoint="https://data.alpaca.markets",  # type: ignore[arg-type]
    )


def _row(ticker: str, day: date) -> DailyBarRow:
    return DailyBarRow(
        ticker=ticker,
        session_date=day,
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=100.0,
        trade_count=7,
        vwap=1.4,
        adjustment="all",
        source="alpaca-iex",
    )


# --- client: pagination + parsing ---------------------------------------------


async def test_get_daily_bars_paginates_and_tags_provenance() -> None:
    page_one = {
        "bars": {
            "AAPL": [
                {
                    "t": "2026-07-01T04:00:00Z",
                    "o": 1,
                    "h": 2,
                    "l": 0.5,
                    "c": 1.5,
                    "v": 100,
                    "n": 7,
                    "vw": 1.4,
                }
            ]
        },
        "next_page_token": "tok",
    }
    page_two = {
        "bars": {
            "AAPL": [{"t": "2026-07-02T04:00:00Z", "o": 1.5, "h": 2, "l": 1, "c": 1.8, "v": 90}]
        },
        "next_page_token": None,
    }
    http = FakeHttpClient([page_one, page_two])
    client = AlpacaDailyBarsClient(_settings())

    rows = await client.get_daily_bars(http, "aapl", "2026-07-01", "2026-07-02")

    assert len(rows) == 2
    assert rows[0] == DailyBarRow(
        ticker="AAPL",
        session_date=date(2026, 7, 1),
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=100.0,
        trade_count=7,
        vwap=1.4,
        adjustment="all",
        source="alpaca-iex",
    )
    # Second bar lacked n/vw — nullable columns come back None, not zero.
    assert rows[1].trade_count is None
    assert rows[1].vwap is None
    # First URL carries the query contract; second URL carries the page token.
    assert "symbols=AAPL" in http.urls[0]
    assert "timeframe=1Day" in http.urls[0]
    assert "adjustment=all" in http.urls[0]
    assert "feed=iex" in http.urls[0]
    assert "start=2026-07-01" in http.urls[0]
    assert "end=2026-07-02" in http.urls[0]
    assert "page_token=tok" in http.urls[1]


async def test_get_daily_bars_rejects_malformed_shape() -> None:
    http = FakeHttpClient([{"bars": [1, 2, 3]}])
    client = AlpacaDailyBarsClient(_settings())
    with pytest.raises(ValueError, match="must be an object"):
        await client.get_daily_bars(http, "AAPL", "2026-07-01", "2026-07-02")


# --- store: upsert idempotency ------------------------------------------------


def test_upsert_sql_is_conflict_upsert() -> None:
    assert "ON CONFLICT (ticker, session_date, adjustment) DO UPDATE" in UPSERT_DAILY_BAR_SQL


async def test_upsert_bars_uses_upsert_sql_and_is_rerunnable() -> None:
    pool = FakePool()
    store = PostgresDailyBarStore(pool)  # type: ignore[arg-type]
    bar = _row("AAPL", date(2026, 7, 1))

    first = await store.upsert_bars([bar])
    second = await store.upsert_bars([bar])  # re-run: same key, no duplicate row

    assert first == second == 1
    upserts = [(sql, args) for sql, args in pool.conn.executed if sql == UPSERT_DAILY_BAR_SQL]
    assert len(upserts) == 2  # each call issues the idempotent ON CONFLICT upsert
    _, args = upserts[0]
    assert args[0] == "AAPL"
    assert args[1] == date(2026, 7, 1)
    assert args[9] == "all"  # adjustment
    assert args[10] == "alpaca-iex"  # source


async def test_ensure_schema_creates_schema_then_table() -> None:
    pool = FakePool()
    store = PostgresDailyBarStore(pool)  # type: ignore[arg-type]

    await store.ensure_schema()

    sqls = [sql for sql, _ in pool.conn.executed]
    assert "CREATE SCHEMA IF NOT EXISTS market_data" in sqls[0]
    assert "market_data.daily_bars" in sqls[1]


# --- date-window defaults -----------------------------------------------------


def test_resolve_window_defaults_five_years_back_to_today() -> None:
    today = date(2026, 7, 23)
    start, end = resolve_window(None, None, today)
    assert end == "2026-07-23"
    assert start == (today - timedelta(days=5 * 365)).isoformat()


def test_resolve_window_honors_explicit_bounds() -> None:
    start, end = resolve_window("2021-01-01", "2026-07-01", date(2026, 7, 23))
    assert start == "2021-01-01"
    assert end == "2026-07-01"


def test_resolve_window_rejects_inverted_range() -> None:
    with pytest.raises(ValueError, match="is after"):
        resolve_window("2026-07-10", "2026-07-01", date(2026, 7, 23))


def test_resolve_window_rejects_malformed_date() -> None:
    with pytest.raises(ValueError):
        resolve_window("07-01-2026", None, date(2026, 7, 23))


# --- ticker parsing -----------------------------------------------------------


def test_parse_tickers_splits_and_uppercases() -> None:
    assert parse_tickers(" aapl, msft ,nvda") == ["AAPL", "MSFT", "NVDA"]
    assert parse_tickers(None) == []
    assert parse_tickers("") == []


def test_read_tickers_file_ignores_comments_and_blanks(tmp_path: Path) -> None:
    path = tmp_path / "universe.txt"
    path.write_text(
        "\n".join(
            [
                "# launch names",
                "AAPL",
                "",
                "msft  # tech",
                "   ",
                "nvda",
            ]
        ),
        encoding="utf-8",
    )
    assert read_tickers_file(path) == ["AAPL", "MSFT", "NVDA"]


def test_resolve_tickers_merges_and_dedupes(tmp_path: Path) -> None:
    path = tmp_path / "more.txt"
    path.write_text("NVDA\nLMT\n", encoding="utf-8")
    # AAPL appears in both sources; order preserved, first-seen wins.
    assert resolve_tickers("AAPL,NVDA", path) == ["AAPL", "NVDA", "LMT"]


# --- backfill loop: dry-run writes nothing ------------------------------------


async def test_backfill_tickers_dry_run_fetches_but_does_not_write() -> None:
    pool = FakePool()
    store = PostgresDailyBarStore(pool)  # type: ignore[arg-type]
    client = FakeBarsClient(
        {"AAPL": [_row("AAPL", date(2026, 7, 1)), _row("AAPL", date(2026, 7, 2))]}
    )

    summary = await backfill_tickers(
        store,
        client,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        ["AAPL"],
        "2026-07-01",
        "2026-07-02",
        dry_run=True,
        inter_ticker_delay_seconds=0.0,
    )

    assert summary == BackfillSummary(tickers=1, rows_fetched=2, rows_upserted=0, dry_run=True)
    assert client.calls == [("AAPL", "2026-07-01", "2026-07-02")]
    # Nothing written: no UPSERT ever executed against the pool.
    assert all(sql != UPSERT_DAILY_BAR_SQL for sql, _ in pool.conn.executed)


async def test_backfill_tickers_writes_when_not_dry_run() -> None:
    pool = FakePool()
    store = PostgresDailyBarStore(pool)  # type: ignore[arg-type]
    client = FakeBarsClient(
        {
            "AAPL": [_row("AAPL", date(2026, 7, 1))],
            "MSFT": [_row("MSFT", date(2026, 7, 1)), _row("MSFT", date(2026, 7, 2))],
        }
    )

    summary = await backfill_tickers(
        store,
        client,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        ["AAPL", "MSFT"],
        "2026-07-01",
        "2026-07-02",
        dry_run=False,
        inter_ticker_delay_seconds=0.0,
    )

    assert summary == BackfillSummary(tickers=2, rows_fetched=3, rows_upserted=3, dry_run=False)
    upserts = [sql for sql, _ in pool.conn.executed if sql == UPSERT_DAILY_BAR_SQL]
    assert len(upserts) == 3


def test_backfill_summary_render_format() -> None:
    summary = BackfillSummary(tickers=2, rows_fetched=10, rows_upserted=10, dry_run=False)
    assert summary.render() == "tickers=2 rows_fetched=10 rows_upserted=10 dry_run=False"
