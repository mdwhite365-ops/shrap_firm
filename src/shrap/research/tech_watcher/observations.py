"""Thesis-level observation log for promoted world-changers.

**The gap this closes.** The Research funnel had two evidence stores and
neither could hold an observation about a *thesis*. ``graph_node_evidence``
(Infra Mapper) records why a ticker sits in a layer role — per-node, not
per-thesis. ``world_changer_evidence`` (this package, ``candidates.py``)
records which *ingested items* produced a candidate at proposal time: it is
keyed ``(candidate_id, item_id)``, carries no observation date, and cannot
represent anything Mike saw himself. So a real event bearing on a promoted
thesis — a milestone, a cost datapoint, a contradicting result — had nowhere
to live and survived only in chat.

This table is the ongoing log, deliberately named apart from the provenance
table it sits beside:

- ``world_changer_evidence`` — what made us propose it (provenance, ingest).
- ``world_changer_observations`` — what has happened since (this module).

**Why the falsifier link is required, not optional.** A thesis accumulating
supportive observations that touch none of its kill criteria is not being
validated — it is collecting a story. That is the failure the funnel's
falsifier discipline exists to prevent, and it is invisible unless the link
is recorded per observation. So every row declares whether it bears on a
declared kill criterion (``kill_criterion_index``, ``NULL`` for none), and
the summary reports that count first. Zero-of-N is the loudest thing this
module can tell you.

**Hard vs soft** reuses the source-class independence taxonomy already in
``synthesis.py``: hard evidence carries legal or financial consequence
(filings, awards, rules), soft evidence is narrative (announcements,
promotional material, press). A promotional demonstration is soft by
construction, however striking the imagery.

Append-only. Corrections are new rows, never edits.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from shrap.research.tech_watcher.candidates import AsyncPool

# How an observation bears on the thesis.
BEARING_SUPPORTS = "supports"
BEARING_CONTRADICTS = "contradicts"
BEARING_NEUTRAL = "neutral"
BEARINGS = (BEARING_SUPPORTS, BEARING_CONTRADICTS, BEARING_NEUTRAL)

CREATE_OBSERVATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.world_changer_observations (
    observation_id TEXT PRIMARY KEY,
    world_changer_id TEXT NOT NULL REFERENCES research.world_changers (candidate_id),
    observation TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    origin TEXT NOT NULL,
    hard BOOLEAN NOT NULL,
    bearing TEXT NOT NULL CHECK (bearing IN ('supports', 'contradicts', 'neutral')),
    kill_criterion_index INTEGER,
    observed_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip()

CREATE_OBSERVATIONS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS world_changer_observations_thesis_idx
ON research.world_changer_observations (world_changer_id, observed_at DESC)
""".strip()

INSERT_OBSERVATION_SQL = """
INSERT INTO research.world_changer_observations (
    observation_id, world_changer_id, observation, evidence_ref,
    origin, hard, bearing, kill_criterion_index, observed_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
""".strip()

SELECT_OBSERVATIONS_SQL = """
SELECT observation_id, world_changer_id, observation, evidence_ref, origin,
       hard, bearing, kill_criterion_index, observed_at, recorded_at
FROM research.world_changer_observations
WHERE world_changer_id = $1
ORDER BY observed_at DESC
""".strip()

SELECT_THESIS_SQL = """
SELECT candidate_id, name, status, kill_criteria, falsifier_horizon
FROM research.world_changers
WHERE candidate_id = $1
""".strip()


class ObservationError(ValueError):
    """Rejected observation — bad bearing, dangling criterion, empty fields."""


@dataclass(frozen=True, slots=True)
class ObservationSummary:
    """Honest accounting over one thesis's observation log."""

    total: int
    supports: int
    contradicts: int
    neutral: int
    hard: int
    bearing_on_criteria: int
    criteria_touched: tuple[int, ...]
    kill_criteria: tuple[str, ...]

    @property
    def soft(self) -> int:
        return self.total - self.hard

    @property
    def criteria_untouched(self) -> tuple[int, ...]:
        return tuple(i for i in range(len(self.kill_criteria)) if i not in self.criteria_touched)

    @property
    def warnings(self) -> tuple[str, ...]:
        """The things worth saying out loud before anyone reads the counts as
        progress."""

        out: list[str] = []
        if self.total == 0:
            return ()
        if self.bearing_on_criteria == 0:
            out.append(
                "NO observation bears on any kill criterion — this log is "
                "narrative accumulation, not validation of the thesis"
            )
        if self.hard == 0:
            out.append(
                "every observation is soft (narrative); no filing, award, or rule "
                "has yet touched this thesis"
            )
        if self.contradicts == 0 and self.supports > 2:
            out.append(
                f"{self.supports} supporting observations and 0 contradicting — "
                "confirmation pattern; check whether disconfirming evidence is "
                "being looked for at all"
            )
        return tuple(out)


