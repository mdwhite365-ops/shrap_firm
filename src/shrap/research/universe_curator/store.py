"""Persistence for Tier 2/3 membership and staged Tier 3 proposals (ADR-0012).

Two tables (Curator spec §State):

- ``research.universe_tiers`` — one row per name currently in Tier 2 (Watch) or
  Tier 3 (Active). The Pre-Trade Checker reads this table with the fixed query
  ``SELECT tier FROM research.universe_tiers WHERE ticker = $1`` and treats
  ``tier = 'active'`` as tradeable; the Curator is the sole writer. Tier 1
  (Discovery) has no state, so a name leaving Tier 2/3 for Discovery is a row
  deletion, not a status flag — which keeps the table's invariant ("current
  members only") and lets a replay of the transition events rebuild it exactly.
- ``research.universe_staging`` — pending Tier 3 proposals awaiting Mike's
  decision. Resolved rows retain their disposition and note; the deny path is
  as auditable as the allow path.

The ``ensure_schema`` DDL follows the house idempotent ensure-schema pattern
(CREATE TABLE IF NOT EXISTS, matching the Tech Watcher and strategy-registry
stores). The Curator owns and migrates these tables; the Pre-Trade Checker
gate is a read-only consumer that never creates them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Protocol

# Tier-column literals. ``active`` is pinned by the Pre-Trade Checker gate
# (src/shrap/risk_compliance/tier3_membership.py:TIER3_ACTIVE_TIER); this store
# must write exactly that literal for a Tier 3 name. ``watch`` is Tier 2.
# Tier 1 (Discovery) has no row, so it is a label used only in event payloads.
TIER_WATCH = "watch"
TIER_ACTIVE = "active"
TIER_DISCOVERY = "discovery"

# Staging dispositions.
DISPOSITION_PENDING = "pending"
DISPOSITION_APPROVED = "approved"
DISPOSITION_REJECTED = "rejected"

# Staging kinds.
KIND_PROMOTION = "promotion"
KIND_EVICTION = "eviction"

CREATE_RESEARCH_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS research"

CREATE_UNIVERSE_TIERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.universe_tiers (
    ticker TEXT PRIMARY KEY,
    cik TEXT,
    tier TEXT NOT NULL CHECK (tier IN ('watch', 'active')),
    mechanism TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    entered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expiry TIMESTAMPTZ,
    falsifier TEXT,
    profile_path TEXT
)
""".strip()

# Roster reads (Filing Processor, News Analyzer) scan the Active tier; the
# expiry sweep scans Watch entries with a due date. One index serves both.
CREATE_UNIVERSE_TIERS_TIER_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS universe_tiers_tier_idx
ON research.universe_tiers (tier)
""".strip()

CREATE_UNIVERSE_STAGING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.universe_staging (
    staging_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('promotion', 'eviction')),
    source_tier TEXT NOT NULL,
    destination_tier TEXT NOT NULL,
    mechanism TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    paired_eviction_ticker TEXT,
    consequences JSONB,
    disposition TEXT NOT NULL DEFAULT 'pending'
        CHECK (disposition IN ('pending', 'approved', 'rejected')),
    note TEXT,
    staged_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
)
""".strip()

CREATE_UNIVERSE_STAGING_DISPOSITION_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS universe_staging_disposition_idx
ON research.universe_staging (disposition, staged_at DESC)
""".strip()

SELECT_TIER_ROW_SQL = """
SELECT ticker, cik, tier, mechanism, evidence_ref, entered_at, expiry, falsifier, profile_path
FROM research.universe_tiers
WHERE ticker = $1
""".strip()

SELECT_TIERS_BY_TIER_SQL = """
SELECT ticker, cik, tier, mechanism, evidence_ref, entered_at, expiry, falsifier, profile_path
FROM research.universe_tiers
WHERE tier = $1
ORDER BY ticker
""".strip()

COUNT_ACTIVE_SQL = """
SELECT count(*) AS n FROM research.universe_tiers WHERE tier = 'active'
""".strip()

SELECT_EXPIRED_WATCH_SQL = """
SELECT ticker, cik, tier, mechanism, evidence_ref, entered_at, expiry, falsifier, profile_path
FROM research.universe_tiers
WHERE tier = 'watch' AND expiry IS NOT NULL AND expiry <= $1
ORDER BY ticker
""".strip()

INSERT_WATCH_SQL = """
INSERT INTO research.universe_tiers (
    ticker, cik, tier, mechanism, evidence_ref, entered_at, expiry, falsifier, profile_path
)
VALUES ($1, $2, 'watch', $3, $4, $5, $6, $7, $8)
""".strip()

# Promotion / launch-load path. ON CONFLICT lets a watch row flip to active and
# keeps the launch load idempotent under re-delivery (though the curator also
# skips already-active names before calling this so no spurious event fires).
UPSERT_ACTIVE_SQL = """
INSERT INTO research.universe_tiers (
    ticker, cik, tier, mechanism, evidence_ref, entered_at, expiry, falsifier, profile_path
)
VALUES ($1, $2, 'active', $3, $4, $5, NULL, NULL, $6)
ON CONFLICT (ticker) DO UPDATE SET
    cik = COALESCE(EXCLUDED.cik, research.universe_tiers.cik),
    tier = 'active',
    mechanism = EXCLUDED.mechanism,
    evidence_ref = EXCLUDED.evidence_ref,
    entered_at = EXCLUDED.entered_at,
    expiry = NULL,
    falsifier = NULL,
    profile_path = COALESCE(EXCLUDED.profile_path, research.universe_tiers.profile_path)
