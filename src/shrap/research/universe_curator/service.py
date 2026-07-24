"""Universe Curator service loop — the daily watch-expiry sweep.

This is the exec container the ``shrap-universe-promote`` CLI ships in, and the
one scheduled behavior this card implements: a daily sweep that expires Tier 2
entries past their date with no renewal (spec §Watch expiry sweep). Date-expiry
only — falsifier observation belongs to the proposing sources (spec default,
open question 4), so no falsifier detection lives here.

Plain asyncio loop, like the other agents — no LangGraph. The loop can only
shrink attention (expire a stale watch entry); it never promotes, so a bug here
fails toward the safe direction (spec §Failure behavior).
"""

from __future__ import annotations

import asyncio
import signal
from typing import cast

import structlog
from redis.asyncio import Redis

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.research.universe_curator.curator import (
    PRODUCED_BY,
    RedisStreamClient,
    expiry_sweep,
)
from shrap.research.universe_curator.store import PostgresUniverseStore

log = structlog.get_logger(__name__)

# One day — the sweep cadence (spec: "Daily watch-expiry sweep over Tier 2").
DEFAULT_SWEEP_INTERVAL_SECONDS = 86400.0


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass


async def run_loop(
    store: PostgresUniverseStore,
    redis: RedisStreamClient,
    stop: asyncio.Event,
    interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
) -> None:
    """Run the expiry sweep on an interval until ``stop`` is set."""

    while not stop.is_set():
        try:
            expired = await expiry_sweep(store, redis)
            log.info("universe_curator.sweep_complete", expired=len(expired))
        except Exception:
            log.exception("universe_curator.sweep_failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass


async def run(
    redis_url: str,
    postgres_dsn: str,
    sweep_interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
    service_name: str = PRODUCED_BY,
    log_level: str = "INFO",
) -> None:
    """Run the Universe Curator service (daily watch-expiry sweep)."""

    configure_logging(service_name, log_level)
    log.info(
        "universe_curator.starting",
        redis_url=redis_url,
        postgres_dsn="***",
        sweep_interval_seconds=sweep_interval_seconds,
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    redis: Redis = Redis.from_url(redis_url, decode_responses=True, socket_timeout=30)
    pool = await create_asyncpg_pool(postgres_dsn)
    store = PostgresUniverseStore(pool)
    await store.ensure_schema()
    try:
        await run_loop(
            store,
            cast(RedisStreamClient, redis),
            stop=stop,
            interval_seconds=sweep_interval_seconds,
        )
    finally:
        await redis.aclose()
        await pool.close()
        log.info("universe_curator.stopped")


__all__ = ["DEFAULT_SWEEP_INTERVAL_SECONDS", "run", "run_loop"]