def summarize(
    observations: Sequence[dict[str, Any]], kill_criteria: Sequence[str]
) -> ObservationSummary:
    """Pure accounting over an observation log."""

    touched = sorted(
        {
            int(o["kill_criterion_index"])
            for o in observations
            if o.get("kill_criterion_index") is not None
        }
    )
    return ObservationSummary(
        total=len(observations),
        supports=sum(1 for o in observations if o["bearing"] == BEARING_SUPPORTS),
        contradicts=sum(1 for o in observations if o["bearing"] == BEARING_CONTRADICTS),
        neutral=sum(1 for o in observations if o["bearing"] == BEARING_NEUTRAL),
        hard=sum(1 for o in observations if o["hard"]),
        bearing_on_criteria=sum(
            1 for o in observations if o.get("kill_criterion_index") is not None
        ),
        criteria_touched=tuple(touched),
        kill_criteria=tuple(kill_criteria),
    )


def render_summary(
    thesis_name: str, summary: ObservationSummary, observations: Sequence[dict[str, Any]]
) -> str:
    lines = [
        f"{thesis_name}",
        f"  {summary.total} observation(s): {summary.supports} supporting, "
        f"{summary.contradicts} contradicting, {summary.neutral} neutral",
        f"  hard: {summary.hard}   soft: {summary.soft}",
        f"  bearing on a kill criterion: {summary.bearing_on_criteria} of {summary.total}",
    ]
    if summary.kill_criteria:
        lines.append("  kill criteria:")
        for i, criterion in enumerate(summary.kill_criteria):
            mark = "touched" if i in summary.criteria_touched else "UNTOUCHED"
            lines.append(f"    [{i}] {mark:9} {criterion}")
    for warning in summary.warnings:
        lines.append(f"  ! {warning}")
    if observations:
        lines.append("  log (newest first):")
        for obs in observations:
            index = obs.get("kill_criterion_index")
            crit = "—" if index is None else f"kc[{index}]"
            hardness = "hard" if obs["hard"] else "soft"
            lines.append(
                f"    {obs['observed_at'].date()}  {obs['bearing']:11} {hardness:4} "
                f"{crit:6} {obs['origin']:24} {obs['observation']}"
            )
    return "\n".join(lines)


def validate_observation(
    *,
    observation: str,
    evidence_ref: str,
    origin: str,
    bearing: str,
    kill_criterion_index: int | None,
    kill_criteria: Sequence[str],
) -> None:
    """Reject an observation the log should not hold. Raises ``ObservationError``."""

    if not observation.strip():
        raise ObservationError("an observation needs a description of what was observed")
    if not evidence_ref.strip():
        raise ObservationError(
            "an observation needs an evidence_ref a human can resolve to a primary source"
        )
    if not origin.strip():
        raise ObservationError(
            "an observation needs an originating institution (e.g. issuer, research, "
            "gov:<agency>) — an unattributed observation cannot be checked"
        )
    if bearing not in BEARINGS:
        raise ObservationError(f"bearing must be one of {list(BEARINGS)}, got '{bearing}'")
    if kill_criterion_index is not None and not 0 <= kill_criterion_index < len(kill_criteria):
        raise ObservationError(
            f"kill_criterion_index {kill_criterion_index} is out of range; this thesis "
            f"declares {len(kill_criteria)} criteria (valid: 0..{len(kill_criteria) - 1})"
        )


class PostgresObservationStore:
    """Sole writer of ``research.world_changer_observations``."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_OBSERVATIONS_TABLE_SQL)
            await conn.execute(CREATE_OBSERVATIONS_INDEX_SQL)

    async def get_thesis(self, world_changer_id: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_THESIS_SQL, world_changer_id)
        return None if row is None else dict(row)

    async def insert_observation(
        self,
        *,
        observation_id: str,
        world_changer_id: str,
        observation: str,
        evidence_ref: str,
        origin: str,
        hard: bool,
        bearing: str,
        kill_criterion_index: int | None,
        observed_at: datetime,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_OBSERVATION_SQL,
                observation_id,
                world_changer_id,
                observation,
                evidence_ref,
                origin,
                hard,
                bearing,
                kill_criterion_index,
                observed_at,
            )

    async def observations_for(self, world_changer_id: str) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_OBSERVATIONS_SQL, world_changer_id)
        return [dict(row) for row in rows]


__all__ = [
    "BEARINGS",
    "BEARING_CONTRADICTS",
    "BEARING_NEUTRAL",
    "BEARING_SUPPORTS",
    "CREATE_OBSERVATIONS_TABLE_SQL",
    "ObservationError",
    "ObservationSummary",
    "PostgresObservationStore",
    "render_summary",
    "summarize",
    "validate_observation",
]