""".strip()

UPDATE_WATCH_EXPIRY_SQL = """
UPDATE research.universe_tiers
SET expiry = $2
WHERE ticker = $1 AND tier = 'watch'
""".strip()

DELETE_TICKER_SQL = """
DELETE FROM research.universe_tiers WHERE ticker = $1
""".strip()

INSERT_STAGING_SQL = """
INSERT INTO research.universe_staging (
    staging_id, ticker, kind, source_tier, destination_tier, mechanism,
    evidence_ref, paired_eviction_ticker, consequences, disposition, note, staged_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, 'pending', NULL, $10)
""".strip()

SELECT_STAGING_ROW_SQL = """
SELECT staging_id, ticker, kind, source_tier, destination_tier, mechanism,
       evidence_ref, paired_eviction_ticker, consequences, disposition, note,
       staged_at, resolved_at
FROM research.universe_staging
WHERE staging_id = $1
""".strip()

SELECT_PENDING_STAGING_SQL = """
SELECT staging_id, ticker, kind, source_tier, destination_tier, mechanism,
       evidence_ref, paired_eviction_ticker, consequences, disposition, note,
       staged_at, resolved_at
FROM research.universe_staging
WHERE disposition = 'pending'
ORDER BY staged_at
""".strip()

RESOLVE_STAGING_SQL = """
UPDATE research.universe_staging
SET disposition = $2, note = $3, resolved_at = $4
WHERE staging_id = $1 AND disposition = 'pending'
""".strip()

# Best-effort consequence annotation (Curator spec Processing §Tier 3 promotion):
# strategies in a paper or live-paper stage that reference the ticker. The
# tickers column is JSONB of unknown internal shape, so we match the quoted
# ticker inside its text form — crude but honest, and it never false-matches a
# substring ticker (the quotes bound it).
SELECT_STRATEGIES_REFERENCING_SQL = """
SELECT strategy_id, name, status
FROM research.strategies
WHERE status = ANY($2::text[]) AND tickers::text ILIKE $1
ORDER BY updated_at DESC
""".strip()


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetchrow(self, sql: str, *args: object) -> Mapping[str, Any] | None: ...

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]: ...

    def transaction(self) -> AbstractAsyncContextManager[object]: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


class PostgresUniverseStore:
    """Sole writer of Tier 2/3 membership and staged Tier 3 proposals."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_RESEARCH_SCHEMA_SQL)
            await conn.execute(CREATE_UNIVERSE_TIERS_TABLE_SQL)
            await conn.execute(CREATE_UNIVERSE_TIERS_TIER_INDEX_SQL)
            await conn.execute(CREATE_UNIVERSE_STAGING_TABLE_SQL)
            await conn.execute(CREATE_UNIVERSE_STAGING_DISPOSITION_INDEX_SQL)

    async def get_tier_row(self, ticker: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_TIER_ROW_SQL, ticker)
        return None if row is None else dict(row)

    async def list_by_tier(self, tier: str) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_TIERS_BY_TIER_SQL, tier)
        return [dict(row) for row in rows]

    async def active_count(self) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(COUNT_ACTIVE_SQL)
        return 0 if row is None else int(row["n"])

    async def expired_watch(self, now: datetime) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_EXPIRED_WATCH_SQL, now)
        return [dict(row) for row in rows]

    async def insert_watch(
        self,
        *,
        ticker: str,
        cik: str | None,
        mechanism: str,
        evidence_ref: str,
        entered_at: datetime,
        expiry: datetime | None,
        falsifier: str | None,
        profile_path: str | None = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_WATCH_SQL,
                ticker,
                cik,
                mechanism,
                evidence_ref,
                entered_at,
                expiry,
                falsifier,
                profile_path,
            )

    async def upsert_active(
        self,
        *,
        ticker: str,
        cik: str | None,
        mechanism: str,
        evidence_ref: str,
        entered_at: datetime,
        profile_path: str | None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                UPSERT_ACTIVE_SQL,
                ticker,
                cik,
                mechanism,
                evidence_ref,
                entered_at,
                profile_path,
            )

    async def update_watch_expiry(self, ticker: str, expiry: datetime) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(UPDATE_WATCH_EXPIRY_SQL, ticker, expiry)
        return str(result).endswith(" 1")

    async def delete_ticker(self, ticker: str) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(DELETE_TICKER_SQL, ticker)
        return str(result).endswith(" 1")

    async def insert_staging(
        self,
        *,
        staging_id: str,
        ticker: str,
        kind: str,
        source_tier: str,
        destination_tier: str,
        mechanism: str,
        evidence_ref: str,
        paired_eviction_ticker: str | None,
        consequences: list[dict[str, Any]],
        staged_at: datetime,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_STAGING_SQL,
                staging_id,
                ticker,
                kind,
                source_tier,
                destination_tier,
                mechanism,
                evidence_ref,
                paired_eviction_ticker,
                json.dumps(consequences, separators=(",", ":")),
                staged_at,
            )

    async def get_staging_row(self, staging_id: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_STAGING_ROW_SQL, staging_id)
        return None if row is None else dict(row)

    async def pending_staging(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_PENDING_STAGING_SQL)
        return [dict(row) for row in rows]

    async def resolve_staging(
        self, staging_id: str, *, disposition: str, note: str | None, resolved_at: datetime
    ) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                RESOLVE_STAGING_SQL, staging_id, disposition, note, resolved_at
            )
        return str(result).endswith(" 1")

    async def apply_promotion(
        self,
        *,
        ticker: str,
        cik: str | None,
        mechanism: str,
        evidence_ref: str,
        entered_at: datetime,
        profile_path: str | None,
        staging_id: str,
        note: str | None,
        resolved_at: datetime,
        evict_ticker: str | None = None,
    ) -> None:
        """Promote (and optionally evict a paired name) and resolve the staging
        row in one transaction. Callers publish the events after commit."""

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                if evict_ticker is not None:
                    await conn.execute(DELETE_TICKER_SQL, evict_ticker)
                await conn.execute(
                    UPSERT_ACTIVE_SQL,
                    ticker,
                    cik,
                    mechanism,
                    evidence_ref,
                    entered_at,
                    profile_path,
                )
                await conn.execute(
                    RESOLVE_STAGING_SQL, staging_id, DISPOSITION_APPROVED, note, resolved_at
                )

    async def apply_eviction(
        self,
        *,
        ticker: str,
        staging_id: str,
        note: str | None,
        resolved_at: datetime,
    ) -> None:
        """Evict a name to Discovery and resolve the staging row in one
        transaction. Callers publish the ``evicted`` event after commit."""

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(DELETE_TICKER_SQL, ticker)
                await conn.execute(
                    RESOLVE_STAGING_SQL, staging_id, DISPOSITION_APPROVED, note, resolved_at
                )

    async def strategies_referencing(
        self, ticker: str, statuses: Sequence[str]
    ) -> list[dict[str, Any]]:
        """Best-effort: strategies in the given stages that reference the ticker.

        Returns an empty list if ``research.strategies`` does not exist yet
        (the Strategy Librarian may not have created it) — annotation is a
        courtesy for Mike's decision, never a gate.
        """

        pattern = f'%"{ticker}"%'
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(SELECT_STRATEGIES_REFERENCING_SQL, pattern, list(statuses))
        except Exception:
            return []
        return [dict(row) for row in rows]


__all__ = [
    "COUNT_ACTIVE_SQL",
    "CREATE_UNIVERSE_STAGING_TABLE_SQL",
    "CREATE_UNIVERSE_TIERS_TABLE_SQL",
    "DELETE_TICKER_SQL",
    "DISPOSITION_APPROVED",
    "DISPOSITION_PENDING",
    "DISPOSITION_REJECTED",
    "INSERT_STAGING_SQL",
    "INSERT_WATCH_SQL",
    "KIND_EVICTION",
    "KIND_PROMOTION",
    "RESOLVE_STAGING_SQL",
    "SELECT_TIER_ROW_SQL",
    "TIER_ACTIVE",
    "TIER_DISCOVERY",
    "TIER_WATCH",
    "UPSERT_ACTIVE_SQL",
    "AsyncPool",
    "PostgresUniverseStore",
]
