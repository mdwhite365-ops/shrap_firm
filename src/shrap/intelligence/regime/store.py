"""PostgreSQL persistence for regime classification runs.

Append-only per spec: every tick lands in intel.regime_history; every
debounced transition additionally lands in intel.regime_changes. Restart
state (label, leader, streak) is re-derived from the latest history row.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from shrap.intelligence.regime.classifier import Classification, ClassifierState

CREATE_INTEL_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS intel"

CREATE_REGIME_HISTORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS intel.regime_history (
    event_id TEXT PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    label TEXT NOT NULL,
    prior_label TEXT NOT NULL,
    changed BOOLEAN NOT NULL,
    leader TEXT NOT NULL,
    streak INTEGER NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    band_lo DOUBLE PRECISION NOT NULL,
    band_hi DOUBLE PRECISION NOT NULL,
    features JSONB NOT NULL,
    scores JSONB NOT NULL,
    missing_features JSONB NOT NULL
)
""".strip()

CREATE_REGIME_HISTORY_AT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS regime_history_at_idx ON intel.regime_history (at DESC)
""".strip()

CREATE_REGIME_CHANGES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS intel.regime_changes (
    event_id TEXT PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    prior_label TEXT NOT NULL,
    new_label TEXT NOT NULL,
    streak INTEGER NOT NULL,
    features JSONB NOT NULL
)
""".strip()

INSERT_REGIME_HISTORY_SQL = """
INSERT INTO intel.regime_history (
    event_id, label, prior_label, changed, leader, streak,
    confidence, band_lo, band_hi, features, scores, missing_features
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11::jsonb, $12::jsonb)
ON CONFLICT (event_id) DO NOTHING
""".strip()

INSERT_REGIME_CHANGE_SQL = """
INSERT INTO intel.regime_changes (event_id, prior_label, new_label, streak, features)
VALUES ($1, $2, $3, $4, $5::jsonb)
ON CONFLICT (event_id) DO NOTHING
""".strip()

SELECT_LAST_STATE_SQL = """
SELECT label, leader, streak FROM intel.regime_history ORDER BY at DESC LIMIT 1
""".strip()


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetch(self, sql: str, *args: object) -> list[Any]: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


class PostgresRegimeStore:
    """Append-only store for classification history and transitions."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_INTEL_SCHEMA_SQL)
            await conn.execute(CREATE_REGIME_HISTORY_TABLE_SQL)
            await conn.execute(CREATE_REGIME_HISTORY_AT_INDEX_SQL)
            await conn.execute(CREATE_REGIME_CHANGES_TABLE_SQL)

    async def record(
        self,
        event_id: str,
        result: Classification,
        features_payload: dict[str, float | None],
    ) -> None:
        scores_payload = [
            {
                "name": score.name,
                "qualifies": score.qualifies,
                "soft_hits": score.soft_hits,
                "soft_total": score.soft_total,
            }
            for score in result.scores
        ]
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_REGIME_HISTORY_SQL,
                event_id,
                result.label,
                result.prior_label,
                result.changed,
                result.leader,
                result.streak,
                result.confidence,
                result.sizing_band[0],
                result.sizing_band[1],
                json.dumps(features_payload, separators=(",", ":")),
                json.dumps(scores_payload, separators=(",", ":")),
                json.dumps(result.missing_features, separators=(",", ":")),
            )
            if result.changed:
                await conn.execute(
                    INSERT_REGIME_CHANGE_SQL,
                    event_id,
                    result.prior_label,
                    result.label,
                    result.streak,
                    json.dumps(features_payload, separators=(",", ":")),
                )

    async def last_state(self) -> ClassifierState | None:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_LAST_STATE_SQL)
        if not rows:
            return None
        row = rows[0]
        return ClassifierState(
            label=str(row["label"]),
            leader=str(row["leader"]),
            streak=int(row["streak"]),
        )
