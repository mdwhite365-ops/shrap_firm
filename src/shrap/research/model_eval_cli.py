"""Entrypoint for `shrap-model-eval` — ADR-0009's shadow-eval, made runnable.

Runs inside the tech-watcher container, which already carries the cloud routing
and the bearer token, the same way `shrap-tech-watcher-refilter` does:

    docker compose exec tech-watcher shrap-model-eval \\
        --models gpt-oss:20b-cloud,kimi-k2.5,deepseek-v4-flash \\
        --sample 30 --repeats 2 --dry-run

`--dry-run` costs nothing and prints the sample, the strata and the exact call
budget. Run it first — the eval draws on the same Ollama usage cap as the
research funnel, so the budget is a number worth seeing before it is spent.

Persistence is eval-only: `research.model_eval_runs` and
`research.model_eval_results`. This CLI never writes a production verdict, never
marks an item filtered, and never touches `filter_verdict_history` — a shadow
eval that fed its own experiments back into the corpus would corrupt every later
eval, invisibly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import structlog
from ulid import ULID

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.llm import TierLLMClient
from shrap.research.model_eval import (
    SELECT_EVAL_CORPUS_SQL,
    TASK_FILTER,
    CallResult,
    EvalPlan,
    EvalReport,
    build_eval_item,
    build_report,
    registry_for_model,
    render_markdown,
    run_plan,
    stratified_sample,
)
from shrap.research.tech_watcher.filter import (
    EXCLUDED_SOURCES,
    FILTER_PROMPT_VERSION,
)

log = structlog.get_logger(__name__)

DEFAULT_TIER = "local-classification"

CREATE_EVAL_RUNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.model_eval_runs (
    run_id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    tier TEXT NOT NULL,
    models JSONB NOT NULL,
    sample_size INTEGER NOT NULL,
    repeats INTEGER NOT NULL,
    seed INTEGER NOT NULL,
    prompt_version INTEGER NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    report_markdown TEXT NOT NULL
)
""".strip()

