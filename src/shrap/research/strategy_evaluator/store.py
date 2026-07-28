"""Persistence for the Evaluator: the ``research.evaluations`` writer and the
read-only readers for the tables it consumes but does not own.

Two responsibilities, deliberately split by ownership (mirrors the Pre-Trade
Checker's "reader, never owner" rule for ``research.universe_tiers``):

- :class:`PostgresEvaluationStore` — sole writer of ``research.evaluations``, an
  append-only metrics ledger. One row per evaluation run: the verdict, the
  per-fold and aggregate metrics as JSONB, the config and protocol version that
  produced them. Never updated; the strategy's *status* lives in
  ``research.strategies`` and its transitions in ``research.strategy_transitions``.
- :class:`PostgresEvaluatorReader` — read-only over ``market_data.daily_bars``
  (backtest data), ``research.world_changers`` (anchor freshness), and
  ``research.universe_tiers`` (Tier-3 eligibility). It never creates or migrates
  those tables; a missing table is an infrastructure fault surfaced by the
  caller, not papered over here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Protocol

from shrap.research.strategy_evaluator.strategy import BarSample

CREATE_RESEARCH_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS research"

CREATE_EVALUATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.evaluations (
    evaluation_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    spec_hash TEXT NOT NULL,
    protocol_version TEXT NOT NULL,
    verdict TEXT NOT NULL,
    reason TEXT NOT NULL,
    anchor_fresh BOOLEAN NOT NULL,
    total_trades INTEGER NOT NULL,
    from_stage TEXT NOT NULL,
    to_stage TEXT,
    aggregate_metrics JSONB NOT NULL,
    fold_metrics JSONB NOT NULL,
    stress_metrics JSONB NOT NULL,
    config JSONB NOT NULL,
    card_path TEXT,
    trigger TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip()

CREATE_EVALUATIONS_STRATEGY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS evaluations_strategy_idx
ON research.evaluations (strategy_id, created_at DESC)
""".strip()

# Added with the archetype-conditional gates (ADR-0013). `anchor_fresh` alone is
# ambiguous once an archetype can be anchor-less: False means "the anchor is
# dead" for infra-graph-play and "there was never an anchor" for
# technical-catalyst. DEFAULT TRUE is correct for every pre-existing row —
# infra-graph-play was the only evaluable archetype when they were written — so
# the dead-anchor set stays queryable as (anchor_required AND NOT anchor_fresh).
ADD_EVALUATIONS_ANCHOR_REQUIRED_SQL = """
ALTER TABLE research.evaluations
ADD COLUMN IF NOT EXISTS anchor_required BOOLEAN NOT NULL DEFAULT TRUE
""".strip()

INSERT_EVALUATION_SQL = """
INSERT INTO research.evaluations (
    evaluation_id, strategy_id, spec_hash, protocol_version, verdict, reason,
    anchor_required, anchor_fresh, total_trades, from_stage, to_stage,
    aggregate_metrics, fold_metrics, stress_metrics, config, card_path,
    trigger, created_at
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
    $12::jsonb, $13::jsonb, $14::jsonb, $15::jsonb, $16, $17, $18
)
""".strip()

# Read-only. The Evaluator consumes these tables; other agents own them.
SELECT_WORLD_CHANGER_STATUS_SQL = """
SELECT status FROM research.world_changers WHERE candidate_id = $1
""".strip()

SELECT_TICKER_TIER_SQL = """
SELECT tier FROM research.universe_tiers WHERE ticker = $1
""".strip()

SELECT_DAILY_BARS_SQL = """
SELECT session_date, open, high, low, close, volume
FROM market_data.daily_bars
WHERE ticker = $1 AND adjustment = $2 AND session_date BETWEEN $3 AND $4
ORDER BY session_date
""".strip()


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetchrow(self, sql: str, *args: object) -> Mapping[str, Any] | None: ...

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


class PostgresEvaluationStore:
    """Append-only sink for ``research.evaluations``."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_RESEARCH_SCHEMA_SQL)
            await conn.execute(CREATE_EVALUATIONS_TABLE_SQL)
            await conn.execute(ADD_EVALUATIONS_ANCHOR_REQUIRED_SQL)
            await conn.execute(CREATE_EVALUATIONS_STRATEGY_INDEX_SQL)

    async def insert_evaluation(
        self,
        *,
        evaluation_id: str,
        strategy_id: str,
        spec_hash: str,
        protocol_version: str,
        verdict: str,
        reason: str,
        anchor_required: bool,
        anchor_fresh: bool,
        total_trades: int,
        from_stage: str,
        to_stage: str | None,
        aggregate_metrics: dict[str, Any],
        fold_metrics: list[dict[str, Any]],
        stress_metrics: dict[str, Any],
        config: dict[str, Any],
        card_path: str | None,
        trigger: str,
        created_at: datetime,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_EVALUATION_SQL,
                evaluation_id,
                strategy_id,
                spec_hash,
                protocol_version,
                verdict,
                reason,
                anchor_required,
                anchor_fresh,
                total_trades,
                from_stage,
                to_stage,
                json.dumps(aggregate_metrics, separators=(",", ":")),
                json.dumps(fold_metrics, separators=(",", ":")),
                json.dumps(stress_metrics, separators=(",", ":")),
                json.dumps(config, separators=(",", ":")),
                card_path,
                trigger,
                created_at,
            )


class PostgresEvaluatorReader:
    """Read-only consumer of market data and foreign anchor/tier tables."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def world_changer_status(self, candidate_id: str) -> str | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_WORLD_CHANGER_STATUS_SQL, candidate_id)
        return None if row is None else str(row["status"])

    async def ticker_tier(self, ticker: str) -> str | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_TICKER_TIER_SQL, ticker)
        return None if row is None else str(row["tier"])

    async def read_bars(
        self, ticker: str, start: date, end: date, adjustment: str
    ) -> list[BarSample]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_DAILY_BARS_SQL, ticker, adjustment, start, end)
        return [
            BarSample(
                session_date=row["session_date"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            for row in rows
        ]


__all__ = [
    "ADD_EVALUATIONS_ANCHOR_REQUIRED_SQL",
    "CREATE_EVALUATIONS_TABLE_SQL",
    "CREATE_RESEARCH_SCHEMA_SQL",
    "INSERT_EVALUATION_SQL",
    "SELECT_DAILY_BARS_SQL",
    "SELECT_TICKER_TIER_SQL",
    "SELECT_WORLD_CHANGER_STATUS_SQL",
    "AsyncPool",
    "PostgresEvaluationStore",
    "PostgresEvaluatorReader",
]
