"""Daily synthesis pass (Tech Watcher spec Processing steps 3-7, v0).

Pipeline over relevant, not-yet-synthesized items:

1. **Cluster** — v0 clusters by archetype key from the filter verdict.
   Coarse but deterministic; LLM topic/entity clustering is a later
   refinement recorded in the spec.
2. **Triangulate** — every cluster's disposition is logged per pass to
   ``research.tech_watcher_cluster_log`` (KI-007 — pre-synthesis holds
   used to leave no trace). A cluster is promotable only with >=2 independent
   source classes (the spec's primary defense against marketing-driven
   false positives). Single-source clusters stay unsynthesized and wait:
   they re-enter every batch until a second source class corroborates or
   they age out. They are the ``seen-not-proposed`` population.
3. **Synthesize** — top-N promotable clusters (ranked by source breadth,
   then evidence count) each get one LLM call producing the spec's strict
   candidate JSON. The call goes to the ``cloud-default`` tier alias; in
   the local-only deployment that alias is env-routed to Ollama.
4. **Validate** — deterministic: required fields, allowed archetype,
   low/medium/high confidence (never a number), horizon enum, non-empty
   named-metric kill criteria, falsifier horizon. Failures are persisted
   as status=``rejected`` with the reason — the graveyard is the
   denominator (survivorship-bias rule).
5. **Persist + publish** — survivors are status=``proposed``, evidence
   rows written, items marked synthesized, one
   ``research.world-changer-proposed`` event each (payload by reference).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from ulid import ULID

from shrap.events import EventPublisher
from shrap.llm.registry import TIER_CLOUD_DEFAULT
from shrap.research.tech_watcher.archetypes import (
    ARCHETYPE_KEYS,
    archetype_impostor_block,
    archetype_prompt_block,
)
from shrap.research.tech_watcher.candidates import PostgresCandidateStore

log = structlog.get_logger(__name__)

PRODUCED_BY = "tech-watcher"
SCHEMA_VERSION = "1.0.0"
STREAM_WORLD_CHANGER_PROPOSED = "research.world-changer-proposed"

STATUS_PROPOSED = "proposed"
STATUS_REJECTED = "rejected"

ALLOWED_CONFIDENCE = frozenset({"low", "medium", "high"})
ALLOWED_HORIZONS = frozenset({"<1y", "1-3y", "3-5y", "5-10y", ">10y", "horizon unknown"})

SYNTHESIS_SYSTEM_PROMPT = (
    "You are the Tech Watcher candidate synthesizer for a research funnel. You "
    "receive a cluster of evidence items that all point at one world-changer "
    "archetype. Draft ONE candidate proposal as a JSON object with EXACTLY these "
    "fields:\n"
    '- "name": short identifier (lowercase, hyphenated)\n'
    '- "archetype": the archetype key you were given\n'
    '- "thesis": one paragraph stating what would change if this plays out\n'
    '- "confidence": "low", "medium", or "high" — NEVER a number or probability\n'
    '- "expected_impact_horizon": one of "<1y", "1-3y", "3-5y", "5-10y", ">10y", '
    'or "horizon unknown" (preferred over a fabricated one)\n'
    '- "kill_criteria": array of observable conditions, each naming a published '
    "metric and a threshold (e.g. \"vendor X's capacity guidance falls below 2x "
    'by FY27 earnings call") — never vague\n'
    '- "falsifier_horizon": the date (YYYY or YYYY-MM) by which at least one kill '
    "criterion is observable\n"
    '- "dependency_graph_seed": array of 3-10 supply-chain layer names that would '
    "appear in a dependency graph if this is real\n"
    "Respond with ONLY the JSON object. If the evidence does not support a "
    'coherent candidate, respond {"no_candidate": true, "reason": "..."}.'
)


@dataclass(frozen=True, slots=True)
class RelevantItem:
    """One filtered-relevant item entering clustering."""

    item_id: str
    source: str
    archetype: str
    title: str
    summary: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class Cluster:
    """One archetype cluster with its triangulation facts."""

    archetype: str
    items: tuple[RelevantItem, ...]

    @property
    def source_classes(self) -> tuple[str, ...]:
        return tuple(sorted({i.source for i in self.items}))

    @property
    def promotable(self) -> bool:
        return len(self.source_classes) >= 2


@dataclass(frozen=True, slots=True)
class SynthesisReport:
    """Counts from one synthesis batch."""

    batch_id: str
    items_relevant: int
    clusters: int
    clusters_promotable: int
    proposed: int
    rejected: int


class CompletionClient(Protocol):
    async def complete(
        self,
        tier: str,
        prompt: str,
        system: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        think: bool | None = None,
    ) -> Any: ...


class RedisStreamClient(Protocol):
    async def xadd(self, stream: str, fields: dict[str, str]) -> str: ...


SELECT_RELEVANT_UNSYNTHESIZED_SQL = """
SELECT item_id, source, title, summary, filter_result
FROM research.raw_source_items
WHERE filtered_at IS NOT NULL
  AND synthesized_at IS NULL
  AND (filter_result->>'relevant')::boolean IS TRUE
