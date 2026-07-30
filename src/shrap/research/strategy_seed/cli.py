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
- ``load-technical``  insert a Framework #3 (``technical-catalyst``) seed by key.
  These carry no world-changer anchor, which the Evaluator only accepts since
  ADR-0013's archetype-conditional gates.
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
from shrap.research.strategy_seed.probe_strategies import (
    PROBE_SEEDS,
    PROBE_SEEDS_BY_KEY,
    probe_record,
)
from shrap.research.strategy_seed.technical_strategies import (
    MOMENTUM_SEEDS,
    MOMENTUM_SEEDS_BY_KEY,
    REVERSAL_SEEDS,
    REVERSAL_SEEDS_BY_KEY,
    TECHNICAL_SEEDS,
    TECHNICAL_SEEDS_BY_KEY,
    momentum_record,
    reversal_record,
    technical_record,
)

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


async def load_probe(registry: RegistryPort, key: str) -> str:
    """Insert one protocol-probe seed at ``hypothesis``, idempotently.

    Same dedup rule as :func:`load_first` — a row with the same ``spec_hash``
    means this probe is already registered and the call is a no-op.
    """

    seed = PROBE_SEEDS_BY_KEY.get(key)
    if seed is None:
        available = ", ".join(sorted(PROBE_SEEDS_BY_KEY))
        raise SystemExit(f"refused: unknown probe {key!r}; available: {available}")
    record = probe_record(seed)
    existing = await registry.get_by_spec_hash(record.spec_hash)
    if existing is not None:
        return (
            f"already present: {existing.strategy_id} ({existing.name}) "
            f"status={existing.status} — skipped (no duplicate)"
        )
    inserted = await registry.register(
        record,
        reason=f"Mike-seed: protocol probe ({seed.role}) exercising Evaluator verdict branches",
        actor=SEED_ACTOR,
        trigger_kind=SEED_TRIGGER_KIND,
    )
    if not inserted:
        return f"already present: {record.strategy_id} — skipped (strategy_id conflict)"
    return (
        f"loaded: {record.strategy_id} ({record.name}) at status={record.status}; "
        f"evaluate with `shrap-strategy-evaluate --strategy-id {record.strategy_id} --dry-run`"
    )


async def load_technical(registry: RegistryPort, key: str) -> str:
    """Insert one Framework #3 seed at ``hypothesis``, idempotently.

    Same dedup rule as the others. Unlike them, the record carries no anchor —
    which is only evaluable because ADR-0013 made the anchor gate
    archetype-conditional.
    """

    seed = TECHNICAL_SEEDS_BY_KEY.get(key)
    if seed is None:
        available = ", ".join(sorted(TECHNICAL_SEEDS_BY_KEY))
        raise SystemExit(f"refused: unknown technical seed {key!r}; available: {available}")
    record = technical_record(seed)
    existing = await registry.get_by_spec_hash(record.spec_hash)
    if existing is not None:
        return (
            f"already present: {existing.strategy_id} ({existing.name}) "
            f"status={existing.status} — skipped (no duplicate)"
        )
    inserted = await registry.register(
        record,
        reason="Mike-seed: first Framework #3 (technical-catalyst) strategy — no anchor",
        actor=SEED_ACTOR,
        trigger_kind=SEED_TRIGGER_KIND,
    )
    if not inserted:
        return f"already present: {record.strategy_id} — skipped (strategy_id conflict)"
    return (
        f"loaded: {record.strategy_id} ({record.name}) at status={record.status}; "
        f"evaluate with `shrap-strategy-evaluate --strategy-id {record.strategy_id} --dry-run`"
    )


async def load_momentum(registry: RegistryPort, key: str) -> str:
    """Insert one cross-sectional momentum seed at ``hypothesis``, idempotently.

    Unlike every other seed this one declares the whole launch universe, which
    is what lets it clear the trade-count gate honestly: the engine counts a
    trade per ticker per weight change, so breadth supplies sample size that a
    single-name daily rule cannot.
    """

    seed = MOMENTUM_SEEDS_BY_KEY.get(key)
    if seed is None:
        available = ", ".join(sorted(MOMENTUM_SEEDS_BY_KEY))
        raise SystemExit(f"refused: unknown momentum seed {key!r}; available: {available}")
    record = momentum_record(seed)
    existing = await registry.get_by_spec_hash(record.spec_hash)
    if existing is not None:
        return (
            f"already present: {existing.strategy_id} ({existing.name}) "
            f"status={existing.status} — skipped (no duplicate)"
        )
    inserted = await registry.register(
        record,
        reason="Mike-seed: cross-sectional momentum over the launch universe",
        actor=SEED_ACTOR,
        trigger_kind=SEED_TRIGGER_KIND,
    )
    if not inserted:
        return f"already present: {record.strategy_id} — skipped (strategy_id conflict)"
    return (
        f"loaded: {record.strategy_id} ({record.name}) at status={record.status} "
        f"over {len(seed.tickers)} tickers; evaluate with "
        f"`shrap-strategy-evaluate --strategy-id {record.strategy_id} --dry-run`\n"
        f"NOTE: every one of those {len(seed.tickers)} tickers needs daily bars in "
        f"market_data.daily_bars and tier 'active' in research.universe_tiers, or the "
        f"evaluation is REFUSED (not killed) with the first offending ticker named."
    )


