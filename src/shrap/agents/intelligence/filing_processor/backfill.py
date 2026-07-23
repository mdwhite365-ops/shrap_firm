"""Entrypoint for `shrap-filing-processor-backfill`.

Mike-initiated on-demand backfill, deferred from the service card (PR #68)
to this one — see ``docs/agents/intelligence/filing-processor.md`` §Trigger,
"On-demand: Mike-initiated backfill over an accession-number or date range
(CLI, same container)." Runs the same fetch -> score -> publish path as the
live service (:mod:`shrap.intelligence.filing_processor.service`) for an
explicit ``--accession`` list or a ``--since``/``--until`` filing-date range
instead of the poll cursor.

No new Dockerfile: this CLI runs inside the existing filing-processor
container via ``docker exec``, the same way ``shrap-tech-watcher-promote``
(``src/shrap/research/tech_watcher/promotion.py``) runs inside the
tech-watcher container.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

import structlog

from shrap.agents.intelligence.filing_processor.config import Settings
from shrap.common.logging import configure_logging
from shrap.intelligence.filing_processor.backfill import parse_date_range, run

log = structlog.get_logger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill the Filing Processor's fetch/score/publish path for "
            "specific 8-Ks, by accession number or filing-date range."
        )
    )
    parser.add_argument(
        "--accession",
        action="append",
        default=None,
        metavar="ACCESSION",
        help="EDGAR accession number to backfill; repeat the flag for multiple",
    )
    parser.add_argument(
        "--since",
        default=None,
        metavar="YYYY-MM-DD",
        help="Backfill 8-Ks filed on/after this date (for the configured Tier 3 roster)",
    )
    parser.add_argument(
        "--until",
        default=None,
        metavar="YYYY-MM-DD",
        help="Backfill 8-Ks filed on/before this date (requires --since)",
    )
    parser.add_argument(
        "--rescore",
        action="store_true",
        help=(
            "Re-score filings that already have a verdict; appends new "
            "verdict-history rows, never overwrites the existing ones (KI-007)"
        ),
    )
    return parser


def main() -> None:
    """Run one backfill pass from CLI args and the agent's environment settings."""

    parser = _build_parser()
    args = parser.parse_args()
    if not args.accession and args.since is None:
        parser.error("either --accession (repeatable) or --since is required")
    if args.accession and args.since is not None:
        parser.error("--accession and --since are mutually exclusive")
    if args.until is not None and args.since is None:
        parser.error("--until requires --since")

    since_dt: datetime | None = None
    until_dt: datetime | None = None
    if args.since is not None:
        try:
            since_dt, until_dt = parse_date_range(args.since, args.until)
        except ValueError as e:
            parser.error(f"invalid date — {e}")

    settings = Settings()
    configure_logging(settings.service_name, settings.log_level)
    log.info("filing_backfill.config_loaded", **settings.redacted())

    summary = asyncio.run(
        run(
            redis_url=settings.redis_url,
            postgres_dsn=settings.postgres_dsn_value(),
            sec_user_agent=settings.sec_user_agent,
            config=settings.run_config(),
            accessions=args.accession,
            since=since_dt,
            until=until_dt,
            rescore=args.rescore,
            service_name=settings.service_name,
            log_level=settings.log_level,
            http_timeout=settings.http_timeout,
        )
    )
    print(summary.render())


if __name__ == "__main__":
    main()
