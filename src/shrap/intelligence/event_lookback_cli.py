"""``shrap-event-lookback`` — the biggest moves in the firm's history, and what it knew.

    shrap-event-lookback                      # 2021-01-01 to yesterday, to stdout
    shrap-event-lookback --start 2024-01-01 --top 50 --out lookback.md

ADR-0018 card 1. **Read-only**: it selects from ``market_data.daily_bars``,
``intelligence.news_items`` and ``intelligence.filings`` and writes to none of
them. Run it where the database is::

    docker compose run --rm strategy-evaluator shrap-event-lookback

The cause attached to a move is the category the News Analyzer or the Filing
Processor already recorded on an item, read back. No model is called, so the
run costs no Ollama quota (#261) and can be repeated freely.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from shrap.common.db import create_asyncpg_pool
from shrap.intelligence.event_lookback import (
    CATEGORY_UNSCORED,
    KIND_FILING,
    KIND_NEWS,
    MARKET_TZ,
    Close,
    Evidence,
    attribute,
    find_moves,
    render_report,
)
from shrap.market_data.store import DEFAULT_BAR_SOURCE
from shrap.research.strategy_evaluator.store import PostgresEvaluatorReader
from shrap.research.universe_curator.launch_list import LAUNCH_LIST

ADJUSTMENT = "all"
DEFAULT_START = date(2021, 1, 1)
DEFAULT_TOP = 40
# Form 8-K item 2.02, "Results of Operations and Financial Condition". A filing
# carrying it is an earnings release whether or not it has been scored yet.
ITEM_RESULTS = "2.02"

# Named columns, never `SELECT *`: `payload` and `full_text` are wide, and a
# listing that silently truncates is a finding nobody made (#260).
SELECT_NEWS_SQL = """
SELECT published_at, headline, symbols, verdict
FROM intelligence.news_items
WHERE published_at > $1 AND published_at <= $2 AND symbols ?| $3::text[]
ORDER BY published_at
""".strip()

SELECT_FILINGS_SQL = """
SELECT filing_date, symbol, title, item_codes, verdicts
FROM intelligence.filings
WHERE filing_date > $1 AND filing_date <= $2 AND symbol = ANY($3::text[])
ORDER BY filing_date
""".strip()

SELECT_NEWS_START_SQL = "SELECT min(published_at) AS started FROM intelligence.news_items"
SELECT_FILINGS_START_SQL = "SELECT min(filing_date) AS started FROM intelligence.filings"


def _json(raw: Any) -> Any:
    """asyncpg returns JSONB as text unless a codec is registered."""

    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return raw


def _materiality(raw: Any) -> int:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else -1


def news_evidence(row: Mapping[str, Any], universe: frozenset[str]) -> list[Evidence]:
    """One :class:`Evidence` per universe ticker a news item names.

    An item not yet scored is kept, marked ``unscored``: it is real evidence
    that something was published, and it cannot name a cause.
    """

    at = row["published_at"]
    if at is None:
        return []
    symbols = _json(row["symbols"])
    verdict = _json(row["verdict"])
    scored = isinstance(verdict, dict)
    category = str(verdict.get("category") or "other") if scored else CATEGORY_UNSCORED
    materiality = _materiality(verdict.get("materiality")) if scored else -1
    tickers = [s for s in symbols if isinstance(s, str)] if isinstance(symbols, list) else []
    return [
        Evidence(KIND_NEWS, ticker, at, category, materiality, str(row["headline"]))
        for ticker in tickers
        if ticker in universe
    ]


def filing_evidence(row: Mapping[str, Any]) -> list[Evidence]:
    """A filing as evidence, under its most material verdict.

    Unscored, an 8-K with item 2.02 is still ``results``: the form says so.
    """

    at = row["filing_date"]
    if at is None:
        return []
    verdicts = _json(row["verdicts"])
    scored = [v for v in verdicts if isinstance(v, dict)] if isinstance(verdicts, list) else []
    codes = _json(row["item_codes"])
    codes = [str(c) for c in codes] if isinstance(codes, list) else []
    if scored:
        best = max(scored, key=lambda v: _materiality(v.get("materiality")))
        category = str(best.get("category") or "other")
        materiality = _materiality(best.get("materiality"))
    elif ITEM_RESULTS in codes:
        category, materiality = "results", -1
    else:
        category, materiality = CATEGORY_UNSCORED, -1
    title = str(row["title"] or "filing")
    text = f"{title} (items {', '.join(codes)})" if codes else title
    return [Evidence(KIND_FILING, str(row["symbol"]), at, category, materiality, text)]


def attribution_start(starts: Mapping[str, datetime | None]) -> datetime | None:
    """The date from which a move with nothing on file means something.

    The *latest* start, so every source was recording. From the earliest, a
    move in the gap would be called unexplained when half the corpus could not
    yet have explained it. ``None`` if any source holds nothing at all.
    """

    values = list(starts.values())
    if not values or any(v is None for v in values):
        return None
    return max(v for v in values if v is not None)


def _dsn() -> str:
    return (
        os.environ.get("EVENT_LOOKBACK_POSTGRES_DSN")
        or os.environ.get("STRATEGY_EVALUATOR_POSTGRES_DSN")
        or "postgresql://shrap:shrap@postgres:5432/shrap"
    )


async def _run(args: argparse.Namespace) -> str:
    universe = [name.ticker for name in LAUNCH_LIST]
    pool = await create_asyncpg_pool(args.dsn)
    try:
        reader = PostgresEvaluatorReader(pool, source=args.source)
        closes: dict[str, list[Close]] = {}
        for ticker in universe:
            rows = await reader.read_bars(ticker, args.start, args.end, ADJUSTMENT)
            if rows:
                closes[ticker] = [Close(r.session_date, r.close) for r in rows]
        if not closes:
            return (
                f"no bars for {args.start}..{args.end} from source {args.source!r}; "
                "nothing to look back over"
            )
        moves, suspect = find_moves(closes, z_min=args.z, abs_min=args.min_move)

        async with pool.acquire() as conn:
            news_start = await conn.fetchval(SELECT_NEWS_START_SQL)
            filings_start = await conn.fetchval(SELECT_FILINGS_START_SQL)
        starts = {"news": news_start, "filings": filings_start}
        corpus_start = attribution_start(starts)

        evidence: list[Evidence] = []
        if corpus_start is not None and moves:
            window_end = max(m.window[1] for m in moves)
            async with pool.acquire() as conn:
                news = await conn.fetch(SELECT_NEWS_SQL, corpus_start, window_end, universe)
                filings = await conn.fetch(SELECT_FILINGS_SQL, corpus_start, window_end, universe)
            known = frozenset(universe)
            for row in news:
                evidence.extend(news_evidence(row, known))
            for row in filings:
                evidence.extend(filing_evidence(row))

        attributions = attribute(
            moves, evidence, corpus_start=corpus_start, breadth_min=args.breadth
        )
        sessions = sorted({c.session_date for series in closes.values() for c in series})
        return render_report(
            attributions,
            suspect,
            first_session=sessions[0],
            last_session=sessions[-1],
            corpus_start=corpus_start,
            tickers=len(closes),
            top=args.top,
            source_starts=starts,
        )
    finally:
        await pool.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "The largest one-day moves in the launch list's history, and the news "
            "and filings the firm had on file before each. Read-only; calls no "
            "model. ADR-0018 card 1."
        )
    )
    parser.add_argument("--dsn", default=_dsn(), help="Postgres DSN")
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="Last session to include (default: yesterday in New York, so no partial bar)",
    )
    parser.add_argument("--z", type=float, default=3.0, help="Sigmas of the name's own vol")
    parser.add_argument("--min-move", type=float, default=0.02, help="Smallest absolute return")
    parser.add_argument(
        "--breadth", type=int, default=8, help="Names moving together that make a market day"
    )
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help="Largest moves to list")
    parser.add_argument("--source", default=DEFAULT_BAR_SOURCE, help="Bar feed")
    parser.add_argument("--out", type=Path, default=None, help="Write the report here")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.end is None:
        # `daily_bars` holds a partial bar for the session in progress (#279).
        args.end = datetime.now(MARKET_TZ).date() - timedelta(days=1)
    report = asyncio.run(_run(args))
    if args.out is not None:
        args.out.write_text(report + "\n")
        print(f"wrote {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()


__all__ = [
    "attribution_start",
    "filing_evidence",
    "main",
    "news_evidence",
]