CREATE_EVAL_RESULTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.model_eval_results (
    run_id TEXT NOT NULL,
    model TEXT NOT NULL,
    item_id TEXT NOT NULL,
    repeat INTEGER NOT NULL,
    latency_ms DOUBLE PRECISION NOT NULL,
    parsed_ok BOOLEAN NOT NULL,
    relevant BOOLEAN,
    archetype TEXT,
    reason TEXT,
    error TEXT,
    raw TEXT,
    PRIMARY KEY (run_id, model, item_id, repeat)
)
""".strip()

INSERT_EVAL_RUN_SQL = """
INSERT INTO research.model_eval_runs (
    run_id, task, tier, models, sample_size, repeats, seed,
    prompt_version, started_at, finished_at, report_markdown
)
VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, $10, $11)
""".strip()

INSERT_EVAL_RESULT_SQL = """
INSERT INTO research.model_eval_results (
    run_id, model, item_id, repeat, latency_ms, parsed_ok,
    relevant, archetype, reason, error, raw
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
""".strip()


async def ensure_schema(pool: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS research")
        await conn.execute(CREATE_EVAL_RUNS_TABLE_SQL)
        await conn.execute(CREATE_EVAL_RESULTS_TABLE_SQL)


async def load_plan(
    pool: Any, *, models: Sequence[str], tier: str, sample: int, seed: int, repeats: int
) -> EvalPlan:
    async with pool.acquire() as conn:
        rows = await conn.fetch(SELECT_EVAL_CORPUS_SQL, sorted(EXCLUDED_SOURCES))
    items = [build_eval_item(row) for row in rows]
    return EvalPlan(
        task=TASK_FILTER,
        tier=tier,
        models=tuple(models),
        items=tuple(stratified_sample(items, sample, seed)),
        repeats=repeats,
        seed=seed,
    )


async def persist(
    pool: Any, run_id: str, report: EvalReport, results: Sequence[CallResult]
) -> None:
    plan = report.plan
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                INSERT_EVAL_RUN_SQL,
                run_id,
                plan.task,
                plan.tier,
                json.dumps(list(plan.models)),
                len(plan.items),
                plan.repeats,
                plan.seed,
                FILTER_PROMPT_VERSION,
                report.started_at,
                report.finished_at,
                render_markdown(report),
            )
            for r in results:
                await conn.execute(
                    INSERT_EVAL_RESULT_SQL,
                    run_id,
                    r.model,
                    r.item_id,
                    r.repeat,
                    r.latency_ms,
                    r.parsed_ok,
                    r.relevant,
                    r.archetype,
                    r.reason,
                    r.error,
                    r.raw,
                )


async def run(
    postgres_dsn: str,
    *,
    models: Sequence[str],
    tier: str,
    sample: int,
    seed: int,
    repeats: int,
    dry_run: bool,
    http_timeout: float = 120.0,
) -> tuple[EvalPlan, EvalReport | None]:
    pool = await create_asyncpg_pool(postgres_dsn)
    try:
        await ensure_schema(pool)
        plan = await load_plan(
            pool, models=models, tier=tier, sample=sample, seed=seed, repeats=repeats
        )
        if dry_run or not plan.items:
            return plan, None

        env = dict(os.environ)
        started_at = datetime.now(UTC)
        async with httpx.AsyncClient(timeout=http_timeout) as http:

            def factory(model: str) -> Any:
                return TierLLMClient(registry_for_model(env, tier, model), cast(Any, http))

            results = await run_plan(plan, factory)
        finished_at = datetime.now(UTC)

        report = build_report(plan, results, started_at, finished_at)
        await persist(pool, str(ULID()), report, results)
        return plan, report
    finally:
        await pool.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Shadow-eval candidate models against the production filter prompt "
            "(ADR-0009 Update Protocol). Writes eval-only tables; never a production verdict."
        )
    )
    parser.add_argument(
        "--models",
        required=True,
        metavar="A,B,C",
        help="Comma-separated model tags to compare. Put the incumbent first.",
    )
    parser.add_argument(
        "--tier", default=DEFAULT_TIER, help=f"Tier under test (default {DEFAULT_TIER})"
    )
    parser.add_argument(
        "--sample", type=int, default=30, help="Items to score per model (default 30)"
    )
    parser.add_argument(
        "--seed", type=int, default=7, help="Sampling seed; a rerun is the same eval"
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=2,
        help="Runs per item per model. 2+ measures self-consistency (default 2)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the sample, strata and exact call budget without spending any of it",
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Append the markdown block to this file (default: stdout only)",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    models = tuple(m.strip() for m in args.models.split(",") if m.strip())
    if len(models) < 2:
        parser.error("--models needs at least two tags: the incumbent and a candidate")
    if len(set(models)) != len(models):
        parser.error("--models contains a duplicate")
    if args.sample < 1 or args.repeats < 1:
        parser.error("--sample and --repeats must be >= 1")

    configure_logging("model-eval", os.environ.get("TECH_WATCHER_LOG_LEVEL", "INFO"))
    dsn = os.environ.get("TECH_WATCHER_POSTGRES_DSN")
    if not dsn:
        parser.error("TECH_WATCHER_POSTGRES_DSN is not set")

    plan, report = asyncio.run(
        run(
            dsn,
            models=models,
            tier=args.tier,
            sample=args.sample,
            seed=args.seed,
            repeats=args.repeats,
            dry_run=args.dry_run,
        )
    )

    print(plan.render())
    if not plan.items:
        print("\nno eligible items in research.raw_source_items — nothing to eval")
        return
    if report is None:
        print("\ndry run — no completions were made and nothing was written")
        return

    block = render_markdown(report)
    print("\n" + block)
    if args.out:
        path = Path(args.out)
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n" + block)
        print(f"appended to {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
