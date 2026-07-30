"""``shrap-literature-refilter`` — re-score the q-fin backlog under a newer prompt.

    shrap-literature-refilter --dry-run
    shrap-literature-refilter --limit 100
    shrap-literature-refilter --force --limit 20

A prompt fix only reaches items that arrive after it ships. For a feed of a few
dozen papers a day, that means a fix effectively never lands on the backlog and
the corpus stays a mixture of verdicts from prompts that no longer exist.

The mirror of ``shrap-tech-watcher-refilter``, one funnel over. It exists
because filter v2 (2026-07-30) corrected a real false accept — a paper whose
finding is that the effect *fails* — with 100 items already scored under v1, and
the only recovery available was deleting the rows so ingest would re-fetch them.
That works and throws away the before/after comparison, which is the only thing
that says whether the fix did anything.

Selection keys on the **(prompt version, model)** pair, not on the prompt alone.
The world-changer re-filter learned that on 2026-07-27: after swapping models
under an unchanged prompt, ``prompt_version < N`` matched nothing and the pass
reported "0 items scored" — silently declining to test the very change being
made. ``--force`` re-scores regardless of either.
"""

from __future__ import annotations

import argparse
import asyncio
import os

import httpx

from shrap.llm import TierLLMClient, TierRegistry
from shrap.llm.registry import TIER_LOCAL_CLASSIFICATION
from shrap.research.hypothesis_generator.literature import PostgresLiteratureStore
from shrap.research.tech_watcher.literature_filter import literature_refilter_pass


class _NoLLM:
    """Placeholder for the dry-run path, which never scores anything."""

    async def complete(self, *args: object, **kwargs: object) -> object:  # pragma: no cover
        raise AssertionError("dry run must not call the model")


async def _run(args: argparse.Namespace) -> str:
    from shrap.common.db import create_asyncpg_pool

    registry = TierRegistry(dict(os.environ))
    # A verdict's identity is (prompt version, model), so the pass needs to know
    # which model would score these items now — without it a model swap under an
    # unchanged prompt selects nothing and reports success.
    current_model = registry.resolve(args.tier).model
    pool = await create_asyncpg_pool(args.dsn)
    header = f"current model for tier {args.tier}: {current_model}\n"
    try:
        sink = PostgresLiteratureStore(pool)
        await sink.ensure_schema()
        if args.dry_run:
            report = await literature_refilter_pass(
                pool,
                _NoLLM(),
                sink,
                max_items=args.limit,
                tier=args.tier,
                current_model=current_model,
                force=args.force,
                dry_run=True,
            )
            return header + report.render()
        async with httpx.AsyncClient(follow_redirects=True) as http:
            report = await literature_refilter_pass(
                pool,
                TierLLMClient(registry, http),
                sink,
                max_items=args.limit,
                tier=args.tier,
                current_model=current_model,
                force=args.force,
            )
        return header + report.render()
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="shrap-literature-refilter",
        description="Re-score already-filtered q-fin items under the current literature prompt.",
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
        default=300,
        help="Maximum items to re-score in this pass (default: 300)",
    )
    parser.add_argument(
        "--tier",
        default=TIER_LOCAL_CLASSIFICATION,
        help=f"LLM tier alias (default: {TIER_LOCAL_CLASSIFICATION})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-score the selection even when prompt version and model are both unchanged",
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
