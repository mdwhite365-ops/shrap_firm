"""One-time repair for seed evidence rows stamped with load time.

**Why this exists.** Card 2's ``load-seed-graph`` wrote evidence rows with
``observed_at = now()`` — the moment of loading, not the date the evidence was
observed. Card 3 fixed the loader, but the load is idempotent-by-skip, so any
graph loaded before that fix keeps the wrong dates forever. The Dell's seed
graph was loaded on 2026-07-27 under the old code and carries four rows
claiming that 2024 procurement announcements were observed that day.

**Why appending cannot fix it.** Staleness reads ``MAX(observed_at)``. A row
stamped too *fresh* always wins the max, so adding correct older rows would
change nothing — the false 2026 stamp would keep the node looking fresh. This
is a general property worth remembering: append-only plus a max-based clock can
absorb a too-*old* error but never a too-*new* one. Repair therefore requires an
in-place update, the documented exception to append-only on that table.

**Scope discipline.** This corrects only rows whose ``(ticker, layer_role)``
*and* ``evidence_ref`` match a declared seed node exactly. Evidence appended by
any later card has a different ref and is never touched, so re-running this
after real evidence lands cannot clobber it. Every correction writes a
``graph_node_history`` row, so the repair is itself auditable rather than a
silent rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ulid import ULID

from shrap.research.infra_mapper.first_graph import SEED_GRAPH_ID, SEED_NODES
from shrap.research.infra_mapper.store import PostgresGraphStore

# Keyed by the seed's own identity: only a row matching all three fields is a
# seed row this repair owns.
_SEED_BY_KEY: dict[tuple[str, str, str], datetime] = {
    (node.ticker, node.layer_role, node.evidence_ref): node.evidence_observed_at
    for node in SEED_NODES
}


@dataclass(frozen=True, slots=True)
class Correction:
    """One evidence row whose observation date was wrong."""

    evidence_id: str
    ticker: str
    layer_role: str
    was: datetime
    now: datetime

    @property
    def days_moved(self) -> int:
        return (self.was - self.now).days


@dataclass(frozen=True, slots=True)
class RestampReport:
    corrections: tuple[Correction, ...]
    rows_examined: int
    dry_run: bool

    def render(self) -> str:
        prefix = "[dry-run] " if self.dry_run else ""
        if not self.corrections:
            return (
                f"{prefix}seed evidence dates already correct "
                f"({self.rows_examined} row(s) examined); nothing to repair"
            )
        lines = [
            f"{prefix}corrected {len(self.corrections)} of {self.rows_examined} "
            f"evidence row(s) on the seed graph:"
        ]
        for correction in self.corrections:
            lines.append(
                f"  {correction.ticker:6} {correction.layer_role:20} "
                f"{correction.was.date()} -> {correction.now.date()} "
                f"({correction.days_moved}d older)"
            )
        return "\n".join(lines)


def plan_corrections(evidence_rows: list[dict[str, Any]]) -> tuple[Correction, ...]:
    """Pure: which seed evidence rows carry the wrong observation date."""

    corrections: list[Correction] = []
    for row in evidence_rows:
        key = (str(row["ticker"]), str(row["layer_role"]), str(row["evidence_ref"]))
        declared = _SEED_BY_KEY.get(key)
        if declared is None:
            continue  # not a seed row — belongs to a later card, leave it alone
        observed_at = row["observed_at"]
        if observed_at == declared:
            continue
        corrections.append(
            Correction(
                evidence_id=str(row["evidence_id"]),
                ticker=str(row["ticker"]),
                layer_role=str(row["layer_role"]),
                was=observed_at,
                now=declared,
            )
        )
    return tuple(corrections)


async def restamp_seed_evidence(
    store: PostgresGraphStore, *, dry_run: bool = False
) -> RestampReport:
    """Repair seed evidence dates on the seed graph. Idempotent."""

    rows = await store.evidence_for_graph(SEED_GRAPH_ID)
    corrections = plan_corrections(rows)

    if not dry_run:
        for correction in corrections:
            await store.correct_evidence_observed_at(
                evidence_id=correction.evidence_id, observed_at=correction.now
            )
            await store.record_history(
                history_id=str(ULID()),
                graph_id=SEED_GRAPH_ID,
                ticker=correction.ticker,
                layer_role=correction.layer_role,
                from_status=None,
                to_status="evidence-date-corrected",
                from_confidence=None,
                to_confidence=None,
                reason=(
                    f"evidence observation date corrected from "
                    f"{correction.was.date()} (load time) to {correction.now.date()} "
                    f"(true observation date); card-2 loader stamped load time"
                ),
            )

    return RestampReport(corrections=corrections, rows_examined=len(rows), dry_run=dry_run)


__all__ = ["Correction", "RestampReport", "plan_corrections", "restamp_seed_evidence"]
