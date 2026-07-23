"""Tier 3 membership gate for the pre-trade checker (ADR-0012).

The Universe Curator owns ``research.universe_tiers`` and is the sole writer;
this gate is a **read-only** consumer. It answers one deterministic question
per order — "is this ticker currently in Tier 3 (Active, tradeable)?" — behind
a short-TTL in-process cache so the order path does not hit Postgres per order.

Two design invariants, both load-bearing for a risk gate:

- **Reader, never owner.** The gate never creates or migrates the table. If
  the table is missing, that is an infrastructure fault the gate must surface
  by failing closed, not paper over by creating an empty table (which would
  then reject every name silently).
- **Fail closed on unavailable state.** If the query cannot be answered
  (table missing, Postgres unreachable, any error), the gate returns
  ``TIER3_STATE_UNAVAILABLE`` and the order is vetoed. A risk gate that fails
  open under infrastructure failure is not a risk gate. Unavailable outcomes
  are never cached — recovery is re-checked on the next order.

The tier-column literal for the tradeable set is ``"active"`` (see
``TIER3_ACTIVE_TIER``). ADR-0012 names the tradeable tier "Tier 3 — Active";
the Curator spec (``docs/agents/research/universe-curator.md``) describes the
``tier`` column without pinning a literal, so this gate fixes it here and the
Pre-Trade Checker spec documents the choice. The Curator's first
implementation card must write this same literal.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)

TICKER_NOT_IN_TIER3 = "TICKER_NOT_IN_TIER3"
TIER3_STATE_UNAVAILABLE = "TIER3_STATE_UNAVAILABLE"

# The ``research.universe_tiers.tier`` value that marks a name as Tier 3
# (Active, tradeable). Tier 2 (Watch) rows carry a different value and must
# never satisfy this gate — including expired watch rows.
TIER3_ACTIVE_TIER = "active"

# One row per name currently in Tier 2 or 3 (Curator spec, State section), so
# this returns 0 or 1 row. Membership is decided in Python on the tier value,
# which keeps the fake-conn tests honest and the "wrong tier" path explicit.
SELECT_TIER_SQL = "SELECT tier FROM research.universe_tiers WHERE ticker = $1"


class Tier3Connection(Protocol):
    async def fetchrow(self, sql: str, *args: object) -> Mapping[str, Any] | None: ...


class Tier3AcquireContext(Protocol):
    async def __aenter__(self) -> Tier3Connection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class Tier3Pool(Protocol):
    def acquire(self) -> Tier3AcquireContext: ...


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    is_member: bool
    expires_at: float


class Tier3MembershipGate:
    """Deterministic Tier 3 membership check with a short-TTL in-process cache.

    ``check`` returns ``None`` when the ticker may proceed, or a veto reason
    code (``TICKER_NOT_IN_TIER3`` / ``TIER3_STATE_UNAVAILABLE``) otherwise —
    mirroring ``RedisRateLimiter.acquire``'s ``None``/reason-code contract so
    the agent loop treats both stateful gates the same way.
    """

    def __init__(
        self,
        pool: Tier3Pool,
        ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._pool = pool
        self._ttl = ttl_seconds
        self._clock = clock
        self._cache: dict[str, _CacheEntry] = {}

    async def check(self, ticker: str) -> str | None:
        symbol = ticker.strip().upper()
        now = self._clock()
        cached = self._cache.get(symbol)
        if cached is not None and cached.expires_at > now:
            return None if cached.is_member else TICKER_NOT_IN_TIER3

        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(SELECT_TIER_SQL, symbol)
        except Exception:
            # Fail closed: table missing / Postgres unreachable / any error.
            # Not cached, so recovery is re-checked on the next order.
            log.error(
                "pre_trade_checker.tier3_state_unavailable",
                ticker=symbol,
                exc_info=True,
            )
            return TIER3_STATE_UNAVAILABLE

        is_member = row is not None and str(row["tier"]).strip().lower() == TIER3_ACTIVE_TIER
        self._cache[symbol] = _CacheEntry(is_member=is_member, expires_at=now + self._ttl)
        return None if is_member else TICKER_NOT_IN_TIER3


__all__ = [
    "SELECT_TIER_SQL",
    "TICKER_NOT_IN_TIER3",
    "TIER3_ACTIVE_TIER",
    "TIER3_STATE_UNAVAILABLE",
    "Tier3AcquireContext",
    "Tier3Connection",
    "Tier3MembershipGate",
    "Tier3Pool",
]
