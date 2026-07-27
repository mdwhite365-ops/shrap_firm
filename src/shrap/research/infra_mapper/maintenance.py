"""Deterministic staleness maintenance for infrastructure graphs.

Implements step 1 of the Infra Mapper spec's daily maintenance pass: for every
node in an active graph, compare the age of its most recent evidence row
against a freshness threshold and move the node between ``active`` and
``stale-evidence`` accordingly. No LLM, no external fetch — this is the
deterministic half the spec says maintenance should migrate to, and it is the
half that can be written now because the answer is a date comparison.

**What this pass does not do.** It does not *refresh* evidence (fetching new
filings is a later card), it does not evaluate kill criteria (Month 4, and it
needs external data), and it does not touch nodes in ``pending-review``,
``downgraded``, or ``removed``. Those statuses are owned by Mike's review gate
and by kill-criterion evaluation respectively; a staleness pass that silently
reactivated a downgraded node would be laundering a kill decision. The pass
owns exactly one axis: ``active`` <-> ``stale-evidence``.

**Staleness is measured from evidence rows, not from the node.**
``graph_nodes.last_confirmed_evidence_ref`` is free text with no date in it, so
the only honest clock is ``MAX(observed_at)`` over that node's
``graph_node_evidence`` rows. A node with no evidence rows at all is stale by
definition — we cannot claim a role is confirmed when nothing records the
confirmation.

The transition is two-way on purpose. A node flagged ``stale-evidence`` returns
to ``active`` once a fresh evidence row lands, so the flag tracks current
reality rather than latching. Re-running the pass with no new evidence is a
no-op: no history rows, no events.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from ulid import ULID

from shrap.events import EventPublisher
from shrap.research.infra_mapper.store import (
    GRAPH_ACTIVE,
    NODE_ACTIVE,
    NODE_STALE_EVIDENCE,
    PostgresGraphStore,
)

PRODUCED_BY = "research/infra-mapper"
SCHEMA_VERSION = "1.0.0"
STREAM_GRAPHS_UPDATED = "research.graphs-updated"

# Spec default (Infra Mapper §Open questions: "Evidence freshness threshold").
# 180 days catches two quarterly filing cycles. Mike owns the real number; this
# is a documented first guess, not a calibration, and the CLI exposes it.
DEFAULT_FRESHNESS_DAYS = 180

# The only two statuses this pass moves between. Everything else is left alone.
MAINTAINED_STATUSES = (NODE_ACTIVE, NODE_STALE_EVIDENCE)


@dataclass(frozen=True, slots=True)
class StalenessVerdict:
    """One node's staleness evaluation. ``from_status == to_status`` means no
    transition — kept in the report so the CLI can show the whole graph, not
    just the movers."""

    graph_id: str
    ticker: str
    layer_role: str
    from_status: str
    to_status: str
    latest_evidence_at: datetime | None
    age_days: int | None
    reason: str

    @property
    def is_transition(self) -> bool:
        return self.from_status != self.to_status


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    """Outcome of one pass. Counts are over evaluated nodes only."""

    graphs_scanned: int
    verdicts: tuple[StalenessVerdict, ...]
    skipped_nodes: int
    dry_run: bool
    freshness_days: int

    @property
    def transitions(self) -> tuple[StalenessVerdict, ...]:
        return tuple(v for v in self.verdicts if v.is_transition)

    @property
    def flagged(self) -> tuple[StalenessVerdict, ...]:
        return tuple(v for v in self.transitions if v.to_status == NODE_STALE_EVIDENCE)

    @property
    def recovered(self) -> tuple[StalenessVerdict, ...]:
        return tuple(v for v in self.transitions if v.to_status == NODE_ACTIVE)

    def render(self) -> str:
        prefix = "[dry-run] " if self.dry_run else ""
        lines = [
            f"{prefix}staleness pass: {self.graphs_scanned} active graph(s), "
            f"{len(self.verdicts)} node(s) evaluated, {self.skipped_nodes} skipped, "
            f"threshold {self.freshness_days}d",
            f"  flagged stale: {len(self.flagged)}   recovered: {len(self.recovered)}   "
            f"unchanged: {len(self.verdicts) - len(self.transitions)}",
        ]
        for verdict in self.verdicts:
            age = "no evidence" if verdict.age_days is None else f"{verdict.age_days}d"
            marker = "->" if verdict.is_transition else "  "
            lines.append(
                f"  {marker} {verdict.ticker:6} {verdict.layer_role:20} "
                f"{verdict.from_status:15} {marker} {verdict.to_status:15} ({age})"
            )
        return "\n".join(lines)


def evaluate_staleness(
    nodes: list[dict[str, object]],
    latest_evidence: dict[tuple[str, str], datetime],
    *,
    now: datetime,
    freshness_days: int = DEFAULT_FRESHNESS_DAYS,
) -> tuple[tuple[StalenessVerdict, ...], int]:
    """Pure staleness evaluation. Returns ``(verdicts, skipped_count)``.

    ``latest_evidence`` maps ``(ticker, layer_role)`` to the newest evidence
    ``observed_at`` for that node. Nodes whose status is outside
    ``MAINTAINED_STATUSES`` are counted as skipped and produce no verdict.
    """

    if freshness_days <= 0:
        raise ValueError(f"freshness_days must be positive, got {freshness_days}")

    cutoff = now - timedelta(days=freshness_days)
    verdicts: list[StalenessVerdict] = []
    skipped = 0

    for node in nodes:
        status = str(node["status"])
        if status not in MAINTAINED_STATUSES:
            skipped += 1
            continue

        ticker = str(node["ticker"])
        layer_role = str(node["layer_role"])
        observed_at = latest_evidence.get((ticker, layer_role))

        if observed_at is None:
            to_status = NODE_STALE_EVIDENCE
            age_days = None
            reason = "no evidence rows recorded for this node"
        else:
            age_days = (now - observed_at).days
            if observed_at < cutoff:
                to_status = NODE_STALE_EVIDENCE
                reason = (
                    f"newest evidence is {age_days}d old, past the "
                    f"{freshness_days}d freshness threshold"
                )
            else:
                to_status = NODE_ACTIVE
                reason = f"newest evidence is {age_days}d old, within {freshness_days}d"

        verdicts.append(
            StalenessVerdict(
                graph_id=str(node["graph_id"]),
                ticker=ticker,
                layer_role=layer_role,
                from_status=status,
                to_status=to_status,
                latest_evidence_at=observed_at,
                age_days=age_days,
                reason=reason,
            )
        )

    return tuple(verdicts), skipped


class _RedisXAdd(Protocol):
    async def xadd(self, stream: str, fields: dict[str, str]) -> str: ...


async def run_maintenance_pass(
    store: PostgresGraphStore,
    redis: _RedisXAdd,
    *,
    now: datetime,
    freshness_days: int = DEFAULT_FRESHNESS_DAYS,
    dry_run: bool = False,
) -> MaintenanceReport:
    """Run the staleness pass over every active graph.

    Only transitions are persisted: an unchanged node writes no history row and
    emits no event, so a daily re-run on unchanged evidence is silent.
    """

    publisher = EventPublisher(redis)
    graphs = [g for g in await store.list_graphs() if g["status"] == GRAPH_ACTIVE]

    all_verdicts: list[StalenessVerdict] = []
    total_skipped = 0

    for graph in graphs:
        graph_id = str(graph["graph_id"])
        nodes = await store.nodes_for_graph(graph_id)
        latest_evidence = await store.latest_evidence_at(graph_id)
        verdicts, skipped = evaluate_staleness(
            nodes, latest_evidence, now=now, freshness_days=freshness_days
        )
        all_verdicts.extend(verdicts)
        total_skipped += skipped

        if dry_run:
            continue

        for verdict in verdicts:
            if not verdict.is_transition:
                continue
            await store.set_node_status(
                graph_id=verdict.graph_id,
                ticker=verdict.ticker,
                layer_role=verdict.layer_role,
                status=verdict.to_status,
            )
            await store.record_history(
                history_id=str(ULID()),
                graph_id=verdict.graph_id,
                ticker=verdict.ticker,
                layer_role=verdict.layer_role,
                from_status=verdict.from_status,
                to_status=verdict.to_status,
                from_confidence=None,
                to_confidence=None,
                reason=verdict.reason,
            )
            await publisher.publish(
                stream=STREAM_GRAPHS_UPDATED,
                produced_by=PRODUCED_BY,
                schema_version=SCHEMA_VERSION,
                payload={
                    "graph_id": verdict.graph_id,
                    "ticker": verdict.ticker,
                    "layer_role": verdict.layer_role,
                    "from_status": verdict.from_status,
                    "to_status": verdict.to_status,
                    "evidence_age_days": verdict.age_days,
                    "freshness_days": freshness_days,
                    "reason": verdict.reason,
                },
            )

    return MaintenanceReport(
        graphs_scanned=len(graphs),
        verdicts=tuple(all_verdicts),
        skipped_nodes=total_skipped,
        dry_run=dry_run,
        freshness_days=freshness_days,
    )


__all__ = [
    "DEFAULT_FRESHNESS_DAYS",
    "MAINTAINED_STATUSES",
    "STREAM_GRAPHS_UPDATED",
    "MaintenanceReport",
    "StalenessVerdict",
    "evaluate_staleness",
    "run_maintenance_pass",
]
