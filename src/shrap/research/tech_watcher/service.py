"""Tech Watcher ingest service loop (slice A — deterministic, no LLM).

Each pass walks the configured sources. Per source: fetch → idempotent
batch upsert with atomic cursor advance → ``ingestion.heartbeat`` event so
the Health Monitor sees freshness. A failing source publishes
``operations.health-anomaly`` and the pass continues with the remaining
sources (spec §Failure behavior: single-source outage degrades throughput,
never corrupts output).
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol, cast

import httpx
import structlog
from redis.asyncio import Redis

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.events import EventPublisher
from shrap.research.tech_watcher.sources import (
    ArxivSource,
    EdgarSource,
    HTTPClient,
    RawSourceItem,
)
from shrap.research.tech_watcher.store import PostgresRawItemStore

log = structlog.get_logger(__name__)

PRODUCED_BY = "tech-watcher"
SCHEMA_VERSION = "1.0.0"
STREAM_INGESTION_HEARTBEAT = "ingestion.heartbeat"
STREAM_HEALTH_ANOMALY = "operations.health-anomaly"


class Source(Protocol):
    @property
    def name(self) -> str: ...

    async def fetch(self, http: HTTPClient, timeout: float = 30.0) -> list[RawSourceItem]: ...


class RawItemStore(Protocol):
    async def upsert_batch(
        self, source: str, items: Sequence[RawSourceItem], fetched_at: datetime
    ) -> int: ...


class RedisStreamClient(Protocol):
    async def xadd(self, stream: str, fields: dict[str, str]) -> str: ...


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass


async def ingest_pass(
    sources: Sequence[Source],
    http: HTTPClient,
    store: RawItemStore,
    redis: RedisStreamClient,
    timeout: float = 30.0,
) -> dict[str, int]:
    """Run one ingest pass across all sources; returns inserted counts.

    A source that raises is reported (``operations.health-anomaly``) and
    skipped; it never aborts the pass.
    """

    publisher = EventPublisher(redis)
    inserted_by_source: dict[str, int] = {}
    for source in sources:
        fetched_at = datetime.now(UTC)
        try:
            items = await source.fetch(http, timeout=timeout)
            inserted = await store.upsert_batch(source.name, items, fetched_at)
        except Exception as e:
            log.exception("tech_watcher.source_failed", source=source.name)
            await publisher.publish(
                stream=STREAM_HEALTH_ANOMALY,
                produced_by=PRODUCED_BY,
                schema_version=SCHEMA_VERSION,
                payload={
                    "agent": PRODUCED_BY,
                    "kind": "source-ingest-failed",
                    "source": source.name,
                    "error": str(e)[:500],
                },
            )
            continue
        inserted_by_source[source.name] = inserted
        await publisher.publish(
            stream=STREAM_INGESTION_HEARTBEAT,
            produced_by=PRODUCED_BY,
            schema_version=SCHEMA_VERSION,
            payload={
                "source": source.name,
                "fetched": len(items),
                "inserted": inserted,
                "fetched_at": fetched_at.isoformat(),
            },
        )
        log.info(
            "tech_watcher.source_ingested",
            source=source.name,
            fetched=len(items),
            inserted=inserted,
        )
    return inserted_by_source


async def run_loop(
    sources: Sequence[Source],
    http: HTTPClient,
    store: RawItemStore,
    redis: RedisStreamClient,
    stop: asyncio.Event,
    interval_seconds: float = 3600.0,
    timeout: float = 30.0,
) -> None:
    """Run ingest passes on a simple interval until ``stop`` is set."""

    while not stop.is_set():
        try:
            counts = await ingest_pass(sources, http, store, redis, timeout=timeout)
            log.info("tech_watcher.pass_complete", inserted=counts)
        except Exception:
            log.exception("tech_watcher.pass_failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass


async def run(
    redis_url: str,
    postgres_dsn: str,
    sec_user_agent: str,
    edgar_forms: tuple[str, ...] = ("10-K", "10-Q", "8-K"),
    arxiv_categories: tuple[str, ...] = ("cs.AI", "cs.LG", "cond-mat", "q-bio.NC"),
    max_results: int = 100,
    interval_seconds: float = 3600.0,
    http_timeout: float = 30.0,
    service_name: str = PRODUCED_BY,
    log_level: str = "INFO",
) -> None:
    """Run the Tech Watcher ingest service until SIGINT/SIGTERM."""

    configure_logging(service_name, log_level)
    log.info(
        "tech_watcher.starting",
        redis_url=redis_url,
        postgres_dsn="***",
        edgar_forms=list(edgar_forms),
        arxiv_categories=list(arxiv_categories),
        interval_seconds=interval_seconds,
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    redis: Redis = Redis.from_url(redis_url, decode_responses=True, socket_timeout=30)
    pool = await create_asyncpg_pool(postgres_dsn)
    store = PostgresRawItemStore(pool)
    await store.ensure_schema()
    sources: list[Source] = [
        EdgarSource(user_agent=sec_user_agent, forms=edgar_forms, max_results=max_results),
        ArxivSource(categories=arxiv_categories, max_results=max_results),
    ]
    async with httpx.AsyncClient(follow_redirects=True) as http:
        try:
            await run_loop(
                sources,
                cast(HTTPClient, http),
                store,
                cast(RedisStreamClient, redis),
                stop=stop,
                interval_seconds=interval_seconds,
                timeout=http_timeout,
            )
        finally:
            await redis.aclose()
            await pool.close()
            log.info("tech_watcher.stopped")


__all__ = [
    "PRODUCED_BY",
    "STREAM_HEALTH_ANOMALY",
    "STREAM_INGESTION_HEARTBEAT",
    "ingest_pass",
    "run",
    "run_loop",
]
