"""``research.capability_gaps`` — the build queue the literature is asking for.

One row per effect the firm cannot currently test, with every paper that cited
it. This is the durable half of ``expressible.py``: a single run's gaps are a
list, but the same gap seen from six unrelated papers over six weeks is an
argument, and only a table can tell those apart.

**Citations are a JSON object keyed by item id, not an array.** ``jsonb ||
jsonb`` merges objects by key, so re-running the generator over an item that was
already counted changes nothing. An array would have appended, and the ranking —
whose whole meaning is "how many *independent* papers asked for this" — would
have inflated every time anyone re-ran the CLI.

Nothing here is a decision. A gap is a measurement of the distance between the
literature and the engine; what to build is Mike's call, and the ranking exists
to inform it rather than to make it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from shrap.research.hypothesis_generator.expressible import (
    CapabilityGap,
    RankedGap,
    rank_gaps,
)

CREATE_CAPABILITY_GAPS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.capability_gaps (
    effect_name TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    missing JSONB NOT NULL DEFAULT '[]'::jsonb,
    sketch TEXT NOT NULL,
    citations JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL
)
""".strip()

# The object merge is what makes this idempotent. `sketch` and `kind` are left
# at their first-seen values on purpose: the first paper to ask for a capability
# defined it, and letting a later, vaguer abstract overwrite a good sketch would
# degrade the queue entry every time the effect came up again.
UPSERT_CAPABILITY_GAP_SQL = """
INSERT INTO research.capability_gaps (
    effect_name, kind, missing, sketch, citations, first_seen_at, last_seen_at
)
VALUES ($1, $2, $3::jsonb, $4, $5::jsonb, $6, $6)
ON CONFLICT (effect_name) DO UPDATE SET
    citations = research.capability_gaps.citations || EXCLUDED.citations,
    last_seen_at = EXCLUDED.last_seen_at
""".strip()

# Ranked by how many DISTINCT papers asked for the capability. `citations` is
# always an object (the column defaults to `{}` and every write merges an
# object), so counting its keys is the citation count.
SELECT_CAPABILITY_GAPS_SQL = """
SELECT
    effect_name, kind, missing, sketch, citations, first_seen_at, last_seen_at,
    (SELECT count(*) FROM jsonb_object_keys(citations)) AS citation_count
FROM research.capability_gaps
ORDER BY citation_count DESC, effect_name
""".strip()


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


class GapStore(Protocol):
    """The slice of persistence the generator needs."""

    async def record(self, gap: CapabilityGap) -> None: ...

    async def ranked(self) -> list[RankedGap]: ...


def _json_value(value: Any) -> Any:
    """asyncpg hands back jsonb as TEXT unless a codec is registered.

    Every test fixture in this repo passes real dicts, which is the one shape
    the driver never produces — that mismatch took the research ledger down in
    production (PR #152). Decoding defensively here means the reader works
    against both.
    """

    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return value


@dataclass(slots=True)
class InMemoryGapStore:
    """For dry runs and tests: records gaps without touching a database."""

    gaps: list[CapabilityGap] = field(default_factory=list)

    async def record(self, gap: CapabilityGap) -> None:
        self.gaps.append(gap)

    async def ranked(self) -> list[RankedGap]:
        return list(rank_gaps(self.gaps))


class PostgresGapStore:
    """The durable build queue."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS research")
            await conn.execute(CREATE_CAPABILITY_GAPS_TABLE_SQL)

    async def record(self, gap: CapabilityGap) -> None:
        citations = {gap.citation.item_id: gap.citation.as_json()}
        async with self._pool.acquire() as conn:
            await conn.execute(
                UPSERT_CAPABILITY_GAP_SQL,
                gap.effect_name,
                gap.kind,
                json.dumps(list(gap.missing), separators=(",", ":")),
                gap.sketch,
                json.dumps(citations, separators=(",", ":")),
                datetime.now(UTC),
            )

    async def ranked(self) -> list[RankedGap]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_CAPABILITY_GAPS_SQL)
        out: list[RankedGap] = []
        for row in rows:
            citations = _json_value(row["citations"])
            missing = _json_value(row["missing"])
            out.append(
                RankedGap(
                    effect_name=str(row["effect_name"]),
                    kind=str(row["kind"]),
                    citations=len(citations) if isinstance(citations, dict) else 0,
                    sketch=str(row["sketch"]),
                    missing=tuple(str(m) for m in missing) if isinstance(missing, list) else (),
                )
            )
        return out


def render_queue(gaps: Sequence[RankedGap]) -> str:
    """The build queue, most-cited first."""

    if not gaps:
        # Deliberately does not claim everything was runnable. It said exactly
        # that on the first live run, where the true cause was six refusals and
        # zero effects reaching the capability check at all — a reassuring
        # sentence about a stage nothing had entered.
        return "No capability gaps recorded — no effect has reached the capability check yet."
    buildable = [g for g in gaps if g.kind == "missing-scorer"]
    lines = [
        f"CAPABILITY GAPS — {len(gaps)} effect(s) the engine cannot run, "
        f"{len(buildable)} of them buildable on close and volume alone.",
        "",
    ]
    lines.extend(g.render() for g in gaps)
    lines.append("")
    lines.append(
        "A `missing-scorer` gap is a function to write. A `missing-data` gap is a "
        "feed to acquire, and the count of those is the honest argument for buying "
        "data rather than a reason to widen what counts as testable."
    )
    return "\n".join(lines)


__all__ = [
    "CREATE_CAPABILITY_GAPS_TABLE_SQL",
    "GapStore",
    "InMemoryGapStore",
    "PostgresGapStore",
    "render_queue",
]
