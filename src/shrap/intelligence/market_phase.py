"""Market-phase cadence helpers shared by Intelligence feed agents.

Both the News Analyzer and the Filing Processor poll on the same
``operations.market-phase`` clock — fast during a session, hourly overnight —
so the mapping and the latest-phase read live here rather than being copied
into each agent. Agents import :func:`interval_for_phase` and
:func:`read_latest_phase` and keep their own run configs and thresholds.
"""

from __future__ import annotations

from typing import Any, Protocol

import structlog

from shrap.events import Envelope, normalize_redis_fields

log = structlog.get_logger(__name__)

STREAM_MARKET_PHASE = "operations.market-phase"

# Phases that keep the fast (active) cadence; everything else — including an
# unknown or absent phase — falls back to active, so a feed agent never goes
# quiet during a session because the scheduler stream was briefly unreadable.
PHASE_IDLE = frozenset({"overnight", "closed-day"})
DEFAULT_ACTIVE_INTERVAL_SECONDS = 600.0
DEFAULT_IDLE_INTERVAL_SECONDS = 3600.0


class PhaseRedis(Protocol):
    async def xrevrange(
        self, name: str, max: str = "+", min: str = "-", count: int | None = None
    ) -> Any: ...


def interval_for_phase(phase: str | None, active_seconds: float, idle_seconds: float) -> float:
    """Map a market phase to a poll interval; idle phases poll hourly."""

    if phase in PHASE_IDLE:
        return idle_seconds
    return active_seconds


async def read_latest_phase(redis: PhaseRedis) -> str | None:
    """Return the latest ``operations.market-phase`` phase, or None.

    Any failure (empty stream, malformed envelope, Redis error) returns None
    so the caller falls back to the active cadence.
    """

    try:
        entries = await redis.xrevrange(STREAM_MARKET_PHASE, count=1)
    except Exception:
        log.warning("market_phase.read_failed")
        return None
    if not entries:
        return None
    _redis_id, fields = entries[0]
    try:
        envelope = Envelope.from_redis_fields(normalize_redis_fields(fields))
    except Exception:
        log.warning("market_phase.malformed_event_skipped")
        return None
    if envelope.payload is None:
        return None
    phase = envelope.payload.get("phase")
    return str(phase) if phase is not None else None


__all__ = [
    "DEFAULT_ACTIVE_INTERVAL_SECONDS",
    "DEFAULT_IDLE_INTERVAL_SECONDS",
    "PHASE_IDLE",
    "STREAM_MARKET_PHASE",
    "PhaseRedis",
    "interval_for_phase",
    "read_latest_phase",
]
