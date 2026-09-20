"""Entrypoint for ``shrap-bar-experiment`` — timeline card 1.4, made runnable.

Runs inside the tech-watcher container, which already carries the cloud routing
and the bearer token::

    docker compose exec tech-watcher shrap-bar-experiment --dry-run
    docker compose exec tech-watcher shrap-bar-experiment --limit 300

``--dry-run`` costs nothing and prints the corpus shape, the exact call budget
and **both Ollama usage windows**. Run it first — the full corpus across three
bars is tens of thousands of calls.

**The quota is account-wide and the session window is the one that stops runs.**
On 2026-09-20 a 599-item replay was refused after 192 items with the weekly
window at 27.7% and the session window at 100%, and the Tech Watcher's hourly
literature pass — which draws on the same account — aborted with ``scored: 0``
behind it. This CLI now reads ``ollama.com/api/usage`` before starting and
refuses to begin inside the reserve held for the always-on agents
(``--reserve``, ``--ignore-quota``).

When a run is stopped anyway, ``--resume-run RUN_ID`` scores only the items that
run recorded an error against and writes them into the same run id, so the
experiment ends as one comparable set of rows without paying again for the items
that succeeded.

Persistence is experiment-only: ``research.bar_experiment_runs`` and
``research.bar_experiment_results``. This CLI never writes ``filter_result``,
never marks ``filtered_at``, never touches ``filter_verdict_history``, and in
particular does **not** re-filter the two v3 control items — the card spec says
to leave them alone until this has run, because they are the only items any
model has ever admitted.
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
from shrap.llm import TierLLMClient, TierRegistry, tracer_from_env
from shrap.llm.ollama_usage import (
    PRODUCTION_RESERVE,
    UsageSnapshot,
    check_budget,
    fetch_usage,
)
from shrap.research.bar_experiment import (
    Bar,
    BarCall,
    ExperimentReport,
    bars_by_key,
    cross_bar_agreement,
    render_markdown,
    run_bar,
    stratified_limit,
    summarize,
)
from shrap.research.tech_watcher.filter import EXCLUDED_SOURCES, UnfilteredItem

log = structlog.get_logger(__name__)

DEFAULT_TIER = "local-classification"

# Generous: a cloud tier occasionally takes tens of seconds, and a timeout here
# costs a whole item rather than a retry.
HTTP_TIMEOUT_SECONDS = 120.0

SELECT_CORPUS_SQL = """
SELECT item_id, source, kind, title, summary
FROM research.raw_source_items
WHERE NOT (source = ANY($1::text[]))
ORDER BY item_id
""".strip()

# The exact items an earlier run scored, so a later run can change ONE variable.
#
# **Why this exists.** The 2026-07-31 control ran 599 items on `qwen3.5:397b`.
# The corpus is now 21,231, so any fresh sample compared against that run varies
# the model *and* the corpus at once — the confound #247 exists to warn about.
# Replaying the item set holds the corpus fixed and leaves the model as the only
# difference.
SELECT_RUN_ITEM_IDS_SQL = """
SELECT DISTINCT item_id
FROM research.bar_experiment_results
WHERE run_id = $1
""".strip()

# The items a run tried and failed, so a run stopped by a spent quota can be
# finished rather than repeated. On 2026-09-20 run 01M2YHEGZ5KSBAADGHYK96QGAY
# scored 192 of 599 and recorded 407 HTTP 429s; re-running the whole set would
# have paid for the 192 again.
SELECT_RUN_ERROR_ITEM_IDS_SQL = """
SELECT DISTINCT item_id
FROM research.bar_experiment_results
WHERE run_id = $1 AND error IS NOT NULL
""".strip()

CREATE_RUNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.bar_experiment_runs (
    run_id TEXT PRIMARY KEY,
    tier TEXT NOT NULL,
    model TEXT NOT NULL,
    bars JSONB NOT NULL,
    corpus_size INTEGER NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    report_markdown TEXT NOT NULL
)
""".strip()

