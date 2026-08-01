"""Backfill orchestration and CLI for ``market_data.intraday_bars``.

The console script ``shrap-market-data-intraday-backfill`` is the second price
path required by ADR-0016's intraday equities work (timeline 2.8).
``market_data.daily_bars`` cannot express a fast loop at any parameterisation:
one bar a day is one decision a day, and no amount of re-parameterising a daily
series produces an intraday one.

Deliberately a sibling module rather than a flag on
:mod:`shrap.market_data.backfill`. The two share argument resolution — tickers
and window are the same question at both grains, and are imported rather than
re-implemented — but differ in the things that matter operationally: the row
counts, the memory profile, and the default lookback. Folding them together
would mean one `--timeframe` flag silently changing a tool's resource
behaviour by four orders of magnitude.

**The arithmetic that shapes this module.** One regular session is 390 minutes,
so one ticker-day of 1Min bars is ~390 rows against daily's 1. Fifty names for
thirty days is ~410k rows; the same fifty names for the Evaluator's five-year
window would be ~24.6M. That is why:

- ``_DEFAULT_LOOKBACK_DAYS`` is 30, not the daily backfill's ~5 years. A caller
  who wants more says so, having been told what it costs.
- The window is walked in chunks (``--chunk-days``) and upserted per chunk
  rather than accumulated. A five-year single-ticker fetch held entirely in
  memory before its first write is tens of megabytes of Python objects and an
  all-or-nothing failure mode; chunking bounds both and makes a long run
  resumable by re-running with a later ``--since``.

**Extended hours are ingested.** See :class:`AlpacaIntradayBarsClient` — Alpaca
returns bars across the extended session and this backfill stores them. A
consumer wanting regular hours filters on ``bar_ts``; a consumer that forgets is
trading four-in-the-morning IEX prints, which is why the fact is documented at
both layers rather than one.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import cast

import httpx
import structlog

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.intelligence.market_data import AlpacaMarketDataSettings
from shrap.market_data.backfill import BackfillSummary, resolve_tickers, resolve_window
from shrap.market_data.client import TIMEFRAME_1MIN, AlpacaIntradayBarsClient
from shrap.market_data.config import Settings
from shrap.market_data.store import IntradayBarRow, PostgresIntradayBarStore
from shrap.trading_floor.alpaca import AsyncHttpClient

log = structlog.get_logger(__name__)

# Thirty days of 1Min bars is ~410k rows across fifty names — enough to develop
# and evaluate an intraday rule, small enough to fetch in one sitting. The daily
# backfill's five-year default would be ~24.6M rows and hours of paging.
_DEFAULT_LOOKBACK_DAYS = 30

# One week per fetch/upsert cycle: ~2,700 rows per ticker, a bounded working set
# and a visible progress line per chunk.
_DEFAULT_CHUNK_DAYS = 7


def chunk_window(start_day: str, end_day: str, chunk_days: int) -> list[tuple[str, str]]:
    """Split ``[start_day, end_day]`` into inclusive sub-windows of ``chunk_days``.

    Returns whole-day bounds as ISO strings. A ``chunk_days`` of zero or less
    yields a single window — the caller asked not to chunk, and silently
    substituting a default would hide that from the log line.
    """

    start = date.fromisoformat(start_day)
    end = date.fromisoformat(end_day)
    if start > end:
        raise ValueError(f"start {start_day} is after end {end_day}")
    if chunk_days <= 0:
        return [(start.isoformat(), end.isoformat())]
    windows: list[tuple[str, str]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
        windows.append((cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end + timedelta(days=1)
    return windows


def _span(rows: list[IntradayBarRow]) -> tuple[str | None, str | None]:
    if not rows:
        return None, None
    stamps = [row.bar_ts for row in rows]
    return min(stamps).isoformat(), max(stamps).isoformat()


async def backfill_intraday(
    store: PostgresIntradayBarStore,
    client: AlpacaIntradayBarsClient,
    http: AsyncHttpClient,
    tickers: list[str],
    start_day: str,
    end_day: str,
    *,
    dry_run: bool,
    timeframe: str = TIMEFRAME_1MIN,
    chunk_days: int = _DEFAULT_CHUNK_DAYS,
    request_limit: int = 10000,
    inter_request_delay_seconds: float = 0.3,
) -> BackfillSummary:
    """Fetch and (unless ``dry_run``) upsert intraday bars, one ticker-chunk at a time.

    The loop is ticker-major and chunk-minor so a single ticker completes before
    the next begins: a run interrupted halfway leaves whole tickers done rather
    than every ticker equally partial, which is the difference between resuming
    and restarting.
    """

    windows = chunk_window(start_day, end_day, chunk_days)
    total_fetched = 0
    total_upserted = 0
    for ticker_index, ticker in enumerate(tickers):
        for window_index, (window_start, window_end) in enumerate(windows):
            rows = await client.get_intraday_bars(
                http,
                ticker,
                window_start,
                window_end,
                timeframe=timeframe,
                limit=request_limit,
            )
            upserted = 0 if dry_run else await store.upsert_bars(rows)
            total_fetched += len(rows)
            total_upserted += upserted
            span_start, span_end = _span(rows)
            log.info(
                "market_data_intraday_backfill.chunk",
                ticker=ticker,
                timeframe=timeframe,
                window_start=window_start,
                window_end=window_end,
                rows_fetched=len(rows),
                rows_upserted=upserted,
                span_start=span_start,
                span_end=span_end,
                dry_run=dry_run,
            )
            is_last = ticker_index == len(tickers) - 1 and window_index == len(windows) - 1
            if inter_request_delay_seconds > 0 and not is_last:
                await asyncio.sleep(inter_request_delay_seconds)
    return BackfillSummary(
        tickers=len(tickers),
        rows_fetched=total_fetched,
        rows_upserted=total_upserted,
        dry_run=dry_run,
    )


async def run(
    postgres_dsn: str,
    market_data_settings: AlpacaMarketDataSettings,
    tickers: list[str],
    start_day: str,
    end_day: str,
    *,
    dry_run: bool,
    feed: str,
    adjustment: str,
    timeframe: str,
    chunk_days: int,
    request_limit: int,
    inter_request_delay_seconds: float,
    service_name: str = "market-data-intraday-backfill",
    log_level: str = "INFO",
    http_timeout: float = 60.0,
) -> BackfillSummary:
    """Run one intraday backfill pass against real infra and return its summary."""

    configure_logging(service_name, log_level)
    log.info(
        "market_data_intraday_backfill.starting",
        postgres_dsn="***",
        alpaca=market_data_settings.redacted(),
        tickers=tickers,
        start_day=start_day,
        end_day=end_day,
        timeframe=timeframe,
        chunk_days=chunk_days,
        feed=feed,
        adjustment=adjustment,
        dry_run=dry_run,
    )
    pool = await create_asyncpg_pool(postgres_dsn)
    store = PostgresIntradayBarStore(pool)
    client = AlpacaIntradayBarsClient(market_data_settings, feed=feed, adjustment=adjustment)
    try:
        if not dry_run:
            await store.ensure_schema()
        async with httpx.AsyncClient(timeout=http_timeout) as http:
            summary = await backfill_intraday(
                store,
                client,
                cast(AsyncHttpClient, http),
                tickers,
                start_day,
                end_day,
                dry_run=dry_run,
                timeframe=timeframe,
                chunk_days=chunk_days,
                request_limit=request_limit,
                inter_request_delay_seconds=inter_request_delay_seconds,
            )
    finally:
        await pool.close()
    log.info("market_data_intraday_backfill.complete", **summary.as_dict())
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill market_data.intraday_bars from Alpaca (IEX feed, adjustment=all). "
            "One ticker-day of 1Min bars is ~390 rows, so --since defaults to 30 days "
            "rather than the daily backfill's five years."
        )
    )
    parser.add_argument(
        "--tickers",
        default=None,
        metavar="AAPL,MSFT,...",
        help="Comma-separated tickers to backfill",
    )
    parser.add_argument(
        "--launch-list",
        action="store_true",
        help=(
            "Backfill every name on the Curator's Tier-3 launch list. Fifty names for "
            "the default 30 days is ~410k rows."
        ),
    )
    parser.add_argument(
        "--tickers-file",
        default=None,
        metavar="PATH",
        help="File of tickers, one per line (# comments and blank lines ignored)",
    )
    parser.add_argument(
        "--since",
        default=None,
        metavar="YYYY-MM-DD",
        help="Earliest day (inclusive); defaults to 30 days ago",
    )
    parser.add_argument(
        "--until",
        default=None,
        metavar="YYYY-MM-DD",
        help="Latest day (inclusive); defaults to today",
    )
    parser.add_argument(
        "--timeframe",
        default=TIMEFRAME_1MIN,
        help=f"Alpaca timeframe token (default {TIMEFRAME_1MIN}); e.g. 5Min, 15Min",
    )
    parser.add_argument(
        "--chunk-days",
        type=int,
        default=_DEFAULT_CHUNK_DAYS,
        help=(
            f"Days per fetch/upsert cycle (default {_DEFAULT_CHUNK_DAYS}). Bounds memory "
            "and makes a long run resumable. 0 disables chunking."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and report row counts without writing to the store",
    )
    return parser


def main() -> None:
    """Run one intraday backfill from CLI args and the ``MARKET_DATA_*`` environment."""

    parser = _build_parser()
    args = parser.parse_args()
    tickers = resolve_tickers(args.tickers, args.tickers_file, launch_list=args.launch_list)
    if not tickers:
        parser.error(
            "at least one ticker is required via --tickers, --tickers-file or --launch-list"
        )
    today = datetime.now(UTC).date()
    # Resolved here rather than by `resolve_window`'s own default: that default
    # is ~5 years, which at this grain is ~24.6M rows for the launch list.
    since = args.since or (today - timedelta(days=_DEFAULT_LOOKBACK_DAYS)).isoformat()
    try:
        start_day, end_day = resolve_window(since, args.until, today)
    except ValueError as e:
        parser.error(f"invalid date range — {e}")

    settings = Settings()
    configure_logging(settings.service_name, settings.log_level)
    log.info("market_data_intraday_backfill.config_loaded", **settings.redacted())

    summary = asyncio.run(
        run(
            postgres_dsn=settings.postgres_dsn_value(),
            market_data_settings=settings.market_data_settings(),
            tickers=tickers,
            start_day=start_day,
            end_day=end_day,
            dry_run=args.dry_run,
            feed=settings.feed,
            adjustment=settings.adjustment,
            timeframe=args.timeframe,
            chunk_days=args.chunk_days,
            request_limit=settings.request_limit,
            inter_request_delay_seconds=settings.inter_ticker_delay_seconds,
            service_name=settings.service_name,
            log_level=settings.log_level,
            http_timeout=settings.http_timeout,
        )
    )
    print(summary.render())


if __name__ == "__main__":
    main()


__all__ = [
    "backfill_intraday",
    "chunk_window",
    "main",
    "run",
]
