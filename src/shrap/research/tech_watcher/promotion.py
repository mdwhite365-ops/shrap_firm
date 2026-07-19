"""Promotion workflow: Mike's promote/kill action and the Mike-seed path.

Spec (docs/agents/research/tech-watcher.md, steps 8-9): promotion is Mike's
call — the agent never auto-promotes. His promote action emits
``research.world-changer-promoted`` (the Infrastructure Mapper's trigger).
A kill must preserve the reason — the graveyard is the hit-rate
denominator — and emits ``research.world-changer-killed``.

The seed path (DQ-007, 2026-07-18) lets Mike enter a candidate he is
tracking informally. A seeded candidate gets ``source_class: mike-seed``
and the same falsifier discipline as pipeline proposals: kill criteria and
a falsifier horizon are required, and the Watcher's anti-duplication check
then attaches future pipeline evidence to it instead of proposing twins.

CLI: ``shrap-tech-watcher-promote {promote,kill,seed} ...``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from ulid import ULID

from shrap.common.db import create_asyncpg_pool
from shrap.events import EventPublisher
from shrap.research.tech_watcher.archetypes import ARCHETYPE_KEYS
from shrap.research.tech_watcher.candidates import AsyncPool, PostgresCandidateStore
from shrap.research.tech_watcher.synthesis import (
    PRODUCED_BY,
    SCHEMA_VERSION,
    STATUS_PROPOSED,
    STREAM_WORLD_CHANGER_PROPOSED,
    RedisStreamClient,
)

log = structlog.get_logger(__name__)

STATUS_UNDER_REVIEW = "under-review"
STATUS_PROMOTED = "promoted"
STATUS_KILLED = "killed"

SOURCE_CLASS_MIKE_SEED = "mike-seed"

STREAM_WORLD_CHANGER_PROMOTED = "research.world-changer-promoted"
STREAM_WORLD_CHANGER_KILLED = "research.world-changer-killed"

# States a decision may act on. A promoted candidate stays killable: the
# spec's step 8 keeps watching kill criteria after promotion.
_PROMOTABLE_FROM = frozenset({STATUS_PROPOSED, STATUS_UNDER_REVIEW})
_KILLABLE_FROM = frozenset({STATUS_PROPOSED, STATUS_UNDER_REVIEW, STATUS_PROMOTED})

SELECT_CANDIDATE_SQL = """
SELECT candidate_id, name, archetype, status, source_classes
FROM research.world_changers
WHERE candidate_id = $1
""".strip()

UPDATE_DECISION_SQL = """
UPDATE research.world_changers
SET status = $2, decided_at = $3, decision_note = $4
WHERE candidate_id = $1
""".strip()


class DecisionError(Exception):
    """The requested decision is not valid for this candidate."""


@dataclass(frozen=True, slots=True)
class Decision:
    """Outcome of a promote/kill/seed action."""

    candidate_id: str
    name: str
    archetype: str
    status: str
    stream: str


def _source_classes(raw: object) -> list[str]:
    if isinstance(raw, str):
        try:
            loaded: object = json.loads(raw)
        except json.JSONDecodeError:
            return []
        raw = loaded
    if isinstance(raw, list):
        return [str(v) for v in raw]
    return []


async def _decide(
    pool: AsyncPool,
    redis: RedisStreamClient,
    candidate_id: str,
    *,
    new_status: str,
    allowed_from: frozenset[str],
    note: str | None,
    stream: str,
    extra_payload: dict[str, Any],
) -> Decision:
    decided_at = datetime.now(UTC)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(SELECT_CANDIDATE_SQL, candidate_id)
        if row is None:
            raise DecisionError(f"candidate {candidate_id} does not exist")
        current = str(row["status"])
        if current not in allowed_from:
            raise DecisionError(
                f"candidate {candidate_id} is '{current}'; "
                f"{new_status} requires one of {sorted(allowed_from)}"
            )
        await conn.execute(UPDATE_DECISION_SQL, candidate_id, new_status, decided_at, note)
    publisher = EventPublisher(redis)
    await publisher.publish(
        stream=stream,
        produced_by=PRODUCED_BY,
        schema_version=SCHEMA_VERSION,
        payload={
            "candidate_id": candidate_id,
            "name": str(row["name"]),
            "archetype": str(row["archetype"]),
            "source_classes": _source_classes(row["source_classes"]),
            "previous_status": current,
            "decided_at": decided_at.isoformat(),
            **extra_payload,
        },
    )
    decision = Decision(
        candidate_id=candidate_id,
        name=str(row["name"]),
        archetype=str(row["archetype"]),
        status=new_status,
        stream=stream,
    )
    log.info(
        "tech_watcher.decision",
        candidate_id=candidate_id,
        status=new_status,
        stream=stream,
    )
    return decision


async def promote_candidate(
    pool: AsyncPool,
    redis: RedisStreamClient,
    candidate_id: str,
    note: str | None = None,
) -> Decision:
    """Mike promotes a candidate; emits the Infrastructure Mapper's trigger."""

    return await _decide(
        pool,
        redis,
        candidate_id,
        new_status=STATUS_PROMOTED,
        allowed_from=_PROMOTABLE_FROM,
        note=note,
        stream=STREAM_WORLD_CHANGER_PROMOTED,
        extra_payload={"note": note},
    )


async def kill_candidate(
    pool: AsyncPool,
    redis: RedisStreamClient,
    candidate_id: str,
    reason: str,
) -> Decision:
    """Mike kills a candidate; the reason is mandatory and preserved."""

    if not reason.strip():
        raise DecisionError("a kill requires a reason — the graveyard is the denominator")
    return await _decide(
        pool,
        redis,
        candidate_id,
        new_status=STATUS_KILLED,
        allowed_from=_KILLABLE_FROM,
        note=reason,
        stream=STREAM_WORLD_CHANGER_KILLED,
        extra_payload={"reason": reason},
    )