CREATE_RESULTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.bar_experiment_results (
    run_id TEXT NOT NULL,
    bar TEXT NOT NULL,
    item_id TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    admitted BOOLEAN,
    label TEXT,
    reason TEXT,
    parsed_ok BOOLEAN,
    latency_ms DOUBLE PRECISION NOT NULL,
    error TEXT,
    raw TEXT,
    PRIMARY KEY (run_id, bar, item_id)
)
""".strip()

INSERT_RUN_SQL = """
INSERT INTO research.bar_experiment_runs (
    run_id, tier, model, bars, corpus_size, started_at, finished_at, report_markdown
)
VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
""".strip()

# **The conflict clause heals, it never overwrites.** A resumed run re-scores the
# items that errored and writes into the same ``run_id``, so the run ends as one
# coherent set of rows rather than two halves under two ids. The
# ``WHERE ... error IS NOT NULL`` guard is what keeps that safe: a row that
# already carries a verdict is left exactly as recorded. Without it a resume
# would quietly replace measurements with fresh ones from a different hour and a
# possibly different model, which is this project's oldest defect — a
# recomputation overwriting a recorded fact.
INSERT_RESULT_SQL = """
INSERT INTO research.bar_experiment_results (
    run_id, bar, item_id, source, title, admitted, label, reason,
    parsed_ok, latency_ms, error, raw
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
ON CONFLICT (run_id, bar, item_id) DO UPDATE SET
    source = EXCLUDED.source,
    title = EXCLUDED.title,
    admitted = EXCLUDED.admitted,
    label = EXCLUDED.label,
    reason = EXCLUDED.reason,
    parsed_ok = EXCLUDED.parsed_ok,
    latency_ms = EXCLUDED.latency_ms,
    error = EXCLUDED.error,
    raw = EXCLUDED.raw
WHERE research.bar_experiment_results.error IS NOT NULL
""".strip()


async def ensure_schema(pool: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS research")
        await conn.execute(CREATE_RUNS_TABLE_SQL)
        await conn.execute(CREATE_RESULTS_TABLE_SQL)


async def load_corpus(
    pool: Any,
    limit: int | None,
    items_from_run: str | None = None,
    resume_run: str | None = None,
) -> list[UnfilteredItem]:
    """The items to score, in corpus order.

    Three selectors, mutually exclusive and checked by the caller:
    ``limit`` samples proportionally, ``items_from_run`` replays another run's
    item set, and ``resume_run`` takes only the items a run recorded an error
    against.
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(SELECT_CORPUS_SQL, sorted(EXCLUDED_SOURCES))
        replay: set[str] | None = None
        if items_from_run:
            replay = {
                str(r["item_id"]) for r in await conn.fetch(SELECT_RUN_ITEM_IDS_SQL, items_from_run)
            }
            if not replay:
                raise SystemExit(f"run {items_from_run!r} has no recorded results to replay")
        elif resume_run:
            replay = {
                str(r["item_id"])
                for r in await conn.fetch(SELECT_RUN_ERROR_ITEM_IDS_SQL, resume_run)
            }
            if not replay:
                raise SystemExit(
                    f"run {resume_run!r} has no errored results to resume — "
                    "either it finished cleanly or the run id is wrong"
                )
    items = [
        UnfilteredItem(
            item_id=str(row["item_id"]),
            source=str(row["source"]),
            kind=None if row["kind"] is None else str(row["kind"]),
            title=str(row["title"]),
            summary=None if row["summary"] is None else str(row["summary"]),
        )
        for row in rows
    ]
    if replay is not None:
        kept = [item for item in items if item.item_id in replay]
        # An item scored then and absent now would silently shrink the panel and
        # make the two runs incomparable in exactly the way replaying is meant to
        # prevent. `raw_source_items` is append-only, so this should be zero.
        missing = len(replay) - len(kept)
        if missing:
            print(f"warning: {missing} of {len(replay)} replayed items are no longer in the corpus")
        return kept

    # Proportional, never a head-of-list slice. The corpus is ordered by
    # item_id and `arxiv:` sorts first, so `items[:600]` is 600 arXiv items and
    # a hard-leg count taken from it is an artifact of the ordering.
    return stratified_limit(items, limit) if limit else items


async def persist_results(pool: Any, run_id: str, calls: Sequence[BarCall]) -> None:
    """Write one bar's rows.

    Separate from the run row so a long experiment can checkpoint: the full
    corpus is ~5,200 items and a single bar takes hours, so persisting only at
    the end would mean a dropped session at hour seven loses everything.
    """

    async with pool.acquire() as conn:
        async with conn.transaction():
            for call in calls:
                verdict = call.verdict
                await conn.execute(
                    INSERT_RESULT_SQL,
                    run_id,
                    call.bar,
                    call.item.item_id,
                    call.item.source,
                    call.item.title[:500],
                    None if verdict is None else verdict.admitted,
                    None if verdict is None else verdict.label,
                    None if verdict is None else verdict.reason,
                    None if verdict is None else verdict.parsed_ok,
                    call.latency_ms,
                    call.error,
                    call.raw[:4000],
                )


async def persist_run(
    pool: Any, run_id: str, report: ExperimentReport, bars: Sequence[Bar]
) -> None:
    """Write the run row once every bar has finished and been checkpointed."""

    async with pool.acquire() as conn:
        await conn.execute(
            INSERT_RUN_SQL,
            run_id,
            report.tier,
            report.model,
            json.dumps([bar.key for bar in bars]),
            report.corpus_size,
            datetime.fromisoformat(report.started_at),
            datetime.fromisoformat(report.finished_at),
            render_markdown(report),
        )


def render_plan(
    items: Sequence[UnfilteredItem],
    bars: Sequence[Bar],
    usage: UsageSnapshot | None = None,
) -> str:
    by_source: dict[str, int] = {}
    for item in items:
        by_source[item.source] = by_source.get(item.source, 0) + 1
    lines = [
        f"corpus: {len(items)} items across {len(by_source)} sources",
        "  " + ", ".join(f"{src}={count}" for src, count in sorted(by_source.items())),
        f"bars: {len(bars)} — " + ", ".join(bar.key for bar in bars),
        f"call budget: {len(items) * len(bars)} completions "
        f"({len(items)} items x {len(bars)} bars)",
    ]
    # The budget above is meaningless without the allowance it is spent from.
    # Printing the completions alone is what let a 599-item run be planned
    # against a weekly window that had plenty left and a session window that
    # had nothing.
    lines.append(
        usage.describe() if usage is not None else "ollama usage: unavailable (proceeding blind)"
    )
    return "\n".join(lines)


async def run(
    dsn: str,
    *,
    bar_keys: Sequence[str] | None,
    tier: str,
    limit: int | None,
    dry_run: bool,
    items_from_run: str | None = None,
    resume_run: str | None = None,
    reserve: float = PRODUCTION_RESERVE,
    ignore_quota: bool = False,
) -> tuple[str, ExperimentReport | None, list[BarCall], tuple[Bar, ...]]:
    bars = bars_by_key(bar_keys)
    pool = await create_asyncpg_pool(dsn)
    try:
        await ensure_schema(pool)
        items = await load_corpus(pool, limit, items_from_run, resume_run)

        env = dict(os.environ)
        registry = TierRegistry(env)
        binding = registry.resolve(tier)
        model = binding.model

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as probe:
            usage = await fetch_usage(cast(Any, probe), binding.api_key)
        print(render_plan(items, bars, usage))

        if dry_run or not items:
            return "", None, [], bars

        # The quota is account-wide: the Tech Watcher's hourly literature pass
        # draws on the same windows. On 2026-09-20 this experiment spent the
        # session allowance and the production filter aborted with `scored: 0`.
        verdict = check_budget(usage, reserve=reserve)
        if not verdict.ok:
            if not ignore_quota:
                raise SystemExit(
                    f"refusing to start: {verdict.reason}\n"
                    "Wait for the window to reset, or pass --ignore-quota to "
                    "spend the reserve the always-on agents run on."
                )
            print(f"warning: starting anyway — {verdict.reason}")

        started_at = datetime.now(UTC)
        # A resume heals the original run in place rather than minting a second
        # id, so the experiment ends as one comparable set of rows.
        run_id = resume_run or str(ULID())
        calls: list[BarCall] = []
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as http:
            client = TierLLMClient(
                registry, cast(Any, http), tracer=tracer_from_env(env, cast(Any, http))
            )
            for bar in bars:
                log.info("bar_experiment.bar_started", bar=bar.key, items=len(items))
                bar_calls = await run_bar(bar, cast(Any, client), items, tier)
                calls.extend(bar_calls)
                # Checkpoint per bar. The full corpus is ~5,200 items and a bar
                # takes hours; persisting only at the end means a dropped
                # session at hour seven loses everything. Rows land as each bar
                # finishes, so a failure costs one bar, not three.
                await persist_results(pool, run_id, bar_calls)
                log.info(
                    "bar_experiment.bar_persisted",
                    bar=bar.key,
                    run_id=run_id,
                    rows=len(bar_calls),
                )

        summaries = tuple(summarize(bar, [c for c in calls if c.bar == bar.key]) for bar in bars)
        report = ExperimentReport(
            corpus_size=len(items),
            tier=tier,
            model=model,
            summaries=summaries,
            started_at=started_at.isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            _agreement=cross_bar_agreement(summaries),
        )
        await persist_run(pool, run_id, report, bars)
        return run_id, report, calls, bars
    finally:
        await pool.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shrap-bar-experiment",
        description="Score the corpus under three archetype-bar formulations.",
    )
    parser.add_argument(
        "--bars",
        default=None,
        metavar="A,B,C",
        help="Comma-separated bar keys (default: all three)",
    )
    parser.add_argument("--tier", default=DEFAULT_TIER, help=f"Tier (default {DEFAULT_TIER})")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Score only the first N items. The card spec says run the full corpus — "
        "a sample reproduces the thin-positive problem every eval has carried.",
    )
    parser.add_argument(
        "--items-from-run",
        default=None,
        metavar="RUN_ID",
        help=(
            "Score exactly the items an earlier run scored, so the model is the "
            "only variable. Ignores --limit."
        ),
    )
    parser.add_argument(
        "--resume-run",
        default=None,
        metavar="RUN_ID",
        help=(
            "Score only the items that run recorded an ERROR against, writing "
            "into the same run id. For finishing a run a spent quota or a dead "
            "endpoint stopped, without paying again for the items that "
            "succeeded. Rows already carrying a verdict are never overwritten."
        ),
    )
    parser.add_argument(
        "--reserve",
        type=float,
        default=PRODUCTION_RESERVE,
        metavar="FRACTION",
        help=(
            f"Fraction of the binding Ollama window to leave for the always-on "
            f"agents, which share this account's quota (default "
            f"{PRODUCTION_RESERVE:.2f})"
        ),
    )
    parser.add_argument(
        "--ignore-quota",
        action="store_true",
        help=(
            "Start even when the binding window is inside the reserve. This "
            "spends the allowance the Tech Watcher's hourly filter runs on."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the corpus shape, the call budget and both Ollama usage "
            "windows without spending any of it"
        ),
    )
    parser.add_argument("--out", default=None, metavar="PATH", help="Append the block to a file")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.items_from_run and args.resume_run:
        parser.error("--items-from-run and --resume-run select different item sets; pass one")

    bar_keys = [b.strip() for b in args.bars.split(",")] if args.bars else None
    configure_logging("bar-experiment", os.environ.get("TECH_WATCHER_LOG_LEVEL", "INFO"))
    dsn = os.environ.get("TECH_WATCHER_POSTGRES_DSN")
    if not dsn:
        parser.error("TECH_WATCHER_POSTGRES_DSN is not set")

    run_id, report, _calls, _bars = asyncio.run(
        run(
            dsn,
            bar_keys=bar_keys,
            tier=args.tier,
            limit=args.limit,
            dry_run=args.dry_run,
            items_from_run=args.items_from_run,
            resume_run=args.resume_run,
            reserve=args.reserve,
            ignore_quota=args.ignore_quota,
        )
    )
    if report is None:
        print("\ndry run — no completions were made and nothing was written")
        return

    block = render_markdown(report)
    print("\n" + block)
    print(f"\nrun_id {run_id} — full results in research.bar_experiment_results", file=sys.stderr)
    if args.out:
        path = Path(args.out)
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write("\n" + block)
        except OSError as exc:
            # Same lesson as the model eval: the run already cost its share of
            # the cap and is persisted, so a write failure must not end it on a
            # traceback that reads like the experiment failed.
            print(
                f"\ncould not append to {path}: {exc}\n"
                "The run completed and is persisted — copy the block above.",
                file=sys.stderr,
            )
            return
        print(f"appended to {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
