"""``shrap-tech-watcher-edgar-text`` — backfill filing bodies for EDGAR items (KI-026).

    shrap-tech-watcher-edgar-text --dry-run --limit 99999   # true eligible count
    shrap-tech-watcher-edgar-text --limit 200               # fetch a batch

The corpus is ~3,700 filings whose stored ``summary`` is an accession number and
a file size. This walks them, dereferences each filing link, and stores the
document body so the filter is judging contents instead of metadata.

**Run it in batches, and expect it to be slow.** One request per filing,
sequentially, under SEC's fair-access policy — a few thousand filings is tens of
minutes, not seconds. The pass is resumable by construction: a row keeps
``document_text IS NULL`` until it succeeds, so re-running picks up exactly what
is left and a failed batch costs nothing but the time already spent.

**After a backfill, re-score.** New items get their body at ingest, but every
existing verdict was formed against metadata and stays stale until:

    shrap-tech-watcher-refilter --force --limit 99999

``--force`` rather than a prompt-version bump: the prompt did not change, the
item content did, and ``FILTER_PROMPT_VERSION`` means "which prompt ran".

Runs in the **tech-watcher** container. It imports the Filing Processor's EDGAR
client, which is dependency-pure (runbook 1e), so no numpy image is needed.
"""

from __future__ import annotations

import argparse
import asyncio
import os

import httpx

from shrap.common.db import create_asyncpg_pool
from shrap.intelligence.filing_processor.client import EdgarFilingClient, HTTPClient
from shrap.research.tech_watcher.edgar_text import DEFAULT_MAX_CHARS, edgar_text_pass
from shrap.research.tech_watcher.store import PostgresRawItemStore

# SEC requires a descriptive User-Agent with a contact address and bans clients
# that omit it. Mirrors the Tech Watcher's ingest header rather than inventing a
# second identity for the same firm hitting the same host.
DEFAULT_USER_AGENT = os.environ.get(
    "TECH_WATCHER_EDGAR_USER_AGENT", "Shrap Research (mdwhite365@gmail.com)"
)


async def _run(args: argparse.Namespace) -> str:
    pool = await create_asyncpg_pool(args.dsn)
    try:
        # Idempotent, and the reason this CLI can run before the service is
        # redeployed: it adds `document_text` if the DB predates this card.
        await PostgresRawItemStore(pool).ensure_schema()
        client = EdgarFilingClient(args.user_agent)
        async with httpx.AsyncClient(follow_redirects=True) as http:
            report = await edgar_text_pass(
                pool,
                client,
                _as_http(http),
                max_items=args.limit,
                max_chars=args.max_chars,
                dry_run=args.dry_run,
            )
        return report.render()
    finally:
        await pool.close()


def _as_http(client: httpx.AsyncClient) -> HTTPClient:
    from typing import cast

    return cast(HTTPClient, client)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shrap-tech-watcher-edgar-text",
        description=(
            "Fetch and store EDGAR filing bodies for items whose feed summary is "
            "index metadata (KI-026). Re-score afterwards with "
            "`shrap-tech-watcher-refilter --force`."
        ),
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get(
            "TECH_WATCHER_POSTGRES_DSN", "postgresql://shrap:shrap@postgres:5432/shrap"
        ),
        help="Postgres DSN (default: TECH_WATCHER_POSTGRES_DSN env)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum filings to fetch this pass (default: 200)",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"Characters of item-section text to store per filing (default {DEFAULT_MAX_CHARS})",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="SEC requires a descriptive User-Agent with a contact address",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many filings are eligible without fetching anything",
    )
    return parser


def main() -> None:
    print(asyncio.run(_run(_build_parser().parse_args())))


if __name__ == "__main__":
    main()
