"""``shrap-market-data-fundamentals-backfill`` — SEC XBRL fundamentals, one request per name.

    docker compose run --rm market-data shrap-market-data-fundamentals-backfill --launch-list

One ``companyfacts`` request per registrant fetches its whole history, so the
fifty-name launch list is fifty requests; re-running it refreshes everything
idempotently. ETFs have no CIK and are reported as unmapped rather than skipped
silently. SEC enforces 10 requests per second; the delay stays well under it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from typing import Any

import structlog

from shrap.market_data.backfill import resolve_tickers
from shrap.market_data.fundamentals import parse_fundamentals
from shrap.market_data.fundamentals_store import PostgresFundamentalsStore
from shrap.market_data.shares import company_facts_url
from shrap.market_data.shares_backfill import (
    DEFAULT_DELAY_SECONDS,
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_USER_AGENT,
    load_ticker_cik_map,
)

log = structlog.get_logger(service="market-data-fundamentals")

# **Funds file financial statements too, and they must not get fundamentals.**
# The first dry run (2026-09-24) fetched figures for GLD, UUP, IBIT and ETHA:
# a gold or bitcoin trust's "total assets" is the metal or coin it holds, so a
# book-to-price screen would rank GLD as a deep-value stock. Every `liquid-etf`
# name is excluded, plus the two crypto trusts, which the launch list files
# under `crypto` beside MARA and RIOT (operating companies, kept). Their SEC
# registrant names — "iShares Bitcoin Trust ETF", "iShares Ethereum Trust ETF" —
# are recorded in `universe_curator.launch_list`.
CRYPTO_TRUSTS: frozenset[str] = frozenset({"IBIT", "ETHA"})


def fund_tickers() -> frozenset[str]:
    from shrap.research.universe_curator.launch_list import CATEGORY_ETF, LAUNCH_LIST

    return frozenset(e.ticker for e in LAUNCH_LIST if e.category == CATEGORY_ETF) | CRYPTO_TRUSTS


@dataclass(frozen=True, slots=True)
class FundamentalsResult:
    tickers: int
    rows_fetched: int
    rows_upserted: int
    unmapped: tuple[str, ...]
    empty: tuple[str, ...]
    dry_run: bool
    funds: tuple[str, ...] = ()

    def summary(self) -> str:
        covered = self.tickers - len(self.unmapped) - len(self.empty) - len(self.funds)
        return (
            f"fundamentals backfill: {covered}/{self.tickers} names covered, "
            f"{self.rows_fetched} figures fetched, {self.rows_upserted} upserted"
            f"{' (dry run)' if self.dry_run else ''}; "
            f"unmapped (no CIK — ETFs, trusts): {', '.join(self.unmapped) or 'none'}; "
            f"mapped but no figures: {', '.join(self.empty) or 'none'}; "
            f"funds, excluded on purpose: {', '.join(self.funds) or 'none'}"
        )


async def run_backfill(
    http: Any,
    store: PostgresFundamentalsStore | None,
    *,
    tickers: list[str],
    user_agent: str,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    dry_run: bool = False,
    funds: frozenset[str] | None = None,
) -> FundamentalsResult:
    excluded = fund_tickers() if funds is None else funds
    skipped = [t for t in tickers if t.strip().upper() in excluded]
    tickers = [t for t in tickers if t.strip().upper() not in excluded]
    cik_by_ticker = await load_ticker_cik_map(http, user_agent=user_agent, timeout=timeout)
    fetched = upserted = 0
    unmapped: list[str] = []
    empty: list[str] = []
    for index, ticker in enumerate(tickers):
        cik = cik_by_ticker.get(ticker.strip().upper())
        if cik is None:
            unmapped.append(ticker)
            continue
        response = await http.get(
            company_facts_url(cik),
            params={},
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout,
        )
        rows = (
            parse_fundamentals(response.json(), ticker=ticker, cik=cik)
            if response.status_code == 200
            else []
        )
        if not rows:
            empty.append(ticker)
        fetched += len(rows)
        if rows and store is not None and not dry_run:
            upserted += await store.upsert_rows(rows)
        log.info(
            "market_data_fundamentals.ticker",
            ticker=ticker,
            cik=cik,
            status=response.status_code,
            figures=len(rows),
            metrics=len({r.metric for r in rows}),
            dry_run=dry_run,
        )
        if delay_seconds > 0 and index + 1 < len(tickers):
            await asyncio.sleep(delay_seconds)
    return FundamentalsResult(
        tickers=len(tickers) + len(skipped),
        rows_fetched=fetched,
        rows_upserted=upserted,
        unmapped=tuple(unmapped),
        empty=tuple(empty),
        dry_run=dry_run,
        funds=tuple(skipped),
    )


async def _run(args: argparse.Namespace, tickers: list[str]) -> str:
    import httpx

    from shrap.common.db import create_asyncpg_pool

    pool = None
    store: PostgresFundamentalsStore | None = None
    if not args.dry_run:
        pool = await create_asyncpg_pool(args.dsn)
        store = PostgresFundamentalsStore(pool)
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
    parser = argparse.ArgumentParser(description="Backfill market_data.fundamentals from SEC XBRL")
    parser.add_argument("--tickers", default=None, metavar="AAPL,MSFT,...")
    parser.add_argument("--launch-list", action="store_true")
    parser.add_argument("--tickers-file", default=None, metavar="PATH")
    parser.add_argument(
        "--user-agent", default=os.environ.get("SEC_USER_AGENT", DEFAULT_USER_AGENT)
    )
    parser.add_argument("--delay-seconds", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--dsn",
        default=os.environ.get(
            "MARKET_DATA_POSTGRES_DSN", "postgresql://shrap:shrap@postgres:5432/shrap"
        ),
    )
    args = parser.parse_args()
    tickers = resolve_tickers(args.tickers, args.tickers_file, launch_list=args.launch_list)
    if not tickers:
        parser.error(
            "at least one ticker is required via --tickers, --tickers-file or --launch-list"
        )
    print(asyncio.run(_run(args, tickers)))


if __name__ == "__main__":
    main()
