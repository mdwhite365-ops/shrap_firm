"""``shrap-world-changer-observe`` — log and review thesis-level observations.

Mike's surface for recording what has happened to a promoted world-changer
since promotion, and for reading back the honest accounting. Sits beside
``shrap-tech-watcher-promote`` (which owns promote/kill/seed) and runs in the
same container.

    shrap-world-changer-observe add <world-changer-id> \\
        --observation "..." --evidence-ref "..." --origin issuer \\
        --bearing supports --observed-at 2026-07-06 [--hard] [--kill-criterion 0]

    shrap-world-changer-observe list <world-changer-id>

``--hard`` is opt-in on purpose: soft (narrative) is the safe default, so
forgetting the flag understates the evidence rather than inflating it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import UTC, datetime
from typing import Protocol, cast

from ulid import ULID

from shrap.common.db import create_asyncpg_pool
from shrap.events import EventPublisher
from shrap.research.tech_watcher.candidates import loaded_criteria
from shrap.research.tech_watcher.observations import (
    BEARINGS,
    ObservationError,
    PostgresObservationStore,
    render_summary,
    summarize,
    validate_observation,
)

PRODUCED_BY = "research/tech-watcher"
SCHEMA_VERSION = "1.0.0"
STREAM_WORLD_CHANGER_OBSERVED = "research.world-changer-observed"


class _RedisXAdd(Protocol):
    async def xadd(self, stream: str, fields: dict[str, str]) -> str: ...


def parse_observed_at(value: str) -> datetime:
    """Accept a plain date or a full ISO timestamp; assume UTC if naive."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


async def add_observation(
    store: PostgresObservationStore,
    redis: _RedisXAdd,
    *,
    world_changer_id: str,
    observation: str,
    evidence_ref: str,
    origin: str,
    bearing: str,
    hard: bool,
    kill_criterion_index: int | None,
    observed_at: datetime,
) -> str:
    thesis = await store.get_thesis(world_changer_id)
    if thesis is None:
        raise ObservationError(f"no world-changer with candidate_id '{world_changer_id}'")

    kill_criteria = loaded_criteria(thesis.get("kill_criteria"))
    validate_observation(
        observation=observation,
        evidence_ref=evidence_ref,
        origin=origin,
        bearing=bearing,
        kill_criterion_index=kill_criterion_index,
        kill_criteria=kill_criteria,
    )

    observation_id = str(ULID())
    await store.insert_observation(
        observation_id=observation_id,
        world_changer_id=world_changer_id,
        observation=observation,
        evidence_ref=evidence_ref,
        origin=origin,
        hard=hard,
        bearing=bearing,
        kill_criterion_index=kill_criterion_index,
        observed_at=observed_at,
    )
    await EventPublisher(redis).publish(
        stream=STREAM_WORLD_CHANGER_OBSERVED,
        produced_by=PRODUCED_BY,
        schema_version=SCHEMA_VERSION,
        payload={
            "observation_id": observation_id,
            "world_changer_id": world_changer_id,
            "bearing": bearing,
            "hard": hard,
            "kill_criterion_index": kill_criterion_index,
            "origin": origin,
            "observed_at": observed_at.isoformat(),
        },
    )

    rows = await store.observations_for(world_changer_id)
    summary = summarize(rows, kill_criteria)
    return f"recorded {observation_id}\n" + render_summary(str(thesis["name"]), summary, rows)


async def list_observations(store: PostgresObservationStore, world_changer_id: str) -> str:
    thesis = await store.get_thesis(world_changer_id)
    if thesis is None:
        raise ObservationError(f"no world-changer with candidate_id '{world_changer_id}'")
    kill_criteria = loaded_criteria(thesis.get("kill_criteria"))
    rows = await store.observations_for(world_changer_id)
    return render_summary(str(thesis["name"]), summarize(rows, kill_criteria), rows)


async def _run(args: argparse.Namespace) -> str:
    from redis.asyncio import Redis

    redis = Redis.from_url(args.redis_url, decode_responses=True, socket_timeout=30)
    pool = await create_asyncpg_pool(args.dsn)
    store = PostgresObservationStore(pool)
    try:
        await store.ensure_schema()
        if args.action == "add":
            return await add_observation(
                store,
                cast(_RedisXAdd, redis),
                world_changer_id=args.world_changer_id,
                observation=args.observation,
                evidence_ref=args.evidence_ref,
                origin=args.origin,
                bearing=args.bearing,
                hard=args.hard,
                kill_criterion_index=args.kill_criterion,
                observed_at=parse_observed_at(args.observed_at),
            )
        return await list_observations(store, args.world_changer_id)
    finally:
        await redis.aclose()
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Log thesis-level observations against a world-changer."
    )
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

    add = sub.add_parser("add", help="Record one observation")
    add.add_argument("world_changer_id")
    add.add_argument("--observation", required=True, help="What was observed, in plain words")
    add.add_argument("--evidence-ref", required=True, help="Pointer a human can resolve")
    add.add_argument(
        "--origin",
        required=True,
        help="Originating institution: issuer / research / gov:<agency>",
    )
    add.add_argument("--bearing", required=True, choices=list(BEARINGS))
    add.add_argument(
        "--hard",
        action="store_true",
        help="Evidence with legal/financial consequence (filing, award, rule). "
        "Default is soft (narrative) — announcements and promotional material.",
    )
    add.add_argument(
        "--kill-criterion",
        type=int,
        default=None,
        help="Index of the kill criterion this bears on. Omit if it bears on none — "
        "that omission is the point, and the summary counts it.",
    )
    add.add_argument("--observed-at", required=True, help="Date or ISO timestamp (UTC if naive)")

    listing = sub.add_parser("list", help="Show the log and its honest accounting")
    listing.add_argument("world_changer_id")

    args = parser.parse_args()
    try:
        print(asyncio.run(_run(args)))
    except ObservationError as exc:
        raise SystemExit(f"rejected: {exc}") from exc


if __name__ == "__main__":
    main()
