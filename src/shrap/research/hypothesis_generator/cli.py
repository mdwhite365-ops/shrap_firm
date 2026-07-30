"""``shrap-hypothesis-generate`` — read the literature, propose what can be run.

    shrap-hypothesis-generate --gaps                 # the build queue, no model calls
    shrap-hypothesis-generate --dry-run --limit 5    # propose, write nothing
    shrap-hypothesis-generate --limit 25             # for real
    shrap-hypothesis-generate --from-file papers.json --dry-run

``--from-file`` exists because this agent's feed is a card that has not shipped:
Tech Watcher does not read arXiv ``q-fin`` yet. A JSON list of items runs the
whole proposer end to end against real abstracts today, which is what makes the
prompt reviewable before anything depends on it. The file format is the same
shape the table holds:

    [{"item_id": "arxiv:2401.00001", "source": "arxiv", "title": "...",
      "abstract": "...", "url": "https://...", "category": "q-fin.PM"}]

``--dry-run`` calls the model and writes nothing — no strategies, no gaps, no
item marked processed. It is the honest way to read a prompt change, because the
refusals are the output that matters and they cost the same either way.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from shrap.llm.registry import TIER_LOCAL_HEAVY
from shrap.research.hypothesis_generator.generator import HypothesisGenerator
from shrap.research.hypothesis_generator.literature import (
    LiteratureItem,
    PostgresLiteratureStore,
)
from shrap.research.hypothesis_generator.store import PostgresGapStore, render_queue

DEFAULT_LIMIT = 25


def _dsn(explicit: str | None) -> str:
    dsn = (
        explicit
        or os.environ.get("HYPOTHESIS_GENERATOR_POSTGRES_DSN")
        or os.environ.get("STRATEGY_EVALUATOR_POSTGRES_DSN")
        or os.environ.get("TECH_WATCHER_POSTGRES_DSN")
        or ""
    )
    if not dsn:
        raise SystemExit(
            "refused: no Postgres DSN. Pass --dsn or set HYPOTHESIS_GENERATOR_POSTGRES_DSN."
        )
    return dsn


def items_from_file(path: Path) -> list[LiteratureItem]:
    """Parse a hand-assembled item list. Strict: a bad entry is a hard error.

    Silently skipping a malformed entry would let a typo shrink the run to
    nothing while it still reported success.
    """

    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit(f"refused: {path} does not hold a JSON list of items")
    items: list[LiteratureItem] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise SystemExit(f"refused: entry {index} in {path} is not an object")
        try:
            published = entry.get("published_at")
            items.append(
                LiteratureItem(
                    item_id=str(entry["item_id"]),
                    source=str(entry.get("source", "manual")),
                    title=str(entry["title"]),
                    abstract=str(entry["abstract"]),
                    url=None if entry.get("url") is None else str(entry["url"]),
                    published_at=(datetime.fromisoformat(str(published)) if published else None),
                    category=None if entry.get("category") is None else str(entry["category"]),
                )
            )
        except KeyError as e:
            raise SystemExit(f"refused: entry {index} in {path} has no {e}") from e
    return items


async def _run(args: argparse.Namespace) -> str:
    from shrap.common.db import create_asyncpg_pool
    from shrap.llm import TierLLMClient, TierRegistry
    from shrap.research.strategy_registry import PostgresStrategyRegistry

    pool = await create_asyncpg_pool(_dsn(args.dsn))
    try:
        literature = PostgresLiteratureStore(pool)
        gaps = PostgresGapStore(pool)
        await literature.ensure_schema()
        await gaps.ensure_schema()

        # Read-only path: no model, no writes, no literature read.
        if args.gaps:
            return render_queue(await gaps.ranked())

        items = (
            items_from_file(Path(args.from_file))
            if args.from_file
            else await literature.pending(args.limit)
        )
        registry = PostgresStrategyRegistry(pool)
        async with httpx.AsyncClient(follow_redirects=True) as http:
            generator = HypothesisGenerator(
                llm=TierLLMClient(TierRegistry(dict(os.environ)), http),
                registry=registry,
                literature=literature,
                gaps=gaps,
                tier=args.tier,
                dry_run=args.dry_run,
            )
            report = await generator.run(items[: args.limit])
        return report.render()
    finally:
        await pool.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shrap-hypothesis-generate",
        description=(
            "Turn published market effects into strategy proposals, and record what "
            "the firm would have to build to test the rest."
        ),
    )
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: env)")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum literature items to read this pass (default: {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--tier",
        default=TIER_LOCAL_HEAVY,
        help=f"LLM tier alias (default: {TIER_LOCAL_HEAVY})",
    )
    parser.add_argument(
        "--from-file",
        default=None,
        help="Read items from a JSON file instead of the literature table",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Call the model and report, but register nothing and mark nothing",
    )
    parser.add_argument(
        "--gaps",
        action="store_true",
        help="Print the capability build queue and exit; no model calls, no writes",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    print(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
