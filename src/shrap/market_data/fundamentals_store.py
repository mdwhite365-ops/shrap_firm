"""``market_data.fundamentals``: every filed figure, kept, read point in time.

Same discipline as ``shares_store``: the primary key includes the accession, so
a figure repeated or restated in a later filing is another row rather than an
overwrite, and the reader hands back the raw history for the panel to apply the
``filed_at`` rule against its own dates.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from shrap.market_data.fundamentals import FundamentalRow, Observation
from shrap.market_data.shares_store import CREATE_MARKET_DATA_SCHEMA_SQL

CREATE_FUNDAMENTALS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS market_data.fundamentals (
    ticker TEXT NOT NULL,
    cik TEXT NOT NULL,
    metric TEXT NOT NULL,
    concept TEXT NOT NULL,
    period_start DATE,
    period_end DATE NOT NULL,
    filed_at DATE NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    form TEXT,
    accession TEXT NOT NULL,
    source TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, metric, period_end, accession)
)
""".strip()

CREATE_FUNDAMENTALS_PIT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS fundamentals_pit_idx
ON market_data.fundamentals (ticker, metric, filed_at)
""".strip()

UPSERT_FUNDAMENTAL_SQL = """
INSERT INTO market_data.fundamentals (
    ticker, cik, metric, concept, period_start, period_end, filed_at, value,
    form, accession, source, fetched_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, now())
ON CONFLICT (ticker, metric, period_end, accession) DO UPDATE SET
    concept = EXCLUDED.concept,
    period_start = EXCLUDED.period_start,
    filed_at = EXCLUDED.filed_at,
    value = EXCLUDED.value,
    form = EXCLUDED.form,
    source = EXCLUDED.source,
    fetched_at = now()
""".strip()

SELECT_FUNDAMENTALS_HISTORY_SQL = """
SELECT ticker, metric, filed_at, period_end, value
FROM market_data.fundamentals
WHERE ticker = ANY($1::text[])
ORDER BY ticker, metric, filed_at, period_end
""".strip()

FundamentalsHistory = dict[str, dict[str, list[Observation]]]
"""``{ticker: {metric: [(filed_at, period_end, value), ...]}}``, oldest filing first."""


class PostgresFundamentalsStore:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_MARKET_DATA_SCHEMA_SQL)
            await conn.execute(CREATE_FUNDAMENTALS_TABLE_SQL)
            await conn.execute(CREATE_FUNDAMENTALS_PIT_INDEX_SQL)

    async def upsert_rows(self, rows: Sequence[FundamentalRow]) -> int:
        if not rows:
            return 0
        async with self._pool.acquire() as conn:
            for r in rows:
                await conn.execute(
                    UPSERT_FUNDAMENTAL_SQL,
                    r.ticker,
                    r.cik,
                    r.metric,
                    r.concept,
                    r.period_start,
                    r.period_end,
                    r.filed_at,
                    r.value,
                    r.form,
                    r.accession,
                    r.source,
                )
        return len(rows)

    async def history(self, tickers: Sequence[str]) -> FundamentalsHistory:
        """Every filed figure for ``tickers``. Absent means unknown, never zero.

        A missing table reads as no history, because this is an optional input
        to a panel: a firm that has not run the backfill yet should still be
        able to evaluate strategies that do not use fundamentals.
        """

        wanted = [t.strip().upper() for t in tickers if t and t.strip()]
        if not wanted:
            return {}
        async with self._pool.acquire() as conn:
            exists = await conn.fetchval("SELECT to_regclass('market_data.fundamentals')")
            if exists is None:
                return {}
            rows = await conn.fetch(SELECT_FUNDAMENTALS_HISTORY_SQL, wanted)
        out: FundamentalsHistory = {}
        for row in rows:
            out.setdefault(str(row["ticker"]), {}).setdefault(str(row["metric"]), []).append(
                (row["filed_at"], row["period_end"], float(row["value"]))
            )
        return out


__all__ = [
    "CREATE_FUNDAMENTALS_TABLE_SQL",
    "SELECT_FUNDAMENTALS_HISTORY_SQL",
    "UPSERT_FUNDAMENTAL_SQL",
    "FundamentalsHistory",
    "PostgresFundamentalsStore",
]
