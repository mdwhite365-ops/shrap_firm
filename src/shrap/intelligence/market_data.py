"""Alpaca daily-bars market data client and PostgreSQL OHLCV store.

The Regime Classifier's statistical layer needs daily closes for a small
ticker set. This module provides the minimal ingestion path: Alpaca's data
API (IEX feed, free tier) into ``market_data.ohlcv_1d``. It is read-only
toward the broker — no trading endpoints exist on the data host.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from pydantic import BaseModel, Field, HttpUrl, SecretStr, model_validator

from shrap.trading_floor.alpaca import AsyncHttpClient

DATA_HOST = "data.alpaca.markets"


class AlpacaMarketDataSettings(BaseModel):
    """Alpaca data-API credentials; restricted to the data host."""

    api_key: str = Field(default_factory=lambda: os.environ.get("ALPACA_API_KEY", ""))
    secret_key: SecretStr = Field(
        default_factory=lambda: SecretStr(os.environ.get("ALPACA_SECRET_KEY", ""))
    )
    endpoint: HttpUrl = Field(
        default_factory=lambda: HttpUrl(
            os.environ.get("ALPACA_DATA_ENDPOINT", "https://data.alpaca.markets")
        )
    )

    @model_validator(mode="after")
    def _data_host_only(self) -> AlpacaMarketDataSettings:
        if not self.api_key:
            raise ValueError("ALPACA_API_KEY is required")
        if not self.secret_key.get_secret_value():
            raise ValueError("ALPACA_SECRET_KEY is required")
        if self.endpoint.host != DATA_HOST:
            raise ValueError(f"Alpaca data endpoint must be the data host: {DATA_HOST}")
        return self

    def redacted(self) -> dict[str, Any]:
        """Safe-to-log shape, never secret values."""
        return {
            "api_key": "***" if self.api_key else None,
            "secret_key": "***" if self.secret_key.get_secret_value() else None,
            "endpoint": str(self.endpoint),
            "mode": "data-readonly",
        }


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One daily OHLCV bar."""

    symbol: str
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


class AlpacaMarketDataClient:
    """Read-only daily bars from Alpaca's data API (IEX feed)."""

    def __init__(self, settings: AlpacaMarketDataSettings, feed: str = "iex") -> None:
        self._settings = settings
        self._feed = feed

    def _auth_headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._settings.api_key,
            "APCA-API-SECRET-KEY": self._settings.secret_key.get_secret_value(),
        }

    def _base(self) -> str:
        return str(self._settings.endpoint).rstrip("/")

    async def get_daily_bars(
        self,
        http_client: AsyncHttpClient,
        symbols: list[str],
        start_day: str,
        limit: int = 10000,
    ) -> list[DailyBar]:
        """Fetch daily bars for ``symbols`` from ``start_day`` (YYYY-MM-DD), inclusive.

        Follows next_page_token pagination until exhausted.
        """

        bars: list[DailyBar] = []
        page_token: str | None = None
        symbol_param = ",".join(sorted({s.strip().upper() for s in symbols if s.strip()}))
        if not symbol_param:
            return bars
        while True:
            url = (
                f"{self._base()}/v2/stocks/bars"
                f"?symbols={symbol_param}&timeframe=1Day&start={start_day}"
                f"&limit={limit}&adjustment=split&feed={self._feed}&sort=asc"
            )
            if page_token:
                url += f"&page_token={page_token}"
            response = await http_client.get(url, headers=self._auth_headers())
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("Alpaca bars response must be a JSON object")
            raw_bars = body.get("bars") or {}
            if not isinstance(raw_bars, dict):
                raise ValueError("Alpaca bars response 'bars' must be an object")
            for symbol, entries in raw_bars.items():
                if not isinstance(entries, list):
                    raise ValueError(f"Alpaca bars for {symbol} must be an array")
                for entry in entries:
                    bars.append(_parse_bar(str(symbol), entry))
            token = body.get("next_page_token")
            if not token:
                return bars
            page_token = str(token)


def _parse_bar(symbol: str, entry: object) -> DailyBar:
    if not isinstance(entry, dict):
        raise ValueError(f"Alpaca bar entry for {symbol} must be an object")
    timestamp = str(entry.get("t", ""))
    if len(timestamp) < 10:
        raise ValueError(f"Alpaca bar entry for {symbol} lacks a timestamp")
    return DailyBar(
        symbol=symbol.upper(),
        day=date.fromisoformat(timestamp[:10]),
        open=float(entry["o"]),
        high=float(entry["h"]),
        low=float(entry["l"]),
        close=float(entry["c"]),
        volume=float(entry["v"]),
    )


CREATE_MARKET_DATA_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS market_data"

CREATE_OHLCV_1D_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS market_data.ohlcv_1d (
    symbol TEXT NOT NULL,
    day DATE NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, day)
)
""".strip()

UPSERT_OHLCV_1D_SQL = """
INSERT INTO market_data.ohlcv_1d (symbol, day, open, high, low, close, volume)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (symbol, day) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume,
    updated_at = now()
""".strip()

SELECT_CLOSES_SQL = """
SELECT day, close
FROM market_data.ohlcv_1d
WHERE symbol = $1
ORDER BY day DESC
LIMIT $2
""".strip()

SELECT_LATEST_DAY_SQL = """
SELECT max(day) AS latest_day FROM market_data.ohlcv_1d WHERE symbol = $1
""".strip()


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetch(self, sql: str, *args: object) -> list[Any]: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


class PostgresOhlcvStore:
    """Upsert and read daily bars in market_data.ohlcv_1d."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_MARKET_DATA_SCHEMA_SQL)
            await conn.execute(CREATE_OHLCV_1D_TABLE_SQL)

    async def upsert_bars(self, bars: list[DailyBar]) -> int:
        async with self._pool.acquire() as conn:
            for bar in bars:
                await conn.execute(
                    UPSERT_OHLCV_1D_SQL,
                    bar.symbol,
                    bar.day,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                )
        return len(bars)

    async def closes(self, symbol: str, limit: int = 260) -> list[float]:
        """Return up to ``limit`` daily closes for ``symbol``, oldest first."""

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_CLOSES_SQL, symbol.upper(), limit)
        return [float(row["close"]) for row in reversed(rows)]

    async def latest_day(self, symbol: str) -> date | None:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_LATEST_DAY_SQL, symbol.upper())
        if not rows:
            return None
        value = rows[0]["latest_day"]
        return value if isinstance(value, date) or value is None else None
