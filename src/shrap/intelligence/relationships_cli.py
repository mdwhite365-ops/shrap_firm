"""``shrap-relationships`` — how reliably each name moves with a driver.

    shrap-relationships                           # XLE, TLT, UUP, GLD, IBIT vs the launch list
    shrap-relationships --drivers XLE,GLD --window 126 --out links.md

ADR-0018 card 8. **Read-only, no model.** It selects from
``market_data.daily_bars`` and writes nothing. Run it where the database is::

    docker compose run --rm strategy-evaluator shrap-relationships
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from shrap.common.db import create_asyncpg_pool
from shrap.intelligence.relationships import (
    DEFAULT_WINDOW,
    Link,
    measure,
    render,
    returns_from_closes,
)
from shrap.market_data.store import DEFAULT_BAR_SOURCE
from shrap.research.strategy_evaluator.store import PostgresEvaluatorReader
from shrap.research.universe_curator.launch_list import LAUNCH_LIST

MARKET_TZ = ZoneInfo("America/New_York")
ADJUSTMENT = "all"
DEFAULT_START = date(2021, 1, 1)
DEFAULT_MARKET = "SPY"
DEFAULT_DRIVERS = "XLE,TLT,UUP,GLD,IBIT"


def _dsn() -> str:
    return (
        os.environ.get("RELATIONSHIPS_POSTGRES_DSN")
        or os.environ.get("STRATEGY_EVALUATOR_POSTGRES_DSN")
        or "postgresql://shrap:shrap@postgres:5432/shrap"
    )


def parse_drivers(text: str) -> list[str]:
    return [t.strip().upper() for t in text.split(",") if t.strip()]


async def _run(args: argparse.Namespace) -> str:
    universe = [name.ticker for name in LAUNCH_LIST]
    drivers = parse_drivers(args.drivers)
    wanted = sorted({*universe, *drivers, args.market})
    pool = await create_asyncpg_pool(args.dsn)
    try:
        reader = PostgresEvaluatorReader(pool, source=args.source)
        returns: dict[str, dict[date, float]] = {}
        for ticker in wanted:
            rows = await reader.read_bars(ticker, args.start, args.end, ADJUSTMENT)
            if rows:
                returns[ticker] = returns_from_closes([(r.session_date, r.close) for r in rows])
    finally:
        await pool.close()
    missing = [d for d in [*drivers, args.market] if d not in returns]
    if missing:
        return (
            f"no bars for {', '.join(missing)} from source {args.source!r}. "
            "A driver the firm does not ingest cannot be measured; nothing computed."
        )
    market = returns[args.market]
    links: list[Link] = []
    for driver in drivers:
        for name in universe:
            if name in (driver, args.market) or name not in returns:
                continue
            links.append(
                measure(
                    driver,
                    name,
                    returns[driver],
                    returns[name],
                    None if driver == args.market else market,
                    window=args.window,
                )
            )
    return render(links, market=args.market, window=args.window)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "How each launch-list name's daily return goes with a driver's, with "
            "the market removed, and how often that holds window to window. "
            "Read-only. ADR-0018 card 8."
        )
    )
    parser.add_argument("--dsn", default=_dsn(), help="Postgres DSN")
    parser.add_argument("--drivers", default=DEFAULT_DRIVERS, help="Comma-separated tickers")
    parser.add_argument("--market", default=DEFAULT_MARKET, help="Removed from both sides")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW, help="Sessions per window")
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="Last session (default: yesterday in New York, so no partial bar)",
    )
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


__all__ = ["main", "parse_drivers"]
