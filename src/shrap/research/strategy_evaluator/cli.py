"""On-demand CLI: ``shrap-strategy-evaluate`` (Evaluator first card).

There is no overnight queue runner or event trigger in this card (deferred).
This CLI is how a Mike-seeded ``hypothesis``-stage strategy gets evaluated for
now — end to end: spec hygiene, anchor freshness, walk-forward + friction
stress, verdict, and (unless ``--dry-run``) the registry transition, the
``research.evaluations`` row, the evaluation card, and the verdict events.

On the ``shrap-universe-promote`` / ``shrap-tech-watcher-promote`` CLI
precedent (PR #54, #75): plain argparse with env-var defaults, no long-running
loop. A refusal (missing strategy, wrong stage, or a spec-hygiene failure such
as the deferred ``bottleneck-rotation`` archetype) exits non-zero with an
explicit reason; nothing is written.

    docker compose run --rm strategy-evaluator \\
        shrap-strategy-evaluate --strategy-id 01STRAT... [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import cast

from shrap.common.db import create_asyncpg_pool
from shrap.events import EventPublisher, RedisPublisher
from shrap.research.strategy_evaluator.engine import (
    DEFAULT_FOLDS,
    DEFAULT_MIN_TRADES,
    DEFAULT_SHARPE_FLOOR,
    DEFAULT_WINDOW_YEARS,
    EvalConfig,
)
from shrap.research.strategy_evaluator.pipeline import (
    DEFAULT_TRIGGER,
    EvaluationError,
    EvaluationPipeline,
)
from shrap.research.strategy_evaluator.store import (
    PostgresEvaluationStore,
    PostgresEvaluatorReader,
)
from shrap.research.strategy_registry import PostgresStrategyRegistry


async def _run(args: argparse.Namespace) -> str:
    from redis.asyncio import Redis

    redis = Redis.from_url(args.redis_url, decode_responses=True, socket_timeout=30)
    pool = await create_asyncpg_pool(args.dsn)
    store = PostgresEvaluationStore(pool)
    reader = PostgresEvaluatorReader(pool)
    registry = PostgresStrategyRegistry(pool)
    publisher = EventPublisher(cast(RedisPublisher, redis))
    config = EvalConfig(
        n_folds=args.folds,
        window_years=args.window_years,
        min_trades=args.min_trades,
        sharpe_floor=args.sharpe_floor,
    )
    pipeline = EvaluationPipeline(
        registry=registry,
        reader=reader,
        store=store,
        publisher=publisher,
        config=config,
        card_root=Path(args.card_root),
    )
    try:
        outcome = await pipeline.evaluate(args.strategy_id, trigger=args.trigger)
        if args.dry_run:
            return f"{outcome.summary()}\nDRY RUN — nothing persisted, no transition, no events."
        await store.ensure_schema()
        result = await pipeline.commit(outcome)
        return (
            f"{outcome.summary()}\n"
            f"evaluation_id={result.evaluation_id} "
            f"transitioned={result.transitioned} to_stage={result.to_stage} "
            f"card={result.card_path} streams={','.join(result.streams)}"
        )
    finally:
        await redis.aclose()
        await pool.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate one hypothesis-stage strategy end to end: walk-forward + "
            "friction stress + verdict, promoting to paper or killing on the "
            "strategy registry. Anchor freshness applies only to archetypes "
            "whose policy requires an anchor, and is checked against "
            "research.world_changers only (bottleneck leg deferred)."
        )
    )
    parser.add_argument("--strategy-id", required=True, help="research.strategies strategy_id")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the verdict without persisting, transitioning, or publishing",
    )
    parser.add_argument("--trigger", default=DEFAULT_TRIGGER, help="Recorded trigger label")
    parser.add_argument(
        "--sharpe-floor",
        type=float,
        default=DEFAULT_SHARPE_FLOOR,
        help=f"Sharpe promote floor, calibration-pending (default {DEFAULT_SHARPE_FLOOR})",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=DEFAULT_MIN_TRADES,
        help=f"Trade-count gate across the walk-forward (default {DEFAULT_MIN_TRADES})",
    )
    parser.add_argument(
        "--folds",
        type=int,
        default=DEFAULT_FOLDS,
        help=f"Walk-forward folds (default {DEFAULT_FOLDS})",
    )
    parser.add_argument(
        "--window-years",
        type=int,
        default=None,
        help=(
            "Cap the backtest lookback to N years. Omitted, every bar in the "
            "store is used — which is the point of backfilling deeper than "
            f"{DEFAULT_WINDOW_YEARS} years. Pass a number only to deliberately "
            "restrict a run to a recent window."
        ),
    )
    parser.add_argument(
        "--card-root",
        default=os.environ.get("STRATEGY_EVALUATOR_CARD_ROOT", "docs/strategies/evaluations"),
        help="Root directory for evaluation cards",
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get(
            "STRATEGY_EVALUATOR_POSTGRES_DSN", "postgresql://shrap:shrap@postgres:5432/shrap"
        ),
        help="Postgres DSN (default: STRATEGY_EVALUATOR_POSTGRES_DSN env)",
    )
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("STRATEGY_EVALUATOR_REDIS_URL", "redis://redis:6379/0"),
        help="Redis URL (default: STRATEGY_EVALUATOR_REDIS_URL env)",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        output = asyncio.run(_run(args))
    except EvaluationError as e:
        raise SystemExit(f"refused: {e}") from e
    print(output)


if __name__ == "__main__":
    main()
