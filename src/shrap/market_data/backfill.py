"""Backfill orchestration and CLI for ``market_data.daily_bars``.

The console script ``shrap-market-data-backfill`` fills the Strategy
Evaluator's historical OHLCV prerequisite (spec §Inputs, ``market_data.*``) and
is equally usable for the Regime Classifier's threshold backfill. It resolves a
ticker set and a date window, fetches daily bars from Alpaca one ticker at a
time (IEX feed, ``adjustment=all``), and upserts them idempotently.

The module splits into a testable core and a thin infra wrapper, mirroring
:mod:`shrap.intelligence.filing_processor.backfill`:

- :func:`parse_tickers`, :func:`read_tickers_file`, :func:`resolve_tickers`,
  :func:`resolve_window` — pure argument resolution.
- :func:`backfill_tickers` — the fetch/upsert loop over an injected client and
  store, with a ``dry_run`` mode that fetches counts and writes nothing.
- :func:`run` — real-infra wiring (asyncpg pool, ``httpx`` client).
- :func:`main` — argparse entrypoint.

**Ticker source (temporary).** Tickers are passed explicitly via ``--tickers``
and/or ``--tickers-file`` for now. The default set will later come from the
Universe Curator's Tier 3 state; until that agent exists, the caller names the
tickers.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import structlog

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.intelligence.market_data import AlpacaMarketDataSettings
from shrap.market_data.client import AlpacaDailyBarsClient
from shrap.market_data.config import Settings
from shrap.market_data.store import DailyBarRow, PostgresDailyBarStore
from shrap.trading_floor.alpaca import AsyncHttpClient

log = structlog.get_logger(__name__)

# Default backtest lookback when --since is omitted. Approximate five years is
# enough for the Evaluator's default window (spec: "5 years daily"); the caller
# can always pin an exact --since.
_DEFAULT_LOOKBACK_DAYS = 5 * 365


@dataclass(frozen=True, slots=True)
class BackfillSummary:
    """Aggregate outcome of one backfill run."""

    tickers: int
    rows_fetched: int
    rows_upserted: int
    dry_run: bool

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "tickers": self.tickers,
            "rows_fetched": self.rows_fetched,
            "rows_upserted": self.rows_upserted,
            "dry_run": self.dry_run,
        }

    def render(self) -> str:
        """One-line plain-text summary the CLI prints on exit."""

        return " ".join(f"{key}={value}" for key, value in self.as_dict().items())


def parse_tickers(value: str | None) -> list[str]:
    """Split a ``--tickers AAPL,MSFT`` string into upper-cased symbols."""

    if not value:
        return []
    return [t.strip().upper() for t in value.split(",") if t.strip()]


def read_tickers_file(path: str | Path) -> list[str]:
    """Read a tickers file: one symbol per line, ``#`` comments and blanks ignored."""

    tickers: list[str] = []
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            tickers.append(line.upper())
    return tickers


def resolve_tickers(tickers: str | None, tickers_file: str | Path | None) -> list[str]:
    """Merge ``--tickers`` and ``--tickers-file`` into a deduped, ordered list."""

    merged = parse_tickers(tickers)
    if tickers_file is not None:
        merged += read_tickers_file(tickers_file)
    seen: set[str] = set()
    ordered: list[str] = []
    for ticker in merged:
        if ticker not in seen:
            seen.add(ticker)
            ordered.append(ticker)
    return ordered


def resolve_window(since: str | None, until: str | None, today: date) -> tuple[str, str]:
    """Resolve ``--since``/``--until`` (YYYY-MM-DD) into inclusive ``(start, end)`` days.

    ``since`` defaults to ~5 years before ``today``; ``until`` defaults to
    ``today``. Both bounds are validated as ISO dates and ``since <= until`` is
    enforced. Raises ``ValueError`` on a malformed date or an inverted range.
    """

    start_day = since if since is not None else (today - timedelta(days=_DEFAULT_LOOKBACK_DAYS))
    end_day = until if until is not None else today
    start = date.fromisoformat(start_day) if isinstance(start_day, str) else start_day
    end = date.fromisoformat(end_day) if isinstance(end_day, str) else end_day
    if start > end:
        raise ValueError(f"--since {start.isoformat()} is after --until {end.isoformat()}")
    return start.isoformat(), end.isoformat()


def _span(rows: list[DailyBarRow]) -> tuple[str | None, str | None]:
    if not rows:
        return None, None
    days = [row.session_date for row in rows]
    return min(days).isoformat(), max(days).isoformat()


