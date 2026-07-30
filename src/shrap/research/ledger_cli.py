"""``shrap-research-ledger`` — what the firm has tried and what it learned.

Reads ``research.strategies`` left-joined to each strategy's newest
``research.evaluations`` row. Read-only: it owns no table and writes nothing.

The join is deliberately LEFT. A strategy with no evaluation is part of the
corpus — it is a hypothesis the firm proposed and has not yet tested, and
dropping it would make the ledger describe only the work that got finished.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from shrap.research.ledger import (
    LedgerRow,
    render,
    row_from_mapping,
    summarise,
)
from shrap.research.strategy_evaluator.engine import (
    DEFAULT_INFORMATION_RATIO_FLOOR,
    DEFAULT_SHARPE_FLOOR,
)

# DISTINCT ON gives the newest evaluation per strategy; the LEFT JOIN keeps
# strategies that have never been evaluated. `attempts` is derived from
# lineage_root_id in the same pass rather than by a query per strategy.
LEDGER_SQL = """
WITH newest AS (
    SELECT DISTINCT ON (strategy_id)
        strategy_id, verdict, reason, protocol_version, total_trades,
        aggregate_metrics, active_metrics, config, card_path, created_at
    FROM research.evaluations
    ORDER BY strategy_id, created_at DESC
),
tries AS (
    SELECT coalesce(lineage_root_id, strategy_id) AS root, count(*) AS attempts
    FROM research.strategies
    GROUP BY 1
)
SELECT
    s.strategy_id,
    s.name,
    s.status,
    s.created_at AS registered_at,
    t.attempts,
    n.verdict, n.reason, n.protocol_version, n.total_trades,
    n.aggregate_metrics, n.active_metrics, n.card_path, n.created_at
FROM research.strategies s
LEFT JOIN newest n ON n.strategy_id = s.strategy_id
LEFT JOIN tries t ON t.root = coalesce(s.lineage_root_id, s.strategy_id)
ORDER BY s.created_at
""".strip()


class Connection(Protocol):
    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> Connection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class Pool(Protocol):
    def acquire(self) -> AcquireContext: ...


async def read_ledger(pool: Pool) -> list[LedgerRow]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(LEDGER_SQL)
    return [row_from_mapping(row) for row in rows]


def _dsn(explicit: str | None) -> str:
    dsn = (
        explicit
        or os.environ.get("STRATEGY_EVALUATOR_POSTGRES_DSN")
        or os.environ.get("STRATEGY_SEED_POSTGRES_DSN")
        or ""
    )
    if not dsn:
        raise SystemExit(
            "refused: no Postgres DSN. Pass --dsn or set STRATEGY_EVALUATOR_POSTGRES_DSN."
        )
    return dsn


async def _run(args: argparse.Namespace) -> str:
    from shrap.common.db import create_asyncpg_pool

    pool = await create_asyncpg_pool(_dsn(args.dsn))
    try:
        rows = await read_ledger(pool)
    finally:
        await pool.close()
    summary = summarise(rows, sharpe_floor=args.sharpe_floor, ir_floor=args.ir_floor)
    return render(rows, summary)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shrap-research-ledger",
        description=(
            "Every strategy the firm has tried, what happened, and what the corpus "
            "supports. Read-only."
        ),
    )
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: env)")
    parser.add_argument(
        "--sharpe-floor",
        type=float,
        default=DEFAULT_SHARPE_FLOOR,
        help=(
            f"Sharpe promote floor to measure the corpus against (default {DEFAULT_SHARPE_FLOOR})"
        ),
    )
    parser.add_argument(
        "--ir-floor",
        type=float,
        default=DEFAULT_INFORMATION_RATIO_FLOOR,
        help=(
            "Information-ratio promote floor to measure the corpus against "
            f"(default {DEFAULT_INFORMATION_RATIO_FLOOR})"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    print(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
