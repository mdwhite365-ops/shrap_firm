"""``shrap-tech-watcher-refilter`` — re-score the backlog under a newer prompt.

A prompt fix only reaches items that arrive after it ships. Everything already
scored keeps whatever verdict the prompt of the day produced, which for a slow
feed (DOE newsroom ingests ~16 items a month) means a fix effectively never
lands. This re-scores items last filtered under an older prompt version.

Safe by construction: ``research.filter_verdict_history`` is append-only, so
the prior prompt's verdicts survive and the before/after comparison stays
queryable. The v2 re-filter (2026-07-18) was run as ad-hoc SQL and destroyed
exactly that comparison — see KI-007.

    shrap-tech-watcher-refilter --dry-run
    shrap-tech-watcher-refilter --source doe-newsroom --limit 50
"""

from __future__ import annotations

import argparse
import asyncio
import os

import httpx

from shrap.common.db import create_asyncpg_pool
from shrap.llm import TierLLMClient, TierRegistry
from shrap.llm.registry import TIER_LOCAL_CLASSIFICATION
from shrap.research.tech_watcher.filter import refilter_pass


async def _run(args: argparse.Namespace) -> str:
    pool = await create_asyncpg_pool(args.dsn)
    try:
        # A dry run only counts rows; no model call, so no HTTP client needed.
        if args.dry_run:
            report = await refilter_pass(
                pool, _NoLLM(), max_items=args.limit, source=args.source, dry_run=True
            )
            return report.render()
        async with httpx.AsyncClient(follow_redirects=True) as http:
            llm = TierLLMClient(TierRegistry(dict(os.environ)), http)
            report = await refilter_pass(
                pool,
                llm,
                max_items=args.limit,
                source=args.source,
                tier=args.tier,
            )
        return report.render()
    finally:
        await pool.close()


class _NoLLM:
    """Placeholder for the dry-run path, which never scores anything."""

    async def complete(self, *args: object, **kwargs: object) -> object:  # pragma: no cover
        raise AssertionError("dry run must not call the model")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-score already-filtered items under the current filter prompt."
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get(
            "TECH_WATCHER_POSTGRES_DSN", "postgresql://shrap:shrap@postgres:5432/shrap"
        ),
        help="Postgres DSN (default: TECH_WATCHER_POSTGRES_DSN env)",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Restrict to one source (e.g. doe-newsroom). Default: all sources.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=300,
        help="Maximum items to re-score in this pass (default: 300)",
    )
    parser.add_argument(
        "--tier",
        default=TIER_LOCAL_CLASSIFICATION,
        help=f"LLM tier alias (default: {TIER_LOCAL_CLASSIFICATION})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many items are eligible without calling the model or writing",
    )
    args = parser.parse_args()
    print(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
