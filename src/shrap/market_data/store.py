"""PostgreSQL store for historical daily OHLCV bars (``market_data.daily_bars``).

The Strategy Evaluator backtests against ``market_data.*`` historical OHLCV
(spec §Inputs). This is the daily grain of that store: one row per
``(ticker, session_date, adjustment)``, upserted so re-running the backfill is
idempotent. Schema and table creation follow the house ensure-schema pattern
(``CREATE ... IF NOT EXISTS``, run at startup — see the Tech Watcher and Filing
Processor stores).

Provenance is a first-class column. ``source`` records the feed the row came
from (``alpaca-iex``) and ``adjustment`` records the price-adjustment mode
(``all`` — splits and dividends). Both are part of the primary key intent: the
same ticker/date can coexist under different adjustment modes if a future card
ever backfills SIP or a different adjustment, and the Evaluator can then select
the mode it wants without ambiguity.

**IEX, not SIP (recorded project fact).** ``alpaca-iex`` volumes are a fraction
of national volume; volatility derived from them reads above the SIP tape. See
the package docstring and ``docs/infrastructure/market-data.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

CREATE_MARKET_DATA_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS market_data"

# Distinct from the Regime Classifier's rolling ``market_data.ohlcv_1d`` window:
# this is the durable, provenance-tracked backtest table. ``trade_count`` /
# ``vwap`` are nullable — the IEX bars usually carry them, but not every bar is
# guaranteed to, and the Evaluator only requires OHLCV.
CREATE_DAILY_BARS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS market_data.daily_bars (
    ticker TEXT NOT NULL,
    session_date DATE NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    trade_count BIGINT,
    vwap DOUBLE PRECISION,
    adjustment TEXT NOT NULL,
    source TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, session_date, adjustment)
)
""".strip()

UPSERT_DAILY_BAR_SQL = """
INSERT INTO market_data.daily_bars (
    ticker, session_date, open, high, low, close, volume,
    trade_count, vwap, adjustment, source, fetched_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, now())
ON CONFLICT (ticker, session_date, adjustment) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume,
    trade_count = EXCLUDED.trade_count,
    vwap = EXCLUDED.vwap,
    source = EXCLUDED.source,
    fetched_at = now()
""".strip()


@dataclass(frozen=True, slots=True)
class DailyBarRow:
    """One daily OHLCV bar bound for ``market_data.daily_bars``."""

    ticker: str
    session_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int | None
    vwap: float | None
    adjustment: str
    source: str


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


class PostgresDailyBarStore:
    """Idempotent upsert sink for ``market_data.daily_bars``."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_MARKET_DATA_SCHEMA_SQL)
            await conn.execute(CREATE_DAILY_BARS_TABLE_SQL)

    async def upsert_bars(self, bars: Sequence[DailyBarRow]) -> int:
        """Upsert ``bars`` one row at a time; returns the count handed in.

        Re-runs are idempotent: a repeated ``(ticker, session_date, adjustment)``
        overwrites the prior OHLCV and refreshes ``fetched_at`` rather than
        inserting a duplicate.
        """

        async with self._pool.acquire() as conn:
            for bar in bars:
                await conn.execute(
                    UPSERT_DAILY_BAR_SQL,
                    bar.ticker,
                    bar.session_date,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                    bar.trade_count,
                    bar.vwap,
                    bar.adjustment,
                    bar.source,
                )
        return len(bars)


__all__ = [
    "CREATE_DAILY_BARS_TABLE_SQL",
    "CREATE_MARKET_DATA_SCHEMA_SQL",
    "UPSERT_DAILY_BAR_SQL",
    "AsyncPool",
    "DailyBarRow",
    "PostgresDailyBarStore",
]
