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
import json
import os
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from shrap.research.dimensions import render as dimension_survey
from shrap.research.dimensions import survey
from shrap.research.guidance import derive
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
        aggregate_metrics, active_metrics, consistency_metrics, config,
        card_path, created_at
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
    s.spec,
    s.tickers,
    s.created_at AS registered_at,
    t.attempts,
    n.verdict, n.reason, n.protocol_version, n.total_trades,
    n.aggregate_metrics, n.active_metrics, n.consistency_metrics,
    n.card_path, n.created_at
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


async def read_ledger(pool: Pool) -> tuple[list[LedgerRow], list[dict[str, Any]]]:
    """Return the flattened rows and the raw ones.

    Guidance reads a strategy's SPEC — the rule, the params, the universe — which
    the flattened LedgerRow deliberately does not carry. Two shapes from one
    query rather than two queries, so the ledger and the guidance can never
    describe different corpora.
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(LEDGER_SQL)
    flattened = [row_from_mapping(row) for row in rows]
    raw = [
        {
            "strategy_id": r["strategy_id"],
            "name": r["name"],
            "spec": _decoded(r.get("spec")),
            "tickers": _decoded(r.get("tickers")),
            "information_ratio": f.information_ratio,
            "tested": f.engine_ran and not f.is_structural,
        }
        for r, f in zip(rows, flattened, strict=True)
    ]
    return flattened, raw


def _decoded(value: Any) -> Any:
    """jsonb arrives as text; the ledger learned this the hard way (PR #152)."""

    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value or {}


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
        rows, raw = await read_ledger(pool)
    finally:
        await pool.close()
    summary = summarise(rows, sharpe_floor=args.sharpe_floor, ir_floor=args.ir_floor)
    out = render(rows, summary)
    if args.guidance:
        out += "\n\nWHAT TO TRY NEXT\n" + derive(raw).render()
    if args.dimensions:
        specs = [r.get("spec") for r in raw if r.get("spec") is not None]
        out += "\n\nAXES\n" + dimension_survey(survey(specs))
    return out


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
        "--guidance",
        action="store_true",
        help="Also derive what the corpus says to try next (informs proposals, never gates)",
    )
    parser.add_argument(
        "--dimensions",
        action="store_true",
        help=(
            "Also survey every axis the engine accepts against what the corpus has "
            "chosen — read off the strategy dataclasses, not a hand-written list"
        ),
    )
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
