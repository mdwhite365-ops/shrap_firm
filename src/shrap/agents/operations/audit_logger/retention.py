"""Stream retention: Redis is the transport, Postgres is the record.

Nothing trimmed any stream until 2026-07-31, when a systems check found
``ops.health-tick`` at 80,509 entries and climbing, with four more streams in
the thousands. Redis persists to disk here (``appendonly yes``), so this was a
slow capacity problem rather than a memory one — the kind that is never urgent
until it is.

**Why the Audit Logger owns this.** It is the component that already enumerates
every stream (``discover_streams`` with pattern ``*``) and the one whose whole
job is moving events into ``ops.audit_events``, where they live permanently.
That makes the framing explicit rather than implied: under ADR-0006 the bus is
how events *travel*, and the audit table is where they are *kept*. Trimming
Redis discards a delivered copy, not the record.

**Why a generous cap rather than trimming to consumer position.** ``XTRIM``
removes entries regardless of any group's read position, so trimming to what the
Audit Logger has consumed could drop entries another consumer had not reached.
Measured on 2026-07-31, every group was at lag 0 — but a cap that leaves days of
headroom is safe without depending on that staying true, and a consumer that
falls further behind than :data:`DEFAULT_MAX_LEN` entries has a problem no
retention policy should be papering over.

**What this does not fix.** A stream growing because a producer is republishing
the same thing forever is a producer bug, and trimming it would hide the
evidence. ``operations.reconciliation-discrepancy`` reached 11,096 entries that
way and was fixed at the source (see
:mod:`~shrap.agents.operations.reconciliation_agent.discrepancy_state`), not
here. Retention is for streams that are *supposed* to grow.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)

# Comfortably over a week of headroom for the noisiest legitimate producers.
# The Health Monitor ticks every 30s (2,880/day) and the three reconciliation
# agents publish one completed event per 300s pass (~2,592/day combined), so
# this is ~8.7 and ~9.6 days respectively. Every other stream in the firm is
# orders of magnitude below it and will never be trimmed at all.
#
# The first draft of this was 20,000, which is 6.94 days for health-tick — under
# the week it claimed. A test pins the arithmetic rather than the number, so the
# next person to change it has to keep the property rather than the constant.
DEFAULT_MAX_LEN = 25_000

# Per-stream overrides. Empty on purpose: one number that nothing has yet needed
# to escape is a policy, and a table of hand-tuned caps is a maintenance burden
# that earns its place only when a stream proves it needs one.
STREAM_MAX_LEN: Mapping[str, int] = {}


class StreamTrimmer(Protocol):
    """Just the trim call.

    Narrow deliberately: the Audit Logger's ``StreamRedis`` already has five
    methods and every test fake implements all of them. Widening it to add
    trimming would make a dozen unrelated fakes fail to typecheck for a
    capability none of them exercise.
    """

    async def xtrim(self, name: str, maxlen: int, approximate: bool = True) -> Any: ...


def cap_for(stream: str) -> int:
    """The retention cap for one stream."""

    return STREAM_MAX_LEN.get(stream, DEFAULT_MAX_LEN)


async def trim_streams(trimmer: StreamTrimmer, streams: Sequence[str]) -> int:
    """Trim each stream to its cap; returns how many entries Redis removed.

    ``approximate=True`` lets Redis trim at radix-node boundaries, which is the
    cheap form — it may leave a few entries above the cap and that is fine, the
    cap is a bound on growth and not an exact length.

    One stream failing does not abort the sweep. Retention is maintenance: it
    must never be the reason the audit trail stops moving.
    """

    removed = 0
    for stream in streams:
        try:
            result = await trimmer.xtrim(stream, maxlen=cap_for(stream), approximate=True)
        except Exception:
            log.warning("audit_logger.trim_failed", stream=stream, exc_info=True)
            continue
        count = int(result) if isinstance(result, int) else 0
        if count:
            log.info("audit_logger.trimmed", stream=stream, removed=count, max_len=cap_for(stream))
        removed += count
    return removed


__all__ = [
    "DEFAULT_MAX_LEN",
    "STREAM_MAX_LEN",
    "StreamTrimmer",
    "cap_for",
    "trim_streams",
]