ORDER BY fetched_at
""".strip()

MARK_SYNTHESIZED_SQL = """
UPDATE research.raw_source_items
SET synthesized_at = $2
WHERE item_id = ANY($1::text[])
""".strip()


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


def build_clusters(items: Sequence[RelevantItem]) -> list[Cluster]:
    """Group relevant items by archetype (v0 clustering)."""

    by_archetype: dict[str, list[RelevantItem]] = {}
    for item in items:
        by_archetype.setdefault(item.archetype, []).append(item)
    return [Cluster(archetype=key, items=tuple(v)) for key, v in sorted(by_archetype.items())]


def validate_candidate(data: object, expected_archetype: str) -> str | None:
    """Return a rejection reason, or None if the candidate passes."""

    if not isinstance(data, dict):
        return "response is not a JSON object"
    if data.get("no_candidate") is True:
        return f"model declined: {str(data.get('reason', ''))[:200]}"
    for field in ("name", "archetype", "thesis", "confidence", "expected_impact_horizon"):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            return f"missing or empty field {field!r}"
    if data["archetype"] not in ARCHETYPE_KEYS:
        return f"unknown archetype {data['archetype']!r}"
    if data["archetype"] != expected_archetype:
        return f"archetype {data['archetype']!r} does not match cluster {expected_archetype!r}"
    if data["confidence"].strip().lower() not in ALLOWED_CONFIDENCE:
        return f"confidence must be low/medium/high, got {data['confidence']!r}"
    if data["expected_impact_horizon"] not in ALLOWED_HORIZONS:
        return f"invalid horizon {data['expected_impact_horizon']!r}"
    kill_criteria = data.get("kill_criteria")
    if (
        not isinstance(kill_criteria, list)
        or not kill_criteria
        or not all(isinstance(k, str) and k.strip() for k in kill_criteria)
    ):
        return "kill_criteria must be a non-empty list of strings"
    falsifier = data.get("falsifier_horizon")
    if not isinstance(falsifier, str) or not falsifier.strip():
        return "missing falsifier_horizon"
    return None


async def _load_relevant_items(pool: AsyncPool) -> list[RelevantItem]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(SELECT_RELEVANT_UNSYNTHESIZED_SQL)
    items: list[RelevantItem] = []
    for row in rows:
        result = row["filter_result"]
        parsed = json.loads(result) if isinstance(result, str) else result
        archetype = parsed.get("archetype") if isinstance(parsed, dict) else None
        if not isinstance(archetype, str) or archetype not in ARCHETYPE_KEYS:
            continue
        items.append(
            RelevantItem(
                item_id=str(row["item_id"]),
                source=str(row["source"]),
                archetype=archetype,
                title=str(row["title"]),
                summary=None if row["summary"] is None else str(row["summary"]),
                reason=str(parsed.get("reason", "")) if isinstance(parsed, dict) else "",
            )
        )
    return items


def _cluster_prompt(cluster: Cluster) -> str:
    lines = [
        f"Archetype vocabulary:\n{archetype_prompt_block()}",
        f"\nTarget archetype for this cluster: {cluster.archetype}",
        archetype_impostor_block(cluster.archetype),
        f"Source classes represented: {', '.join(cluster.source_classes)}",
        "\nEvidence items:",
    ]
    for item in cluster.items[:20]:
        summary = (item.summary or "")[:800]
        lines.append(f"- [{item.source}] {item.title}\n  filter-reason: {item.reason}\n  {summary}")
    return "\n".join(lines)


async def synthesis_pass(
    pool: AsyncPool,
    llm: CompletionClient,
    redis: RedisStreamClient,
    max_proposals: int = 10,
    tier: str = TIER_CLOUD_DEFAULT,
) -> SynthesisReport:
    """Run one synthesis batch end to end; returns the batch counts."""

    batch_id = str(ULID())
    ran_at = datetime.now(UTC)
    store = PostgresCandidateStore(pool)  # type: ignore[arg-type]
    publisher = EventPublisher(redis)

    items = await _load_relevant_items(pool)
    clusters = build_clusters(items)
    promotable = [c for c in clusters if c.promotable]
    promotable.sort(key=lambda c: (len(c.source_classes), len(c.items)), reverse=True)
    to_synthesize = promotable[:max_proposals]

    # KI-007: log every cluster's disposition before any LLM call, so a
    # triangulation hold or a crash mid-batch still leaves a queryable trace.
    synthesized_archetypes = {c.archetype for c in to_synthesize}
    for cluster in clusters:
        if cluster.archetype in synthesized_archetypes:
            outcome = "synthesized"
        elif cluster.promotable:
            outcome = "deferred-max-proposals"
        else:
            outcome = "held-single-source"
        await store.record_cluster(
            batch_id=batch_id,
            ran_at=ran_at,
            archetype=cluster.archetype,
            outcome=outcome,
            source_classes=list(cluster.source_classes),
            item_ids=[i.item_id for i in cluster.items],
        )

    proposed = 0
    rejected = 0
    llm_model = "none"
    for cluster in to_synthesize:
        result = await llm.complete(
            tier=tier,
            prompt=_cluster_prompt(cluster),
            system=SYNTHESIS_SYSTEM_PROMPT,
            json_mode=True,
            temperature=0.4,
        )
        llm_model = result.model
        try:
            data: object = json.loads(result.content)
        except json.JSONDecodeError:
            data = {"unparseable": result.content[:500]}
        rejection = validate_candidate(data, cluster.archetype)
        candidate_id = str(ULID())
        record = data if isinstance(data, dict) else {"raw": str(data)[:1000]}
        status = STATUS_REJECTED if rejection else STATUS_PROPOSED
        kill_criteria_raw = record.get("kill_criteria")
        seed_raw = record.get("dependency_graph_seed")
        await store.insert_candidate(
            candidate_id=candidate_id,
            name=str(record.get("name", f"unnamed-{cluster.archetype}"))[:100],
            archetype=cluster.archetype,
            status=status,
            thesis=str(record.get("thesis", ""))[:4000],
            confidence=str(record.get("confidence", "low")).lower()[:10],
            expected_impact_horizon=str(record.get("expected_impact_horizon", "horizon unknown"))[
                :20
            ],
            kill_criteria=kill_criteria_raw if isinstance(kill_criteria_raw, list) else [],
            falsifier_horizon=(
                str(record["falsifier_horizon"])[:20]
                if isinstance(record.get("falsifier_horizon"), str)
                else None
            ),
            dependency_graph_seed=seed_raw if isinstance(seed_raw, list) else None,
            source_classes=list(cluster.source_classes),
            score={"source_classes": len(cluster.source_classes), "evidence": len(cluster.items)},
            rejection_reason=rejection,
            llm_model=result.model,
            batch_id=batch_id,
            raw_response=record,
            created_at=ran_at,
            evidence=[(i.item_id, i.source, i.reason[:300]) for i in cluster.items],
        )
        item_ids = [i.item_id for i in cluster.items]
        async with pool.acquire() as conn:
            await conn.execute(MARK_SYNTHESIZED_SQL, item_ids, ran_at)
        if rejection:
            rejected += 1
            log.info(
                "tech_watcher.candidate_rejected",
                candidate_id=candidate_id,
                archetype=cluster.archetype,
                reason=rejection,
            )
        else:
            proposed += 1
            await publisher.publish(
                stream=STREAM_WORLD_CHANGER_PROPOSED,
                produced_by=PRODUCED_BY,
                schema_version=SCHEMA_VERSION,
                payload={
                    "candidate_id": candidate_id,
                    "name": str(record.get("name", "")),
                    "archetype": cluster.archetype,
                    "source_classes": list(cluster.source_classes),
                    "batch_id": batch_id,
                },
            )
            log.info(
                "tech_watcher.candidate_proposed",
                candidate_id=candidate_id,
                name=record.get("name"),
                archetype=cluster.archetype,
                source_classes=cluster.source_classes,
            )

    await store.insert_batch(
        batch_id=batch_id,
        ran_at=ran_at,
        items_filtered=0,
        items_relevant=len(items),
        clusters=len(clusters),
        clusters_promotable=len(promotable),
        candidates_proposed=proposed,
        candidates_rejected=rejected,
        llm_model=llm_model,
    )
    return SynthesisReport(
        batch_id=batch_id,
        items_relevant=len(items),
        clusters=len(clusters),
        clusters_promotable=len(promotable),
        proposed=proposed,
        rejected=rejected,
    )