async def load_reversal(registry: RegistryPort, key: str) -> str:
    """Insert one short-horizon reversal seed at ``hypothesis``, idempotently.

    Same universe and same machinery as the momentum seeds — the comparison
    between the two rules is the point, and a different universe would confound
    it with a selection difference.
    """

    seed = REVERSAL_SEEDS_BY_KEY.get(key)
    if seed is None:
        available = ", ".join(sorted(REVERSAL_SEEDS_BY_KEY))
        raise SystemExit(f"refused: unknown reversal seed {key!r}; available: {available}")
    record = reversal_record(seed)
    existing = await registry.get_by_spec_hash(record.spec_hash)
    if existing is not None:
        return (
            f"already present: {existing.strategy_id} ({existing.name}) "
            f"status={existing.status} — skipped (no duplicate)"
        )
    inserted = await registry.register(
        record,
        reason="Mike-seed: short-horizon cross-sectional reversal over the launch universe",
        actor=SEED_ACTOR,
        trigger_kind=SEED_TRIGGER_KIND,
    )
    if not inserted:
        return f"already present: {record.strategy_id} — skipped (strategy_id conflict)"
    tradeable = (
        "NOT TRADEABLE YET — the Runner cannot open a short; do not assign an account"
        if seed.long_short
        else "tradeable on the paper path, but it is a documented DEVIATION from the "
        "long/short construction the prior measures"
    )
    return (
        f"loaded: {record.strategy_id} ({record.name}) at status={record.status} "
        f"over {len(seed.tickers)} tickers; evaluate with "
        f"`shrap-strategy-evaluate --strategy-id {record.strategy_id} --dry-run`\n"
        f"NOTE: {tradeable}."
    )


def render_reversal_catalogue() -> str:
    """List the available reversal seeds without touching the database."""

    lines = [f"Reversal seeds: {len(REVERSAL_SEEDS)}"]
    for s in REVERSAL_SEEDS:
        leg = "long/short" if s.long_short else "long only"
        lines.append(f"  {s.key}  {s.strategy_id}  {s.name}  [{leg}]")
    return "\n".join(lines)


def render_momentum_catalogue() -> str:
    """List the available cross-sectional seeds without touching the database."""

    lines = [f"Momentum seeds: {len(MOMENTUM_SEEDS)}"]
    for s in MOMENTUM_SEEDS:
        lines.append(
            f"  {s.key:<24} lookback={s.lookback} skip={s.skip} top_n={s.top_n} "
            f"tickers={len(s.tickers)}  {s.strategy_id}"
        )
    return "\n".join(lines)


def render_technical_catalogue() -> str:
    """List the available Framework #3 seeds without touching the database."""

    lines = [f"Technical seeds: {len(TECHNICAL_SEEDS)}"]
    for s in TECHNICAL_SEEDS:
        lines.append(f"  {s.key:<16} {s.ticker:<6} fast={s.fast} slow={s.slow}  {s.strategy_id}")
    return "\n".join(lines)


def render_probe_catalogue() -> str:
    """List the available probe seeds without touching the database."""

    lines = [f"Probe seeds: {len(PROBE_SEEDS)}"]
    for s in PROBE_SEEDS:
        lines.append(f"  {s.key:<14} {s.role:<10} fast={s.fast} slow={s.slow}  {s.strategy_id}")
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> str:
    if args.action == "list-probes":
        return render_probe_catalogue()
    if args.action == "list-technical":
        return render_technical_catalogue()
    if args.action == "list-momentum":
        return render_momentum_catalogue()
    if args.action == "list-reversal":
        return render_reversal_catalogue()
    pool = await create_asyncpg_pool(args.dsn)
    registry = PostgresStrategyRegistry(pool)
    try:
        # Idempotent: creates the research.strategies table if this DB predates it.
        await registry.ensure_schema()
        if args.action == "load-first":
            return await load_first(registry)
        if args.action == "load-probe":
            return await load_probe(registry, args.key)
        if args.action == "load-technical":
            return await load_technical(registry, args.key)
        if args.action == "load-momentum":
            return await load_momentum(registry, args.key)
        if args.action == "load-reversal":
            return await load_reversal(registry, args.key)
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
    probe = sub.add_parser(
        "load-probe", help="Insert a protocol-probe strategy by key (idempotent)"
    )
    probe.add_argument("key", choices=sorted(PROBE_SEEDS_BY_KEY), help="Probe seed key")
    sub.add_parser("list-probes", help="Show available probe seeds (no database access)")
    technical = sub.add_parser(
        "load-technical", help="Insert a Framework #3 technical-catalyst seed (idempotent)"
    )
    technical.add_argument("key", choices=sorted(TECHNICAL_SEEDS_BY_KEY), help="Technical seed key")
    sub.add_parser("list-technical", help="Show available Framework #3 seeds (no database access)")

    momentum = sub.add_parser(
        "load-momentum", help="Insert a cross-sectional momentum seed (idempotent)"
    )
    momentum.add_argument("key", choices=sorted(MOMENTUM_SEEDS_BY_KEY), help="Momentum seed key")
    sub.add_parser("list-momentum", help="Show available momentum seeds (no database access)")

    reversal = sub.add_parser(
        "load-reversal", help="Insert a short-horizon reversal seed (idempotent)"
    )
    reversal.add_argument("key", choices=sorted(REVERSAL_SEEDS_BY_KEY), help="Reversal seed key")
    sub.add_parser("list-reversal", help="Show available reversal seeds (no database access)")
    sub.add_parser("list", help="Show research.strategies rows (id, name, archetype, status)")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    output = asyncio.run(_run(args))
    print(output)


if __name__ == "__main__":
    main()
