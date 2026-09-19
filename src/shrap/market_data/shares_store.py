"""PostgreSQL store for ``market_data.shares_outstanding``.

Append-oriented rather than current-value: every reported count is kept with the
date it describes (``as_of``) and the date it became public (``filed_at``), so a
backtest can ask what the firm could have known on a given day. See
:mod:`shrap.market_data.shares` for why conflating those two dates is look-ahead
bias rather than a rounding detail.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Protocol

from shrap.market_data.shares import SharesRow

CREATE_MARKET_DATA_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS market_data"

# PRIMARY KEY includes filed_at so an amendment is a new row rather than an
# overwrite. A backtest replaying 2024 must see what was believed in 2024,
# including figures later restated — overwriting would rewrite history in the
# firm's favour and no test downstream would notice.
CREATE_SHARES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS market_data.shares_outstanding (
    ticker TEXT NOT NULL,
    cik TEXT NOT NULL,
    as_of DATE NOT NULL,
    filed_at DATE NOT NULL,
    shares DOUBLE PRECISION NOT NULL,
    unit TEXT NOT NULL,
    form TEXT,
    source TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, as_of, filed_at)
)
""".strip()

# The index the point-in-time lookup actually uses: filed_at first, because that
# is the column the no-peek predicate filters on.
CREATE_SHARES_POINT_IN_TIME_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS shares_outstanding_pit_idx
ON market_data.shares_outstanding (ticker, filed_at DESC, as_of DESC)
""".strip()

UPSERT_SHARES_SQL = """
INSERT INTO market_data.shares_outstanding (
    ticker, cik, as_of, filed_at, shares, unit, form, source, fetched_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
ON CONFLICT (ticker, as_of, filed_at) DO UPDATE SET
    shares = EXCLUDED.shares,
    unit = EXCLUDED.unit,
    form = EXCLUDED.form,
    source = EXCLUDED.source,
    fetched_at = now()
""".strip()

# **filed_at <= $2, never as_of <= $2.** This one predicate is the difference
# between an honest backtest and a look-ahead one: a count describing 2024-03-31
# is often not public until May, and selecting it for an April date uses
# information nobody had. DISTINCT ON takes the newest period the firm could see,
# breaking ties on the later filing, which is the amendment.
SELECT_SHARES_AS_OF_SQL = """
SELECT DISTINCT ON (ticker)
    ticker, cik, as_of, filed_at, shares, unit, form, source
FROM market_data.shares_outstanding
WHERE ticker = $1 AND filed_at <= $2
ORDER BY ticker, as_of DESC, filed_at DESC
""".strip()

SELECT_LATEST_FILED_BY_TICKER_SQL = """
SELECT ticker, max(filed_at) AS last_filed
FROM market_data.shares_outstanding
GROUP BY ticker
""".strip()

# Market cap on a date, joined against the price the firm already stores. The
# LATERAL is what applies the point-in-time rule per row rather than once.
SELECT_MARKET_CAP_SQL = """
SELECT b.ticker, b.session_date, b.close, s.shares, b.close * s.shares AS market_cap
FROM market_data.daily_bars b
CROSS JOIN LATERAL (
    SELECT shares
    FROM market_data.shares_outstanding o
    WHERE o.ticker = b.ticker AND o.filed_at <= b.session_date
    ORDER BY o.as_of DESC, o.filed_at DESC
    LIMIT 1
) s
WHERE b.ticker = ANY($1::text[])
  AND b.adjustment = $2
  AND b.session_date BETWEEN $3 AND $4
  AND b.source = $5
ORDER BY b.ticker, b.session_date
""".strip()


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


class PostgresSharesStore:
    """Idempotent sink and point-in-time reader for share counts."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_MARKET_DATA_SCHEMA_SQL)
            await conn.execute(CREATE_SHARES_TABLE_SQL)
            await conn.execute(CREATE_SHARES_POINT_IN_TIME_INDEX_SQL)

    async def upsert_rows(self, rows: Sequence[SharesRow]) -> int:
        if not rows:
            return 0
        async with self._pool.acquire() as conn:
            for row in rows:
                await conn.execute(
                    UPSERT_SHARES_SQL,
                    row.ticker,
                    row.cik,
                    row.as_of,
                    row.filed_at,
                    row.shares,
                    row.unit,
                    row.form,
                    row.source,
                )
        return len(rows)

    async def shares_as_of(self, ticker: str, on: date) -> float | None:
        """The share count public on ``on``, or ``None`` if nothing was filed yet.

        ``None`` is not zero. A caller that substitutes zero produces a market
        cap of zero, which is a rankable number and will sort the name to the
        bottom of a size screen rather than excluding it.
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_SHARES_AS_OF_SQL, ticker.strip().upper(), on)
        if not rows:
            return None
        return float(rows[0]["shares"])

    async def latest_filed_by_ticker(self) -> dict[str, date]:
        """Newest filing date per ticker, so a backfill can resume."""

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_LATEST_FILED_BY_TICKER_SQL)
        return {str(row["ticker"]): row["last_filed"] for row in rows}


__all__ = [
    "CREATE_MARKET_DATA_SCHEMA_SQL",
    "CREATE_SHARES_POINT_IN_TIME_INDEX_SQL",
    "CREATE_SHARES_TABLE_SQL",
    "SELECT_LATEST_FILED_BY_TICKER_SQL",
    "SELECT_MARKET_CAP_SQL",
    "SELECT_SHARES_AS_OF_SQL",
    "UPSERT_SHARES_SQL",
    "PostgresSharesStore",
]