async def backfill_tickers(
    store: PostgresDailyBarStore,
    client: AlpacaDailyBarsClient,
    http: AsyncHttpClient,
    tickers: list[str],
    start_day: str,
    end_day: str,
    *,
    dry_run: bool,
    request_limit: int = 10000,
    inter_ticker_delay_seconds: float = 0.3,
) -> BackfillSummary:
    """Fetch and (unless ``dry_run``) upsert daily bars for each ticker in turn.

    One ticker per Alpaca call, sequential, with a small delay between tickers
    for politeness. Logs per-ticker progress (rows fetched/upserted, date span).
    In ``dry_run`` mode nothing is written — the run reports fetch counts only.
    """

    total_fetched = 0
    total_upserted = 0
    for index, ticker in enumerate(tickers):
        rows = await client.get_daily_bars(http, ticker, start_day, end_day, limit=request_limit)
        upserted = 0 if dry_run else await store.upsert_bars(rows)
        total_fetched += len(rows)
        total_upserted += upserted
        span_start, span_end = _span(rows)
        log.info(
            "market_data_backfill.ticker",
            ticker=ticker,
            rows_fetched=len(rows),
            rows_upserted=upserted,
            span_start=span_start,
            span_end=span_end,
            dry_run=dry_run,
        )
        if inter_ticker_delay_seconds > 0 and index < len(tickers) - 1:
            await asyncio.sleep(inter_ticker_delay_seconds)
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
    request_limit: int,
    inter_ticker_delay_seconds: float,
    service_name: str = "market-data-backfill",
    log_level: str = "INFO",
    http_timeout: float = 30.0,
) -> BackfillSummary:
    """Run one backfill pass against real infra and return its summary."""

    configure_logging(service_name, log_level)
    log.info(
        "market_data_backfill.starting",
        postgres_dsn="***",
        alpaca=market_data_settings.redacted(),
        tickers=tickers,
        start_day=start_day,
        end_day=end_day,
        feed=feed,
        adjustment=adjustment,
        dry_run=dry_run,
    )
    pool = await create_asyncpg_pool(postgres_dsn)
    store = PostgresDailyBarStore(pool)
    client = AlpacaDailyBarsClient(market_data_settings, feed=feed, adjustment=adjustment)
    try:
        if not dry_run:
            await store.ensure_schema()
        async with httpx.AsyncClient(timeout=http_timeout) as http:
            summary = await backfill_tickers(
                store,
                client,
                cast(AsyncHttpClient, http),
                tickers,
                start_day,
                end_day,
                dry_run=dry_run,
                request_limit=request_limit,
                inter_ticker_delay_seconds=inter_ticker_delay_seconds,
            )
    finally:
        await pool.close()
    log.info("market_data_backfill.complete", **summary.as_dict())
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill market_data.daily_bars from Alpaca (IEX feed, adjustment=all). "
            "Tickers are explicit for now; the default set will later come from the "
            "Universe Curator's Tier 3 state."
        )
    )
    parser.add_argument(
        "--tickers",
        default=None,
        metavar="AAPL,MSFT,...",
        help="Comma-separated tickers to backfill",
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
        help="Earliest session date (inclusive); defaults to ~5 years ago",
    )
    parser.add_argument(
        "--until",
        default=None,
        metavar="YYYY-MM-DD",
        help="Latest session date (inclusive); defaults to today",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and report row counts without writing to the store",
    )
    return parser


def main() -> None:
    """Run one backfill pass from CLI args and the ``MARKET_DATA_*`` environment."""

    parser = _build_parser()
    args = parser.parse_args()
    tickers = resolve_tickers(args.tickers, args.tickers_file)
    if not tickers:
        parser.error("at least one ticker is required via --tickers or --tickers-file")
    try:
        start_day, end_day = resolve_window(args.since, args.until, datetime.now(UTC).date())
    except ValueError as e:
        parser.error(f"invalid date range — {e}")

    settings = Settings()
    configure_logging(settings.service_name, settings.log_level)
    log.info("market_data_backfill.config_loaded", **settings.redacted())

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
            request_limit=settings.request_limit,
            inter_ticker_delay_seconds=settings.inter_ticker_delay_seconds,
            service_name=settings.service_name,
            log_level=settings.log_level,
            http_timeout=settings.http_timeout,
        )
    )
    print(summary.render())


if __name__ == "__main__":
    main()


__all__ = [
    "BackfillSummary",
    "backfill_tickers",
    "parse_tickers",
    "read_tickers_file",
    "resolve_tickers",
    "resolve_window",
    "run",
]
