"""The loop that makes the proposer an agent instead of a command.

#156 shipped the Hypothesis Generator as a tools-profile CLI. Tech Watcher went
on filling ``research.literature_items`` hourly and **nothing ever read them**,
because nothing invoked the CLI. A funnel whose last stage runs only when a
person types a command is a funnel with a person in it.

This is that stage, on an interval. It mirrors ``strategy_evaluator``'s trigger
deliberately, down to the sweep-then-sleep shape — that service already proved
the pattern and a second dialect of the same loop is a second thing to reason
about at 3am.

**Why arming it is safe, stated as a bound rather than a hope.** This service
writes to ``research.strategies``, which is the sort of thing that deserves an
argument. A proposal's identity is ``(rule, factor)`` and an identity the firm
already holds is refused, so the proposer can mint **at most one lineage root
per implemented effect, ever**. Registry pollution is bounded by the size of the
scorer library, not by how long this runs or how much literature arrives. It
cannot flood the Evaluator and it cannot spend a lineage's promote budget on a
search.

And what it writes is ``hypothesis`` — a status that trades nothing. The
Evaluator still has to clear it, kills apply unattended, and a promote is
withheld for Mike (ADR-0015). ``HYPOTHESIS_GENERATOR_DRY_RUN=true`` is the kill
switch: the sweep still runs and still reports, and writes nothing.

**A quiet sweep is the normal case.** Most days there is no new literature, and
most literature produces a capability gap rather than a strategy. The log says
so either way — a service that only logged when it acted could not be told apart
from one that had stopped.
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

import httpx
import structlog

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.llm import TierLLMClient, TierRegistry, tracer_from_env
from shrap.llm.registry import TIER_LOCAL_HEAVY
from shrap.research.hypothesis_generator.generator import HypothesisGenerator
from shrap.research.hypothesis_generator.literature import PostgresLiteratureStore
from shrap.research.hypothesis_generator.store import PostgresGapStore
from shrap.research.strategy_registry import PostgresStrategyRegistry

log = structlog.get_logger(__name__)

PRODUCED_BY = "hypothesis-generator"

# Literature arrives at the speed of arXiv — a few dozen q-fin papers a day,
# ingested hourly. Sweeping faster than the feed produces would spend database
# round-trips to find nothing; sweeping much slower would let a backlog build
# behind a stage that is already the last one.
DEFAULT_SWEEP_INTERVAL_SECONDS = 3600.0

# Items per sweep. Not a throttle on proposals — `hypothesis_key` handles that
# structurally — but on model calls, so a large backlog is worked over several
# hours rather than in one burst against a rate limit.
DEFAULT_MAX_ITEMS = 25


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - non-POSIX
            pass


async def sweep(
    generator: HypothesisGenerator, literature: PostgresLiteratureStore, limit: int
) -> Any:
    """One pass: read pending literature, propose or record gaps, report."""

    items = await literature.pending(limit)
    if not items:
        log.info("hypothesis_generator.sweep_empty")
        return None
    report = await generator.run(items)
    log.info(
        "hypothesis_generator.sweep_complete",
        read=len(report.outcomes),
        proposed=len(report.proposed),
        gaps=sum(1 for o in report.outcomes if o.gap is not None),
        dry_run=report.dry_run,
    )
    return report


async def run(
    *,
    redis_url: str,
    postgres_dsn: str,
    service_name: str = PRODUCED_BY,
    log_level: str = "INFO",
    sweep_interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
    max_items: int = DEFAULT_MAX_ITEMS,
    tier: str = TIER_LOCAL_HEAVY,
    dry_run: bool = False,
    env: dict[str, str] | None = None,
) -> None:
    """Run the sweep loop until SIGTERM/SIGINT.

    ``redis_url`` is accepted and unused: this agent publishes no events yet,
    because nothing subscribes to ``research.hypothesis.proposed`` and building
    a publisher for an empty room is the pattern the Evaluator trigger's own
    docstring warns against. Taking the parameter keeps the service signature
    uniform with every other agent, so wiring one is not a special case.
    """

    configure_logging(service_name, log_level)
    pool = await create_asyncpg_pool(postgres_dsn)
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    try:
        literature = PostgresLiteratureStore(pool)
        gaps = PostgresGapStore(pool)
        registry = PostgresStrategyRegistry(pool)
        await literature.ensure_schema()
        await gaps.ensure_schema()
        await registry.ensure_schema()
        log.info(
            "hypothesis_generator.starting",
            sweep_interval_seconds=sweep_interval_seconds,
            max_items=max_items,
            tier=tier,
            dry_run=dry_run,
        )
        resolved_env = env if env is not None else dict(os.environ)
        async with httpx.AsyncClient(follow_redirects=True) as http:
            generator = HypothesisGenerator(
                llm=TierLLMClient(
                    TierRegistry(resolved_env), http, tracer=tracer_from_env(resolved_env, http)
                ),
                registry=registry,
                literature=literature,
                gaps=gaps,
                tier=tier,
                dry_run=dry_run,
            )
            while not stop.is_set():
                try:
                    await sweep(generator, literature, max_items)
                except Exception:
                    # A model outage or a malformed row must not end the
                    # service. Items stay unmarked and the next sweep retries
                    # them, which is the same recovery the literature filter
                    # relies on one stage upstream.
                    log.exception("hypothesis_generator.sweep_failed")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=sweep_interval_seconds)
                except TimeoutError:
                    pass
    finally:
        await pool.close()
        log.info("hypothesis_generator.stopped")


__all__ = [
    "DEFAULT_MAX_ITEMS",
    "DEFAULT_SWEEP_INTERVAL_SECONDS",
    "PRODUCED_BY",
    "run",
    "sweep",
]