async def seed_candidate(
    pool: AsyncPool,
    redis: RedisStreamClient,
    *,
    name: str,
    archetype: str,
    thesis: str,
    kill_criteria: list[str],
    falsifier_horizon: str,
    confidence: str = "medium",
    expected_impact_horizon: str = "unknown",
    note: str | None = None,
) -> Decision:
    """Enter a Mike-tracked candidate under the pipeline's falsifier discipline."""

    if archetype not in ARCHETYPE_KEYS:
        raise DecisionError(f"unknown archetype '{archetype}'; allowed: {sorted(ARCHETYPE_KEYS)}")
    criteria = [c.strip() for c in kill_criteria if c.strip()]
    if not criteria:
        raise DecisionError("a seed requires at least one observable kill criterion")
    if not falsifier_horizon.strip():
        raise DecisionError("a seed requires a falsifier horizon")
    candidate_id = str(ULID())
    created_at = datetime.now(UTC)
    store = PostgresCandidateStore(pool)
    await store.insert_candidate(
        candidate_id=candidate_id,
        name=name[:100],
        archetype=archetype,
        status=STATUS_PROPOSED,
        thesis=thesis[:4000],
        confidence=confidence.lower()[:10],
        expected_impact_horizon=expected_impact_horizon[:20],
        kill_criteria=list(criteria),
        falsifier_horizon=falsifier_horizon[:20],
        dependency_graph_seed=None,
        source_classes=[SOURCE_CLASS_MIKE_SEED],
        score=None,
        rejection_reason=None,
        llm_model=SOURCE_CLASS_MIKE_SEED,
        batch_id=SOURCE_CLASS_MIKE_SEED,
        raw_response={"seeded_by": "mike", "note": note},
        created_at=created_at,
    )
    publisher = EventPublisher(redis)
    await publisher.publish(
        stream=STREAM_WORLD_CHANGER_PROPOSED,
        produced_by=PRODUCED_BY,
        schema_version=SCHEMA_VERSION,
        payload={
            "candidate_id": candidate_id,
            "name": name,
            "archetype": archetype,
            "source_classes": [SOURCE_CLASS_MIKE_SEED],
            "batch_id": SOURCE_CLASS_MIKE_SEED,
        },
    )
    log.info(
        "tech_watcher.candidate_seeded",
        candidate_id=candidate_id,
        name=name,
        archetype=archetype,
    )
    return Decision(
        candidate_id=candidate_id,
        name=name,
        archetype=archetype,
        status=STATUS_PROPOSED,
        stream=STREAM_WORLD_CHANGER_PROPOSED,
    )


async def _run(args: argparse.Namespace) -> Decision:
    from redis.asyncio import Redis

    redis = Redis.from_url(args.redis_url, decode_responses=True, socket_timeout=30)
    pool = await create_asyncpg_pool(args.dsn)
    try:
        # Idempotent: adds the decision columns if this DB predates the card.
        await PostgresCandidateStore(pool).ensure_schema()
        client = cast(RedisStreamClient, redis)
        if args.action == "promote":
            return await promote_candidate(pool, client, args.candidate_id, note=args.note)
        if args.action == "kill":
            return await kill_candidate(pool, client, args.candidate_id, reason=args.reason)
        return await seed_candidate(
            pool,
            client,
            name=args.name,
            archetype=args.archetype,
            thesis=args.thesis,
            kill_criteria=args.kill_criterion,
            falsifier_horizon=args.falsifier_horizon,
            confidence=args.confidence,
            expected_impact_horizon=args.impact_horizon,
            note=args.note,
        )
    finally:
        await redis.aclose()
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote, kill, or seed world-changer candidates.")
    parser.add_argument(
        "--dsn",
        default=os.environ.get(
            "TECH_WATCHER_POSTGRES_DSN", "postgresql://shrap:shrap@postgres:5432/shrap"
        ),
        help="Postgres DSN (default: TECH_WATCHER_POSTGRES_DSN env)",
    )
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("TECH_WATCHER_REDIS_URL", "redis://redis:6379/0"),
        help="Redis URL (default: TECH_WATCHER_REDIS_URL env)",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    promote = sub.add_parser("promote", help="Promote a candidate (Mapper trigger)")
    promote.add_argument("candidate_id")
    promote.add_argument("--note", default=None, help="Optional decision note")

    kill = sub.add_parser("kill", help="Kill a candidate (reason required)")
    kill.add_argument("candidate_id")
    kill.add_argument("--reason", required=True, help="Why the candidate dies; preserved forever")

    seed = sub.add_parser("seed", help="Seed a Mike-tracked candidate")
    seed.add_argument("--name", required=True)
    seed.add_argument("--archetype", required=True, choices=sorted(ARCHETYPE_KEYS))
    seed.add_argument("--thesis", required=True)
    seed.add_argument(
        "--kill-criterion",
        action="append",
        required=True,
        help="Observable kill condition; repeat the flag for each criterion",
    )
    seed.add_argument("--falsifier-horizon", required=True, help="e.g. 2027-06")
    seed.add_argument("--confidence", default="medium", choices=["low", "medium", "high"])
    seed.add_argument("--impact-horizon", default="unknown", help="e.g. 2-5y")
    seed.add_argument("--note", default=None)

    args = parser.parse_args()
    try:
        decision = asyncio.run(_run(args))
    except DecisionError as e:
        raise SystemExit(f"refused: {e}") from e
    print(
        f"{decision.status}: {decision.name} ({decision.candidate_id}) "
        f"[{decision.archetype}] -> {decision.stream}"
    )


if __name__ == "__main__":
    main()
