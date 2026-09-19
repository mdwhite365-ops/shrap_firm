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
from shrap.market_data.store import DEFAULT_BAR_SOURCE
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
    IntradayEvaluatorReader,
    PostgresEvaluationStore,
    PostgresEvaluatorReader,
)
from shrap.research.strategy_registry import PostgresStrategyRegistry

# The token meaning "read market_data.daily_bars", i.e. the behaviour this CLI
# had before intraday panels existed. Matches Alpaca's own daily token and
# `strategy_runner.cadence.alpaca_timeframe`, which returns the same string for a
# daily cadence.
TIMEFRAME_DAILY = "1Day"


def _check_intraday_window(args: argparse.Namespace) -> None:
    """Refuse an unbounded lookback at an intraday grain.

    ``--window-years`` defaults to None, meaning "every bar in the store", which
    is right for daily bars and ruinous below them. One ticker-year is ~252 rows
    daily, ~6,500 at 15Min and ~98,000 at 1Min; across a fifty-name launch list
    an unbounded 1Min request is millions of rows per year of history.

    A refusal rather than a quiet default, because the caller is the only one who
    knows how much history they actually backfilled — and a silently truncated
    window would produce a verdict on a different panel than the one they
    believe they asked for.
    """

    if args.timeframe == TIMEFRAME_DAILY or args.window_years is not None:
        return
    raise EvaluationError(
        f"--timeframe {args.timeframe} needs an explicit --window-years. "
        "An unbounded lookback is fine for daily bars and is not fine below them: "
        "one ticker-year is ~252 daily rows but ~6,500 at 15Min and ~98,000 at 1Min. "
        "Pass the window you actually backfilled."
    )


async def _run(args: argparse.Namespace) -> str:
    from redis.asyncio import Redis

    redis = Redis.from_url(args.redis_url, decode_responses=True, socket_timeout=30)
    pool = await create_asyncpg_pool(args.dsn)
    store = PostgresEvaluationStore(pool)
    reader: PostgresEvaluatorReader | IntradayEvaluatorReader
    if args.timeframe == TIMEFRAME_DAILY:
        reader = PostgresEvaluatorReader(pool, source=args.bar_source)
    else:
        reader = IntradayEvaluatorReader(
            pool,
            timeframe=args.timeframe,
            include_extended=args.include_extended,
            source=args.bar_source,
        )
    registry = PostgresStrategyRegistry(pool)
    publisher = EventPublisher(cast(RedisPublisher, redis))
    # `bar_source` is set from the same argument that built the reader above.
    # One value, used twice — the reader enforces the feed and the config
    # records it, and they cannot disagree because neither is derived
    # independently. A test pins that.
    config = EvalConfig(
        n_folds=args.folds,
        window_years=args.window_years,
        min_trades=args.min_trades,
        sharpe_floor=args.sharpe_floor,
        bar_source=args.bar_source,
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
        "--timeframe",
        default=TIMEFRAME_DAILY,
        metavar="1Day|15Min|5Min|...",
        help=(
            f"Bar grain to backtest on (default {TIMEFRAME_DAILY}, reading "
            "market_data.daily_bars). Any other token reads market_data.intraday_bars "
            "and must match the token the backfill stored. Intraday requires "
            "--window-years, and regular trading hours only unless --include-extended."
        ),
    )
    parser.add_argument(
        "--include-extended",
        action="store_true",
        help=(
            "Include pre/post-market bars in an intraday panel. Off by default: the "
            "backfill stores 04:00-20:00, where a handful of shares sets a price no "
            "strategy could have traded at size."
        ),
    )
    parser.add_argument(
        "--bar-source",
        default=DEFAULT_BAR_SOURCE,
        help=(
            "Which stored feed to backtest against, e.g. alpaca-iex (default) "
            "or alpaca-sip. The panel is built from this feed alone; a run "
            "cannot mix them."
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
        _check_intraday_window(args)
        output = asyncio.run(_run(args))
    except EvaluationError as e:
        raise SystemExit(f"refused: {e}") from e
    print(output)


if __name__ == "__main__":
    main()
