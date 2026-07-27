"""Mike-seed CLI: ``shrap-strategy-seed`` — create hypothesis-stage strategies.

There is no other way to create a ``hypothesis``-stage strategy for the
Evaluator to run against: the Hypothesis Generator is deferred and the registry
has no CLI. This is the Mike-seed path. On the ``shrap-universe-promote`` /
``shrap-tech-watcher-promote`` CLI precedent (PR #54, #75): plain argparse with
an env-var DSN default, no long-running loop. It writes only to
``research.strategies`` through the registry — no Redis (the registry does not
publish; the Strategy Librarian emits lifecycle events from the transition log).

Subcommands:

- ``load-first``  insert the firm's FIRST seeded strategy (a code constant) at
  status ``hypothesis``, idempotently — a re-run with the same ``spec_hash`` is
  a no-op (no duplicate row).
- ``list``        show ``research.strategies`` rows (id, name, archetype,
  status, tickers) so the strategy_id can be fed to ``shrap-strategy-evaluate``.

    docker compose run --rm strategy-evaluator shrap-strategy-seed load-first
    docker compose run --rm strategy-evaluator shrap-strategy-seed list
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence
from typing import Protocol

from shrap.common.db import create_asyncpg_pool
from shrap.research.strategy_registry import PostgresStrategyRegistry, StrategyRecord
from shrap.research.strategy_seed.first_strategy import first_strategy_record

SEED_ACTOR = "mike-seed"
SEED_REASON = "Mike-seed: first hypothesis strategy exercising the funnel -> Evaluator path"
SEED_TRIGGER_KIND = "mike-seed"


class RegistryPort(Protocol):
    """The registry surface the seed CLI needs (structural — Postgres or fake)."""

    async def ensure_schema(self) -> None: ...

    async def get_by_spec_hash(self, spec_hash: str) -> StrategyRecord | None: ...

    async def register(
        self,
        record: StrategyRecord,
        *,
        reason: str,
        actor: str,
        trigger_kind: str = ...,
        trigger_ref: str | None = ...,
    ) -> bool: ...

    async def list_all(self) -> list[StrategyRecord]: ...


async def load_first(registry: RegistryPort) -> str:
    """Insert the first seeded strategy at ``hypothesis``, idempotently.

    Skips if a row with the seed's ``spec_hash`` already exists (the dedup key),
    so a re-run never creates a duplicate. Returns a line describing what it did.
    """

    record = first_strategy_record()
    existing = await registry.get_by_spec_hash(record.spec_hash)
    if existing is not None:
        return (
            f"already present: {existing.strategy_id} ({existing.name}) "
            f"status={existing.status} spec_hash={record.spec_hash} — skipped (no duplicate)"
        )
    inserted = await registry.register(
        record,
        reason=SEED_REASON,
        actor=SEED_ACTOR,
        trigger_kind=SEED_TRIGGER_KIND,
    )
    if not inserted:
        # strategy_id already existed (a concurrent re-run raced us) — still a no-op.
        return f"already present: {record.strategy_id} — skipped (strategy_id conflict)"
    return (
        f"loaded: {record.strategy_id} ({record.name}) at status={record.status}; "
        f"evaluate with `shrap-strategy-evaluate --strategy-id {record.strategy_id}`"
    )


def _format_tickers(tickers: object) -> str:
    """Compact ticker rendering for the ``list`` output (long/short or a list)."""

    collected: list[str] = []
    if isinstance(tickers, dict):
        for key in ("long", "short"):
            vals = tickers.get(key)
            if isinstance(vals, list):
                collected += [str(v) for v in vals]
        if not collected:
            collected += [str(k) for k in tickers]
    elif isinstance(tickers, list):
        collected += [str(v) for v in tickers]
    return ", ".join(collected) if collected else "-"


def render_list(records: Sequence[StrategyRecord]) -> str:
    """Render the ``list`` output: one line per strategy, id first."""

    lines = [f"Strategies: {len(records)}"]
    for r in records:
        lines.append(
            f"  {r.strategy_id}  {r.name}  <{r.archetype}>  "
            f"[{r.status}]  {_format_tickers(r.tickers)}"
        )
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> str:
    pool = await create_asyncpg_pool(args.dsn)
    registry = PostgresStrategyRegistry(pool)
    try:
        # Idempotent: creates the research.strategies table if this DB predates it.
        await registry.ensure_schema()
        if args.action == "load-first":
            return await load_first(registry)
        # list
        return render_list(await registry.list_all())
    finally:
        await pool.close()


def _default_dsn() -> str:
    # Reuse the evaluator's DSN env where sensible — same database, same DSN,
    # so an operator need not set a second variable.
    return (
        os.environ.get("STRATEGY_SEED_POSTGRES_DSN")
        or os.environ.get("STRATEGY_EVALUATOR_POSTGRES_DSN")
        or "postgresql://shrap:shrap@postgres:5432/shrap"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Strategy seed CLI — create the firm's first hypothesis strategy "
        "and list research.strategies rows."
    )
    parser.add_argument(
        "--dsn",
        default=_default_dsn(),
        help="Postgres DSN (default: STRATEGY_SEED_POSTGRES_DSN or "
        "STRATEGY_EVALUATOR_POSTGRES_DSN env)",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("load-first", help="Insert the first seeded strategy (idempotent)")
    sub.add_parser("list", help="Show research.strategies rows (id, name, archetype, status)")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    output = asyncio.run(_run(args))
    print(output)


if __name__ == "__main__":
    main()
