"""Entrypoint for `shrap-filing-processor-discover`.

The sibling of `shrap-filing-processor-backfill`, and the distinction matters:

- **backfill** re-drives fetch → score → publish over 8-Ks the Tech Watcher
  already ingested. It cannot see anything filed before the Tech Watcher was
  running, because it reads that agent's table.
- **discover** (this one) asks EDGAR directly what a registrant filed over an
  arbitrary date range and queues what it finds as pending rows. It writes no
  events and scores nothing — the live service drains the queue on its own
  cadence, so a backfilled filing is read by exactly the same path as a live
  one.

Run the two in that order to reach filings older than the funnel:
``discover --since 2024-01-01`` then let the service work, or drive the queue
immediately with ``backfill --since 2024-01-01``.

No new Dockerfile: runs inside the existing filing-processor container via
``docker compose exec``, same as the backfill CLI.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date

import structlog

from shrap.agents.intelligence.filing_processor.config import Settings
from shrap.common.logging import configure_logging
from shrap.intelligence.filing_processor.discovery import (
    DEFAULT_FORMS,
    FORM_8K_AMENDED,
    DiscoveryConfig,
    default_symbols,
    parse_date_range,
    run,
)

log = structlog.get_logger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Discover 8-Ks straight from EDGAR's submissions API over a filing-date "
            "range and queue them as pending filings for the live fetch/score path."
        )
    )
    parser.add_argument(
        "--since",
        required=True,
        metavar="YYYY-MM-DD",
        help="Discover 8-Ks filed on/after this date",
    )
    parser.add_argument(
        "--until",
        default=None,
        metavar="YYYY-MM-DD",
        help="Discover 8-Ks filed on/before this date (default: today)",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        metavar="AAPL,NVDA",
        help=(
            "Comma-separated tickers (default: the configured Tier 3 roster). "
            "Tickers outside the roster are resolved via SEC company_tickers.json"
        ),
    )
    parser.add_argument(
        "--include-amendments",
        action="store_true",
        help=f"Also discover {FORM_8K_AMENDED} amendments (default: original 8-Ks only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Report what would be queued, including how much of it the store "
            "already has, without writing anything"
        ),
    )
    return parser


def _parse_symbols(raw: str | None, settings: Settings) -> tuple[str, ...]:
    if raw is None:
        return default_symbols(settings.run_config().roster)
    return tuple(s.strip().upper() for s in raw.split(",") if s.strip())


def main() -> None:
    """Run one discovery pass from CLI args and the agent's environment settings."""

    parser = _build_parser()
    args = parser.parse_args()

    since: date
    until: date
    try:
        since, until = parse_date_range(args.since, args.until)
    except ValueError as e:
        parser.error(f"invalid date — {e}")
    if since >= until:
        parser.error("--since must be earlier than --until")

    settings = Settings()
    configure_logging(settings.service_name, settings.log_level)
    log.info("filing_discovery.config_loaded", **settings.redacted())

    symbols = _parse_symbols(args.symbols, settings)
    if not symbols:
        parser.error("no symbols to discover — pass --symbols or set FILING_PROCESSOR_ROSTER")

    forms = (*DEFAULT_FORMS, FORM_8K_AMENDED) if args.include_amendments else DEFAULT_FORMS

    summary = asyncio.run(
        run(
            postgres_dsn=settings.postgres_dsn_value(),
            sec_user_agent=settings.sec_user_agent,
            roster=settings.run_config().roster,
            config=DiscoveryConfig(
                forms=forms,
                throttle_seconds=settings.fetch_throttle_seconds,
                http_timeout=settings.http_timeout,
            ),
            symbols=symbols,
            since=since,
            until=until,
            dry_run=args.dry_run,
            log_level=settings.log_level,
        )
    )
    print(summary.render())


if __name__ == "__main__":
    main()
