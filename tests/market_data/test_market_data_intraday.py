"""Tests for the intraday bars store, Alpaca fetch, and chunked backfill.

Same fake shapes as ``test_market_data_backfill.py`` — a fake HTTP client
returning JSON page bodies and a fake asyncpg pool recording SQL — with one
addition the daily grain never needed: ``executemany``, because at ~390 rows a
ticker-day the per-row loop is not viable.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

import pytest
from pydantic import SecretStr

from shrap.intelligence.market_data import AlpacaMarketDataSettings
from shrap.market_data.client import TIMEFRAME_1MIN, AlpacaIntradayBarsClient
from shrap.market_data.intraday_backfill import backfill_intraday, chunk_window
from shrap.market_data.store import (
    CREATE_INTRADAY_HYPERTABLE_SQL,
    UPSERT_INTRADAY_BAR_SQL,
    IntradayBarRow,
    PostgresIntradayBarStore,
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
    def __init__(self, *, timescale: bool = True) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.many: list[tuple[str, Sequence[Sequence[object]]]] = []
        self._timescale = timescale

    async def execute(self, sql: str, *args: object) -> object:
        self.executed.append((sql, args))
        return "OK"

    async def executemany(self, sql: str, args: Sequence[Sequence[object]]) -> object:
        self.many.append((sql, args))
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        return [{"?column?": 1}] if self._timescale else []


class FakeAcquire:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakePool:
    def __init__(self, *, timescale: bool = True) -> None:
        self.conn = FakeConn(timescale=timescale)

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)


class FakeIntradayClient:
    """Records get_intraday_bars calls and returns a preset row list per ticker."""

    def __init__(self, rows_by_ticker: dict[str, list[IntradayBarRow]]) -> None:
        self._rows = rows_by_ticker
        self.calls: list[tuple[str, str, str]] = []

    async def get_intraday_bars(
        self,
        http_client: object,
        ticker: str,
        start: str,
        end: str,
        *,
        timeframe: str = TIMEFRAME_1MIN,
        limit: int = 10000,
    ) -> list[IntradayBarRow]:
        self.calls.append((ticker, start, end))
        return self._rows.get(ticker, [])


def _settings() -> AlpacaMarketDataSettings:
    return AlpacaMarketDataSettings(
        api_key="data-key",
        secret_key=SecretStr("data-secret"),
        endpoint="https://data.alpaca.markets",  # type: ignore[arg-type]
    )


def _bar(ticker: str, stamp: str) -> IntradayBarRow:
    return IntradayBarRow(
        ticker=ticker,
        bar_ts=datetime.fromisoformat(stamp),
        timeframe=TIMEFRAME_1MIN,
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


# --- chunk_window --------------------------------------------------------------


def test_chunk_window_splits_inclusively_with_a_short_final_chunk() -> None:
    windows = chunk_window("2026-07-01", "2026-07-10", 7)

    # Inclusive bounds: the first chunk ends on the 7th, not the 8th, or the
    # 8th's bars would be fetched twice and upserted twice.
    assert windows == [("2026-07-01", "2026-07-07"), ("2026-07-08", "2026-07-10")]


def test_chunk_window_of_zero_yields_one_window() -> None:
    # The caller asked not to chunk. Substituting a default would silently
    # change the memory profile they were explicitly opting out of.
    assert chunk_window("2026-07-01", "2026-07-31", 0) == [("2026-07-01", "2026-07-31")]


def test_chunk_window_covers_every_day_exactly_once() -> None:
    windows = chunk_window("2026-07-01", "2026-07-31", 7)

    assert windows[0][0] == "2026-07-01"
    assert windows[-1][1] == "2026-07-31"
    for earlier, later in pairwise(windows):
        assert earlier[1] < later[0]


def test_chunk_window_refuses_an_inverted_range() -> None:
    with pytest.raises(ValueError, match="is after"):
        chunk_window("2026-07-31", "2026-07-01", 7)


# --- client --------------------------------------------------------------------


async def test_client_requests_the_intraday_timeframe_and_parses_a_zulu_stamp() -> None:
    http = FakeHttpClient(
        [
            {
                "bars": {
                    "AAPL": [
                        {
                            "t": "2026-07-31T13:31:00Z",
                            "o": 1.0,
                            "h": 2.0,
                            "l": 0.5,
                            "c": 1.5,
                            "v": 100,
                            "n": 7,
                            "vw": 1.4,
                        }
                    ]
                },
                "next_page_token": None,
            }
        ]
    )
    client = AlpacaIntradayBarsClient(_settings())

    rows = await client.get_intraday_bars(http, "aapl", "2026-07-31", "2026-07-31")  # type: ignore[arg-type]

    assert "timeframe=1Min" in http.urls[0]
    assert len(rows) == 1
    # The offset carries which minute this bar belongs to. Slicing it off would
    # reinterpret every bar in whatever the reading process calls local time.
    assert rows[0].bar_ts == datetime(2026, 7, 31, 13, 31, tzinfo=UTC)
    assert rows[0].timeframe == TIMEFRAME_1MIN
    assert rows[0].source == "alpaca-iex"


async def test_client_follows_pagination() -> None:
    def page(stamp: str, token: str | None) -> dict[str, Any]:
        return {
            "bars": {
                "AAPL": [
                    {"t": stamp, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 100},
                ]
            },
            "next_page_token": token,
        }

    http = FakeHttpClient([page("2026-07-31T13:31:00Z", "tok"), page("2026-07-31T13:32:00Z", None)])
    client = AlpacaIntradayBarsClient(_settings())

    rows = await client.get_intraday_bars(http, "AAPL", "2026-07-31", "2026-07-31")  # type: ignore[arg-type]

    assert len(rows) == 2
    assert "page_token=tok" in http.urls[1]


async def test_client_keeps_extended_hours_bars() -> None:
    # 08:00 ET is pre-market. Storing it is the recoverable choice: a consumer
    # filters on bar_ts, but a bar dropped at ingest is gone.
    http = FakeHttpClient(
        [
            {
                "bars": {
                    "AAPL": [
                        {
                            "t": "2026-07-31T12:00:00Z",
                            "o": 1.0,
                            "h": 1.0,
                            "l": 1.0,
                            "c": 1.0,
                            "v": 5,
                        },
                        {
                            "t": "2026-07-31T13:31:00Z",
                            "o": 1.0,
                            "h": 1.0,
                            "l": 1.0,
                            "c": 1.0,
                            "v": 500,
                        },
                    ]
                },
                "next_page_token": None,
            }
        ]
    )
    client = AlpacaIntradayBarsClient(_settings())

    rows = await client.get_intraday_bars(http, "AAPL", "2026-07-31", "2026-07-31")  # type: ignore[arg-type]

    assert len(rows) == 2


# --- store ---------------------------------------------------------------------


async def test_store_upserts_in_one_round_trip() -> None:
    pool = FakePool()
    store = PostgresIntradayBarStore(pool)  # type: ignore[arg-type]
    bars = [_bar("AAPL", f"2026-07-31T13:{minute:02d}:00+00:00") for minute in range(30, 40)]

    count = await store.upsert_bars(bars)

    assert count == 10
    # One executemany, not ten executes. At ~390 rows a ticker-day the round
    # trips would dominate the fetch they persist.
    assert len(pool.conn.many) == 1
    sql, payload = pool.conn.many[0]
    assert sql == UPSERT_INTRADAY_BAR_SQL
    assert len(payload) == 10
    assert not pool.conn.executed


async def test_store_upsert_of_nothing_touches_the_database() -> None:
    pool = FakePool()
    store = PostgresIntradayBarStore(pool)  # type: ignore[arg-type]

    assert await store.upsert_bars([]) == 0
    assert not pool.conn.many


async def test_ensure_schema_creates_the_hypertable_only_when_timescale_is_present() -> None:
    with_ts = FakePool(timescale=True)
    await PostgresIntradayBarStore(with_ts).ensure_schema()  # type: ignore[arg-type]
    assert any(sql == CREATE_INTRADAY_HYPERTABLE_SQL for sql, _ in with_ts.conn.executed)

    # A dev box or a restored dump has no timescaledb extension, and the ingest
    # must still come up rather than failing at ensure_schema.
    without_ts = FakePool(timescale=False)
    await PostgresIntradayBarStore(without_ts).ensure_schema()  # type: ignore[arg-type]
    assert not any(sql == CREATE_INTRADAY_HYPERTABLE_SQL for sql, _ in without_ts.conn.executed)


# --- backfill loop -------------------------------------------------------------


async def test_backfill_is_ticker_major_so_an_interrupted_run_leaves_whole_tickers() -> None:
    client = FakeIntradayClient(
        {
            "AAPL": [_bar("AAPL", "2026-07-01T13:31:00+00:00")],
            "MSFT": [_bar("MSFT", "2026-07-01T13:31:00+00:00")],
        }
    )
    pool = FakePool()
    store = PostgresIntradayBarStore(pool)  # type: ignore[arg-type]

    summary = await backfill_intraday(
        store,
        client,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        ["AAPL", "MSFT"],
        "2026-07-01",
        "2026-07-10",
        dry_run=False,
        chunk_days=7,
        inter_request_delay_seconds=0,
    )

    # Both chunks of AAPL before the first chunk of MSFT: resuming means "start
    # from the ticker that was in flight", not "every ticker is half done".
    assert [ticker for ticker, _, _ in client.calls] == ["AAPL", "AAPL", "MSFT", "MSFT"]
    assert client.calls[0][1:] == ("2026-07-01", "2026-07-07")
    assert client.calls[1][1:] == ("2026-07-08", "2026-07-10")
    assert summary.rows_fetched == 4
    assert summary.rows_upserted == 4


async def test_backfill_dry_run_fetches_and_writes_nothing() -> None:
    client = FakeIntradayClient({"AAPL": [_bar("AAPL", "2026-07-01T13:31:00+00:00")]})
    pool = FakePool()
    store = PostgresIntradayBarStore(pool)  # type: ignore[arg-type]

    summary = await backfill_intraday(
        store,
        client,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        ["AAPL"],
        "2026-07-01",
        "2026-07-01",
        dry_run=True,
        chunk_days=7,
        inter_request_delay_seconds=0,
    )

    assert summary.rows_fetched == 1
    assert summary.rows_upserted == 0
    assert summary.dry_run is True
    assert not pool.conn.many
