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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol, cast

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

SELECT_LAST_SESSION_BY_TICKER_SQL = """
SELECT ticker, max(session_date) AS last_session
FROM market_data.daily_bars
GROUP BY ticker
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

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]: ...

    async def executemany(self, sql: str, args: Sequence[Sequence[object]]) -> object: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


# --- intraday grain -----------------------------------------------------------
#
# A SEPARATE TABLE rather than a `timeframe` column on `daily_bars`, for two
# reasons that both bite.
#
# Volume. Fifty names at 390 regular-session minutes is ~19,500 rows per
# trading day against daily's 50 — roughly 4.9M rows a year, two orders of
# magnitude more. Every existing query against `daily_bars` (the Evaluator's
# five-year panel read, above all) would pay for that in planning and index
# size while wanting none of it.
#
# Grain. Daily's key is `session_date DATE`; intraday needs `TIMESTAMPTZ`, and
# a nullable time column on a shared table would make "the daily bar" and "the
# 09:31 bar" indistinguishable to anything that forgot to filter.
#
# `timeframe` is still part of the key here so 1Min and 5Min can coexist
# without a third table.
CREATE_INTRADAY_BARS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS market_data.intraday_bars (
    ticker TEXT NOT NULL,
    bar_ts TIMESTAMPTZ NOT NULL,
    timeframe TEXT NOT NULL,
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
    PRIMARY KEY (ticker, bar_ts, timeframe, adjustment)
)
""".strip()

# TimescaleDB is deployed (ADR-0004) but nothing has used a hypertable until
# now, because nothing until now wrote at this rate. Guarded on the extension
# actually being present: the ingest must work on a plain Postgres — a dev box,
# a restored dump — rather than failing at ensure_schema on a missing extension.
# Chunked weekly: a week is ~97k rows per fifty names, small enough to stay in
# cache for the Runner's trailing-window reads.
SELECT_TIMESCALE_PRESENT_SQL = "SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'"

CREATE_INTRADAY_HYPERTABLE_SQL = """
SELECT create_hypertable(
    'market_data.intraday_bars', 'bar_ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
)
""".strip()

SELECT_LAST_BAR_BY_TICKER_SQL = """
SELECT ticker, max(bar_ts) AS last_bar
FROM market_data.intraday_bars
WHERE timeframe = $1
GROUP BY ticker
""".strip()

UPSERT_INTRADAY_BAR_SQL = """
INSERT INTO market_data.intraday_bars (
    ticker, bar_ts, timeframe, open, high, low, close, volume,
    trade_count, vwap, adjustment, source, fetched_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, now())
ON CONFLICT (ticker, bar_ts, timeframe, adjustment) DO UPDATE SET
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
class IntradayBarRow:
    """One intraday OHLCV bar bound for ``market_data.intraday_bars``."""

    ticker: str
    bar_ts: datetime
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int | None
    vwap: float | None
    adjustment: str
    source: str


class PostgresIntradayBarStore:
    """Idempotent upsert sink for ``market_data.intraday_bars``."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_MARKET_DATA_SCHEMA_SQL)
            await conn.execute(CREATE_INTRADAY_BARS_TABLE_SQL)
            if await conn.fetch(SELECT_TIMESCALE_PRESENT_SQL):
                await conn.execute(CREATE_INTRADAY_HYPERTABLE_SQL)

    async def last_bar_by_ticker(self, timeframe: str) -> dict[str, datetime]:
        """Newest stored ``bar_ts`` per ticker at ``timeframe``.

        Scoped to one timeframe because the maximum across mixed grains is
        meaningless: a ticker with 5Min history and no 1Min would report a
        recent watermark and then be asked for nothing at the grain it lacks.
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_LAST_BAR_BY_TICKER_SQL, timeframe)
        return {str(row["ticker"]): cast(datetime, row["last_bar"]) for row in rows}

    async def upsert_bars(self, bars: Sequence[IntradayBarRow]) -> int:
        """Upsert ``bars`` in one round trip; returns the count handed in.

        ``executemany`` rather than the daily store's per-row loop, and the
        difference is not stylistic: one ticker-day of 1Min bars is ~390 rows
        and a full universe backfill is millions. At one round trip per row the
        network cost alone would dominate the fetch it is meant to persist.
        """

        if not bars:
            return 0
        payload = [
            (
                bar.ticker,
                bar.bar_ts,
                bar.timeframe,
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
            for bar in bars
        ]
        async with self._pool.acquire() as conn:
            await conn.executemany(UPSERT_INTRADAY_BAR_SQL, payload)
        return len(bars)


class PostgresDailyBarStore:
    """Idempotent upsert sink for ``market_data.daily_bars``."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_MARKET_DATA_SCHEMA_SQL)
            await conn.execute(CREATE_DAILY_BARS_TABLE_SQL)

    async def last_session_by_ticker(self) -> dict[str, date]:
        """Newest stored ``session_date`` per ticker.

        This is what lets an incremental sweep ask for the days it is missing
        instead of re-requesting five years on every pass. Per-ticker rather
        than a single global maximum on purpose: a name added to the universe
        later has no history, and a global maximum would quietly start its
        series at today and leave it permanently short — a gap that would only
        surface as an inexplicably thin backtest months later.
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_LAST_SESSION_BY_TICKER_SQL)
        return {str(row["ticker"]): cast(date, row["last_session"]) for row in rows}

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
    "CREATE_INTRADAY_BARS_TABLE_SQL",
    "CREATE_INTRADAY_HYPERTABLE_SQL",
    "CREATE_MARKET_DATA_SCHEMA_SQL",
    "SELECT_TIMESCALE_PRESENT_SQL",
    "UPSERT_DAILY_BAR_SQL",
    "UPSERT_INTRADAY_BAR_SQL",
    "AsyncPool",
    "DailyBarRow",
    "IntradayBarRow",
    "PostgresDailyBarStore",
    "PostgresIntradayBarStore",
]
