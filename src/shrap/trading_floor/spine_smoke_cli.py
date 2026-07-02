"""Entrypoint for `shrap-spine-smoke` (Card 15/16 live compose smoke)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import cast

from redis.asyncio import Redis

from shrap.common.db import create_asyncpg_pool
from shrap.trading_floor.spine_smoke import SmokeDb, SmokeRedis, run_spine_smoke

_DEFAULT_REDIS_URL = "redis" + "://" + "redis" + ":6379/0"
_DEFAULT_POSTGRES_DSN = "postgresql://shrap:shrap@postgres:5432/shrap"


def _default_postgres_dsn() -> str:
    for env_var in (
        "SPINE_SMOKE_POSTGRES_DSN",
        "PAPER_ORDER_STORE_POSTGRES_DSN",
        "RECONCILIATION_AGENT_POSTGRES_DSN",
    ):
        value = os.environ.get(env_var, "")
        if value:
            return value
    return _DEFAULT_POSTGRES_DSN


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shrap-spine-smoke",
        description="Inject one paper intent into the running compose stack and "
        "verify every service in the spine reacts (Card 15; --wait-fill and "
        "--wait-reconciliation add the Card 16 checks).",
    )
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("SPINE_SMOKE_REDIS_URL", _DEFAULT_REDIS_URL),
    )
    parser.add_argument("--postgres-dsn", default=_default_postgres_dsn())
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--side", default="buy", choices=["buy", "sell"])
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--event-timeout", type=float, default=60.0)
    parser.add_argument("--db-timeout", type=float, default=60.0)
    parser.add_argument("--wait-fill", action="store_true")
    parser.add_argument("--fill-timeout", type=float, default=300.0)
    parser.add_argument("--wait-reconciliation", action="store_true")
    parser.add_argument("--reconciliation-timeout", type=float, default=420.0)
    return parser


class _PoolDb:
    """Adapt an asyncpg pool to the smoke's narrow fetch interface."""

    def __init__(self, pool: object) -> None:
        self._pool = pool

    async def fetch(self, sql: str, *args: object) -> list[object]:
        async with self._pool.acquire() as conn:  # type: ignore[attr-defined]
            result = await conn.fetch(sql, *args)
            return cast("list[object]", result)


async def _run(args: argparse.Namespace) -> int:
    redis: Redis = Redis.from_url(args.redis_url, decode_responses=True, socket_timeout=30)
    pool = await create_asyncpg_pool(args.postgres_dsn)
    try:
        report = await run_spine_smoke(
            redis=cast(SmokeRedis, redis),
            db=cast(SmokeDb, _PoolDb(pool)),
            ticker=args.ticker,
            side=args.side,
            quantity=args.quantity,
            event_timeout_seconds=args.event_timeout,
            db_timeout_seconds=args.db_timeout,
            wait_fill=args.wait_fill,
            fill_timeout_seconds=args.fill_timeout,
            wait_reconciliation=args.wait_reconciliation,
            reconciliation_timeout_seconds=args.reconciliation_timeout,
        )
    finally:
        await redis.aclose()
        await pool.close()

    verdict = "SPINE SMOKE PASSED" if report.passed else "SPINE SMOKE FAILED"
    print(f"\n{verdict} ({sum(c.passed for c in report.checks)}/{len(report.checks)} checks)")
    return 0 if report.passed else 1


def main() -> None:
    args = _build_parser().parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
