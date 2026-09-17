"""Backfill orchestration and CLI for ``market_data.shares_outstanding``.

``shrap-market-data-shares-backfill`` closes the cheapest of KI-035's seven
capability gaps: the Hypothesis Generator refused a paper for want of market
capitalisation, and no share-count column existed anywhere in the firm.

**Where the data comes from.** SEC XBRL `companyconcept`, which serves the full
reported history of one concept for one registrant in a single request:

    https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/dei/EntityCommonStockSharesOutstanding.json

One request per ticker for all of history, not one per period — so a fifty-name
backfill is fifty requests, and re-running it is fifty more. That is why this is
a plain sequential loop with a politeness delay rather than the chunked,
resumable machinery the price backfills need.

**SEC rate limits are a published 10 requests/second and they are enforced.** The
default delay is deliberately well under that; the tool is not worth optimising
into a 429.

**Every row is kept, including superseded ones.** A restated share count arrives
as a new row with a later ``filed_at`` and the original stays — a backtest
replaying 2024 must see what was believed in 2024. See
:mod:`shrap.market_data.shares` for why ``filed_at`` rather than ``as_of`` is the
column that governs visibility.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from typing import Any

import structlog

from shrap.market_data.backfill import resolve_tickers
from shrap.market_data.shares import SharesRow, company_concept_url, parse_company_concept
from shrap.market_data.shares_store import PostgresSharesStore

log = structlog.get_logger(service="market-data-shares")

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# SEC publishes a 10 req/s limit and enforces it. 0.2s is half that, for a tool
# that runs at most once a quarter per name.
DEFAULT_DELAY_SECONDS = 0.2

DEFAULT_HTTP_TIMEOUT = 30.0

# SEC requires a descriptive User-Agent with contact details and returns 403
# without one. Overridable because the contact address should be the operator's.
DEFAULT_USER_AGENT = "shrap-firm research contact@example.com"


@dataclass(frozen=True, slots=True)
class BackfillResult:
    tickers: int
    rows_fetched: int
    rows_upserted: int
    unmapped: tuple[str, ...]
    dry_run: bool

    def summary(self) -> str:
        line = (
            f"tickers={self.tickers} rows_fetched={self.rows_fetched} "
            f"rows_upserted={self.rows_upserted} dry_run={self.dry_run}"
        )
        if self.unmapped:
            line += f" unmapped={','.join(self.unmapped)}"
        return line


async def fetch_shares_for_ticker(
    http: Any,
    *,
    ticker: str,
    cik: str,
    user_agent: str,
    timeout: float,
) -> list[SharesRow]:
    """Every reported share count for one registrant, oldest filing first.

    A non-200 returns no rows rather than raising: one delisted or
    never-XBRL-filing name must not abort a fifty-name backfill. The count is
    reported so a silent zero is visible in the log.
    """

    url = company_concept_url(cik)
    response = await http.get(
        url,
        params={},
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        timeout=timeout,
    )
    if response.status_code != 200:
        log.warning(
            "market_data_shares_backfill.fetch_failed",
            ticker=ticker,
            cik=cik,
            status=response.status_code,
        )
        return []
    return parse_company_concept(response.json(), ticker=ticker, cik=cik)


async def load_ticker_cik_map(http: Any, *, user_agent: str, timeout: float) -> dict[str, str]:
    """TICKER -> CIK for every SEC registrant.

    Reuses the Filing Processor's parser rather than re-deriving the shape of a
    file two modules now depend on.
    """

    from shrap.intelligence.filing_processor.discovery import parse_company_tickers

    response = await http.get(
        COMPANY_TICKERS_URL,
        params={},
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        timeout=timeout,
    )
    if response.status_code != 200:
        raise RuntimeError(f"company_tickers.json fetch failed: HTTP {response.status_code}")
    return parse_company_tickers(response.json())


async def run_backfill(
    http: Any,
    store: PostgresSharesStore | None,
    *,
    tickers: list[str],
    user_agent: str,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    dry_run: bool = False,
) -> BackfillResult:
    """Fetch and store share counts for ``tickers``."""

    cik_by_ticker = await load_ticker_cik_map(http, user_agent=user_agent, timeout=timeout)
    fetched = 0
    upserted = 0
    unmapped: list[str] = []

    for index, ticker in enumerate(tickers):
        cik = cik_by_ticker.get(ticker.strip().upper())
        if cik is None:
            # ETFs and trusts are not XBRL registrants under a ticker the way an
            # operating company is. Named rather than silently skipped, because
            # "SPY has no market cap" should be a fact the operator sees.
            unmapped.append(ticker)
            continue
        rows = await fetch_shares_for_ticker(
            http, ticker=ticker, cik=cik, user_agent=user_agent, timeout=timeout
        )
        fetched += len(rows)
        if rows and not dry_run and store is not None:
            upserted += await store.upsert_rows(rows)
        log.info(
            "market_data_shares_backfill.ticker",
            ticker=ticker,
            cik=cik,
            rows=len(rows),
            first_filed=rows[0].filed_at.isoformat() if rows else None,
            last_filed=rows[-1].filed_at.isoformat() if rows else None,
            dry_run=dry_run,
        )
        if delay_seconds > 0 and index + 1 < len(tickers):
            await asyncio.sleep(delay_seconds)

    result = BackfillResult(
        tickers=len(tickers),
        rows_fetched=fetched,
        rows_upserted=upserted,
        unmapped=tuple(unmapped),
        dry_run=dry_run,
    )
    log.info(
        "market_data_shares_backfill.complete",
        **{
            "tickers": result.tickers,
            "rows_fetched": result.rows_fetched,
            "rows_upserted": result.rows_upserted,
            "unmapped": list(result.unmapped),
            "dry_run": result.dry_run,
        },
    )
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill market_data.shares_outstanding from SEC XBRL companyconcept. "
            "Market capitalisation is shares x the price the firm already stores; "
            "this supplies the missing half (KI-035)."
        )
    )
    parser.add_argument("--tickers", default=None, metavar="AAPL,MSFT,...")
    parser.add_argument(
        "--launch-list",
        action="store_true",
        help="Every name on the Curator's Tier-3 launch list (one request each)",
    )
    parser.add_argument("--tickers-file", default=None, metavar="PATH")
    parser.add_argument(
        "--user-agent",
        default=os.environ.get("SEC_USER_AGENT", DEFAULT_USER_AGENT),
        help="SEC requires a descriptive User-Agent with contact details (SEC_USER_AGENT env)",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help=f"Politeness delay between registrants (default {DEFAULT_DELAY_SECONDS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and report row counts without writing",
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get(
            "MARKET_DATA_POSTGRES_DSN", "postgresql://shrap:shrap@postgres:5432/shrap"
        ),
    )
    return parser


async def _run(args: argparse.Namespace, tickers: list[str]) -> str:
    import httpx

    from shrap.common.db import create_asyncpg_pool

    pool = None
    store: PostgresSharesStore | None = None
    if not args.dry_run:
        pool = await create_asyncpg_pool(args.dsn)
        store = PostgresSharesStore(pool)
        await store.ensure_schema()
    try:
        async with httpx.AsyncClient() as http:
            result = await run_backfill(
                http,
                store,
                tickers=tickers,
                user_agent=args.user_agent,
                delay_seconds=args.delay_seconds,
                dry_run=args.dry_run,
            )
        return result.summary()
    finally:
        if pool is not None:
            await pool.close()


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    tickers = resolve_tickers(args.tickers, args.tickers_file, launch_list=args.launch_list)
    if not tickers:
        parser.error(
            "at least one ticker is required via --tickers, --tickers-file or --launch-list"
        )
    print(asyncio.run(_run(args, tickers)))


if __name__ == "__main__":
    main()
