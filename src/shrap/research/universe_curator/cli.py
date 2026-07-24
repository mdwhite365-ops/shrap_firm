"""Approval CLI: ``shrap-universe-promote`` (Curator spec open question 2, CLI).

Mike's decision surface for Tier 2/3 state. Every subcommand is an explicit
human decision — there is no auto path. On the ``shrap-tech-watcher-promote``
precedent (PR #54).

Subcommands:

- ``seed``             record a Tier 2 watch entry (evidence_ref + expiry/falsifier)
- ``stage``            assemble a promotion/eviction proposal into staging
- ``approve``          approve a staged proposal (mutates Tier 3, emits events)
- ``reject``           reject a staged proposal (resolves it with a note)
- ``extend``           renew a watch entry's expiry clock
- ``expire``           expire a watch entry now
- ``load-launch-list`` load the locked Tier 3 launch list, idempotently
- ``list``             show current tiers and pending staged proposals
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import UTC, datetime
from typing import cast

from shrap.common.db import create_asyncpg_pool
from shrap.research.universe_curator.curator import (
    MECHANISM_MIKE_SEED,
    MECHANISMS,
    CuratorError,
    RedisStreamClient,
    approve_staged,
    expire_watch,
    extend_watch,
    format_consequences,
    load_launch_list,
    reject_staged,
    repo_profile_exists,
    seed_watch,
    stage_transition,
)
from shrap.research.universe_curator.store import PostgresUniverseStore


def _parse_date(value: str) -> datetime:
    """Parse a YYYY-MM-DD expiry into a UTC-midnight datetime."""

    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as e:
        raise CuratorError(f"invalid date {value!r}; expected YYYY-MM-DD") from e


async def _run(args: argparse.Namespace) -> str:
    from redis.asyncio import Redis

    redis = Redis.from_url(args.redis_url, decode_responses=True, socket_timeout=30)
    pool = await create_asyncpg_pool(args.dsn)
    store = PostgresUniverseStore(pool)
    client = cast(RedisStreamClient, redis)
    try:
        # Idempotent: creates the tier/staging tables if this DB predates the card.
        await store.ensure_schema()

        if args.action == "seed":
            expiry = _parse_date(args.expiry) if args.expiry else None
            result = await seed_watch(
                store,
                client,
                ticker=args.ticker,
                evidence_ref=args.evidence_ref,
                mechanism=args.mechanism,
                expiry=expiry,
                falsifier=args.falsifier,
                cik=args.cik,
            )
            return result.detail

        if args.action == "stage":
            result = await stage_transition(
                store,
                ticker=args.ticker,
                kind=args.kind,
                profile_exists=repo_profile_exists(args.repo_root),
                evidence_ref=args.evidence_ref,
                mechanism=args.mechanism,
                evict_ticker=args.evict,
            )
            return f"{result.detail}\nconsequences:\n{format_consequences(result.consequences)}"

        if args.action == "approve":
            result = await approve_staged(store, client, staging_id=args.staging_id, note=args.note)
            return result.detail

        if args.action == "reject":
            result = await reject_staged(store, client, staging_id=args.staging_id, note=args.note)
            return result.detail

        if args.action == "extend":
            result = await extend_watch(store, ticker=args.ticker, expiry=_parse_date(args.expiry))
            return result.detail

        if args.action == "expire":
            result = await expire_watch(store, client, ticker=args.ticker)
            return result.detail

        if args.action == "load-launch-list":
            loaded = await load_launch_list(store, client)
            return f"launch-list loaded: {len(loaded)} promoted ({', '.join(loaded) or 'none new'})"

        # list
        return await _render_list(store)
    finally:
        await redis.aclose()
        await pool.close()


async def _render_list(store: PostgresUniverseStore) -> str:
    active = await store.list_by_tier("active")
    watch = await store.list_by_tier("watch")
    pending = await store.pending_staging()
    lines = [
        f"Tier 3 (Active): {len(active)}",
        *[
            f"  {r['ticker']}  <{r['mechanism']}>  {r.get('profile_path') or '(grandfathered)'}"
            for r in active
        ],
        f"Tier 2 (Watch): {len(watch)}",
        *[f"  {r['ticker']}  <{r['mechanism']}>  expiry={r.get('expiry')}" for r in watch],
        f"Pending staged proposals: {len(pending)}",
        *[
            f"  {r['staging_id']}  {r['kind']} {r['ticker']}"
            + (f" (evicts {r['paired_eviction_ticker']})" if r["paired_eviction_ticker"] else "")
            for r in pending
        ],
    ]
    return "\n".join(lines)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dsn",
        default=os.environ.get(
            "UNIVERSE_CURATOR_POSTGRES_DSN", "postgresql://shrap:shrap@postgres:5432/shrap"
        ),
        help="Postgres DSN (default: UNIVERSE_CURATOR_POSTGRES_DSN env)",
    )
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("UNIVERSE_CURATOR_REDIS_URL", "redis://redis:6379/0"),
        help="Redis URL (default: UNIVERSE_CURATOR_REDIS_URL env)",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Universe Curator approval CLI — seed, stage, approve/reject, "
        "extend, expire, load the launch list."
    )
    _add_common(parser)
    sub = parser.add_subparsers(dest="action", required=True)
    mechanisms = sorted(MECHANISMS)

    seed = sub.add_parser("seed", help="Record a Tier 2 watch entry")
    seed.add_argument("--ticker", required=True)
    seed.add_argument("--evidence-ref", required=True, help="Resolvable pointer to the evidence")
    seed.add_argument("--mechanism", default=MECHANISM_MIKE_SEED, choices=mechanisms)
    seed.add_argument("--expiry", default=None, help="YYYY-MM-DD; required unless --falsifier")
    seed.add_argument("--falsifier", default=None, help="Observable falsifier condition")
    seed.add_argument("--cik", default=None, help="EDGAR CIK if known (else backfilled later)")

    stage = sub.add_parser("stage", help="Stage a Tier 3 promotion/eviction proposal")
    stage.add_argument("--ticker", required=True)
    stage.add_argument("--kind", required=True, choices=["promotion", "eviction"])
    stage.add_argument("--evidence-ref", default=None, help="Override; defaults from the watch row")
    stage.add_argument("--mechanism", default=None, choices=mechanisms)
    stage.add_argument("--evict", default=None, help="Ticker to evict if promoting at cap")
    stage.add_argument(
        "--repo-root",
        default=os.environ.get("UNIVERSE_CURATOR_REPO_ROOT", "."),
        help="Repo root for the docs/universe/<ticker>.md profile check",
    )

    approve = sub.add_parser("approve", help="Approve a staged proposal")
    approve.add_argument("--staging-id", required=True)
    approve.add_argument("--note", default=None, help="Optional decision note")

    reject = sub.add_parser("reject", help="Reject a staged proposal (note required)")
    reject.add_argument("--staging-id", required=True)
    reject.add_argument("--note", required=True, help="Why Mike rejected; preserved")

    extend = sub.add_parser("extend", help="Renew a watch entry's expiry")
    extend.add_argument("--ticker", required=True)
    extend.add_argument("--expiry", required=True, help="New expiry, YYYY-MM-DD")

    expire = sub.add_parser("expire", help="Expire a watch entry now")
    expire.add_argument("--ticker", required=True)

    sub.add_parser("load-launch-list", help="Load the locked Tier 3 launch list (idempotent)")
    sub.add_parser("list", help="Show current tiers and pending staged proposals")

    args = parser.parse_args()
    try:
        output = asyncio.run(_run(args))
    except CuratorError as e:
        raise SystemExit(f"refused: {e}") from e
    print(output)


if __name__ == "__main__":
    main()
