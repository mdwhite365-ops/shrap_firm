"""``shrap-infra-mapper`` — load the hand-seeded graph and inspect graph state.

Month-2 CLI (deterministic; no LLM). Mirrors the ``shrap-strategy-seed`` /
``shrap-universe-promote`` precedents.

Subcommands:

- ``load-seed-graph`` — insert the first hand-seeded graph (``first_graph.py``)
  idempotently: skipped if a graph for its world-changer already exists. Emits
  ``research.graphs-initialized`` once and ``research.graphs-added`` per node,
  and writes append-only history + evidence rows.
- ``list`` — show graphs and their node counts by layer role and status.
- ``restamp-seed-evidence`` — one-time repair for a seed graph loaded before
  card 3 fixed the loader, whose evidence rows carry load time instead of the
  true observation date. Idempotent; ``--dry-run`` reports without writing.
- ``maintenance`` — deterministic staleness pass over every active graph:
  flags nodes whose newest evidence is past the freshness threshold, recovers
  nodes whose evidence has since been refreshed, and emits
  ``research.graphs-updated`` per transition. ``--dry-run`` reports without
  writing.
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
from shrap.research.infra_mapper.first_graph import (
    SEED_GRAPH_ID,
    SEED_GRAPH_TITLE,
    SEED_NODE_STATUS,
    SEED_NODES,
    SEED_WORLD_CHANGER_ID,
)
from shrap.research.infra_mapper.maintenance import (
    DEFAULT_FRESHNESS_DAYS,
    run_maintenance_pass,
)
from shrap.research.infra_mapper.restamp import restamp_seed_evidence
from shrap.research.infra_mapper.store import GRAPH_ACTIVE, NODE_ACTIVE, PostgresGraphStore

PRODUCED_BY = "research/infra-mapper"
SCHEMA_VERSION = "1.0.0"

STREAM_GRAPHS_INITIALIZED = "research.graphs-initialized"
STREAM_GRAPHS_ADDED = "research.graphs-added"
STREAM_GRAPHS_UPDATED = "research.graphs-updated"


class _RedisXAdd(Protocol):
    async def xadd(self, stream: str, fields: dict[str, str]) -> str: ...


async def load_seed_graph(store: PostgresGraphStore, redis: _RedisXAdd) -> str:
    """Insert the hand-seeded graph, idempotently. Returns a human summary."""

    existing = await store.get_graph_by_world_changer(SEED_WORLD_CHANGER_ID)
    if existing is not None:
        return (
            f"graph for {SEED_WORLD_CHANGER_ID} already present ({existing['graph_id']}); skipped"
        )

    publisher = EventPublisher(redis)
    await store.insert_graph(
        graph_id=SEED_GRAPH_ID,
        world_changer_id=SEED_WORLD_CHANGER_ID,
        title=SEED_GRAPH_TITLE,
        status=GRAPH_ACTIVE,
    )
    for node in SEED_NODES:
        await store.upsert_node(
            graph_id=SEED_GRAPH_ID,
            ticker=node.ticker,
            layer_role=node.layer_role,
            confidence=node.confidence,
            critical_path_status=node.critical_path_status,
            last_confirmed_evidence_ref=node.evidence_ref,
            kill_criteria=node.kill_criteria,
            status=SEED_NODE_STATUS,
        )
        await store.insert_evidence(
            evidence_id=str(ULID()),
            graph_id=SEED_GRAPH_ID,
            ticker=node.ticker,
            layer_role=node.layer_role,
            evidence_ref=node.evidence_ref,
            source_class=node.evidence_source_class,
            # The date the evidence was observed, not the date we loaded it.
            # Load-time stamps would make 2024 deals look brand new and the
            # staleness pass would report a graph of aging refs as fresh.
            observed_at=node.evidence_observed_at,
        )
        await store.record_history(
            history_id=str(ULID()),
            graph_id=SEED_GRAPH_ID,
            ticker=node.ticker,
            layer_role=node.layer_role,
            from_status=None,
            to_status=SEED_NODE_STATUS,
            from_confidence=None,
            to_confidence=node.confidence,
            reason="hand-seeded Month-2 graph",
        )
        await publisher.publish(
            stream=STREAM_GRAPHS_ADDED,
            produced_by=PRODUCED_BY,
            schema_version=SCHEMA_VERSION,
            payload={
                "graph_id": SEED_GRAPH_ID,
                "world_changer_id": SEED_WORLD_CHANGER_ID,
                "ticker": node.ticker,
                "layer_role": node.layer_role,
                "confidence": node.confidence,
                "critical_path_status": node.critical_path_status,
            },
        )
    await publisher.publish(
        stream=STREAM_GRAPHS_INITIALIZED,
        produced_by=PRODUCED_BY,
        schema_version=SCHEMA_VERSION,
        payload={
            "graph_id": SEED_GRAPH_ID,
            "world_changer_id": SEED_WORLD_CHANGER_ID,
            "title": SEED_GRAPH_TITLE,
            "node_count": len(SEED_NODES),
        },
    )
    return f"graph {SEED_GRAPH_ID} initialized with {len(SEED_NODES)} nodes"


async def render_list(store: PostgresGraphStore) -> str:
    graphs = await store.list_graphs()
    lines = [f"Graphs: {len(graphs)}"]
    for graph in graphs:
        nodes = await store.nodes_for_graph(str(graph["graph_id"]))
        active = sum(1 for n in nodes if n["status"] == NODE_ACTIVE)
        lines.append(
            f"  {graph['graph_id']}  [{graph['status']}]  {graph['title']}  "
            f"({len(nodes)} nodes, {active} active)"
        )
        for node in nodes:
            lines.append(
                f"    {node['ticker']:6} {node['layer_role']:20} "
                f"{node['confidence']:6} {node['critical_path_status']}  [{node['status']}]"
            )
    return "\n".join(lines)


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _run(args: argparse.Namespace) -> str:
    from redis.asyncio import Redis

    redis = Redis.from_url(args.redis_url, decode_responses=True, socket_timeout=30)
    pool = await create_asyncpg_pool(args.dsn)
    store = PostgresGraphStore(pool)
    try:
        await store.ensure_schema()
        if args.action == "load-seed-graph":
            return await load_seed_graph(store, cast(_RedisXAdd, redis))
        if args.action == "restamp-seed-evidence":
            restamp_report = await restamp_seed_evidence(store, dry_run=args.dry_run)
            return restamp_report.render()
        if args.action == "maintenance":
            maintenance_report = await run_maintenance_pass(
                store,
                cast(_RedisXAdd, redis),
                now=_utcnow(),
                freshness_days=args.freshness_days,
                dry_run=args.dry_run,
            )
            return maintenance_report.render()
        return await render_list(store)
    finally:
        await redis.aclose()
        await pool.close()


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dsn",
        default=os.environ.get(
            "INFRA_MAPPER_POSTGRES_DSN", "postgresql://shrap:shrap@postgres:5432/shrap"
        ),
        help="Postgres DSN (default: INFRA_MAPPER_POSTGRES_DSN env)",
    )
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("INFRA_MAPPER_REDIS_URL", "redis://redis:6379/0"),
        help="Redis URL (default: INFRA_MAPPER_REDIS_URL env)",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Infrastructure Mapper CLI (Month-2).")
    _add_common(parser)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("load-seed-graph", help="Load the hand-seeded graph (idempotent)")
    sub.add_parser("list", help="Show graphs and their nodes")
    restamp = sub.add_parser(
        "restamp-seed-evidence",
        help="Repair seed evidence rows stamped with load time (one-time, idempotent)",
    )
    restamp.add_argument(
        "--dry-run",
        action="store_true",
        help="Report corrections without writing",
    )
    maint = sub.add_parser("maintenance", help="Run the deterministic staleness pass")
    maint.add_argument(
        "--freshness-days",
        type=int,
        default=DEFAULT_FRESHNESS_DAYS,
        help=f"Evidence freshness threshold in days (default: {DEFAULT_FRESHNESS_DAYS})",
    )
    maint.add_argument(
        "--dry-run",
        action="store_true",
        help="Report transitions without writing status, history, or events",
    )
    args = parser.parse_args()
    print(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
