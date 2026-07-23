"""News Analyzer service loop (spec §Processing / §Trigger).

Each pass: read the current market phase for cadence, fetch news since the
cursor (idempotent upsert, atomic cursor advance), bulk-score unscored items
on the local tier, escalate material items to the cloud tier, publish
materiality>=1 signals on ``intelligence.signal``, and emit an ingestion
heartbeat with the pass counts. Every verdict appends to the history table
BEFORE the item is marked scored (KI-007 crash-ordering: a crash between the
two re-scores the item rather than losing the verdict).

Market-phase cadence is read fresh each iteration from
``operations.market-phase``; an empty or unreadable stream falls back to the
active (10-minute) interval and logs it.
"""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

import httpx
import structlog
from redis.asyncio import Redis

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.events import EventPublisher, RedisPublisher
from shrap.intelligence.market_data import AlpacaMarketDataSettings
from shrap.intelligence.market_phase import (
    DEFAULT_ACTIVE_INTERVAL_SECONDS,
    DEFAULT_IDLE_INTERVAL_SECONDS,
    STREAM_MARKET_PHASE,
    PhaseRedis,
    interval_for_phase,
    read_latest_phase,
)
from shrap.intelligence.news_analyzer.client import NEWS_SOURCE, AlpacaNewsClient, NewsItem
from shrap.intelligence.news_analyzer.scorer import (
    NEWS_PROMPT_VERSION,
    NEWS_SYSTEM_PROMPT,
    CompletionClient,
    MaterialityVerdict,
    build_prompt,
    higher_verdict,
    parse_news_response,
)
from shrap.intelligence.news_analyzer.store import PostgresNewsStore, ScorableItem
from shrap.llm import TierLLMClient, TierRegistry
from shrap.llm.registry import TIER_CLOUD_DEFAULT, TIER_LOCAL_CLASSIFICATION
from shrap.trading_floor.alpaca import AsyncHttpClient

log = structlog.get_logger(__name__)

PRODUCED_BY = "intelligence/news-analyzer"
SCHEMA_VERSION = "1.0.0"
SIGNAL_TYPE = "news"

STREAM_INTELLIGENCE_SIGNAL = "intelligence.signal"
STREAM_INGESTION_HEARTBEAT = "ingestion.heartbeat"

# Market-phase cadence (interval_for_phase / read_latest_phase / PhaseRedis /
# the interval defaults and STREAM_MARKET_PHASE) lives in
# ``shrap.intelligence.market_phase`` and is shared with the Filing Processor;
# re-exported here so callers and tests keep importing it from this module.


class NewsSource(Protocol):
    async def get_news(
        self,
        http_client: AsyncHttpClient,
        symbols: list[str],
        start: str,
        limit: int = 50,
    ) -> list[NewsItem]: ...


class NewsStore(Protocol):
    async def cursor_ts(self, feed: str) -> datetime | None: ...

    async def upsert_items(
        self, feed: str, items: Sequence[NewsItem], fetched_at: datetime
    ) -> int: ...

    async def select_unscored(self, limit: int) -> list[ScorableItem]: ...

    async def append_verdict(
        self,
        verdict: MaterialityVerdict,
        prompt_version: int,
        tier: str,
        model: str,
        decided_at: datetime,
    ) -> None: ...

    async def mark_scored(
        self,
        verdict: MaterialityVerdict,
        prompt_version: int,
        model: str,
        scored_at: datetime,
    ) -> None: ...


class Publisher(Protocol):
    async def publish(
        self,
        stream: str,
        produced_by: str,
        schema_version: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class NewsRunConfig:
    """Symbols and knobs for the News Analyzer passes."""

    symbols: tuple[str, ...]
    feed: str = NEWS_SOURCE
    lookback_days: int = 3
    page_limit: int = 50
    score_max_items: int = 300
    escalation_threshold: int = 2
    publish_threshold: int = 1
    local_tier: str = TIER_LOCAL_CLASSIFICATION
    cloud_tier: str = TIER_CLOUD_DEFAULT
    active_interval_seconds: float = DEFAULT_ACTIVE_INTERVAL_SECONDS
    idle_interval_seconds: float = DEFAULT_IDLE_INTERVAL_SECONDS


@dataclass(frozen=True, slots=True)
class FetchCounts:
    fetched: int
    inserted: int


@dataclass(frozen=True, slots=True)
class ScoreCounts:
    scored: int
    relevant: int
    published: int
    escalated: int


def build_signal_payload(item: ScorableItem, verdict: MaterialityVerdict) -> dict[str, Any]:
    """ADR-0006 payload for one ``intelligence.signal`` news event."""

    return {
        "signal_type": SIGNAL_TYPE,
        "symbols": list(verdict.symbols),
        "category": verdict.category,
        "materiality": verdict.materiality,
        "headline": item.headline,
        "summary": verdict.summary,
        "source": NEWS_SOURCE,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "item_ref": item.item_id,
    }


async def fetch_pass(
    source: NewsSource,
    http_client: AsyncHttpClient,
    store: NewsStore,
    config: NewsRunConfig,
    now: datetime,
) -> FetchCounts:
    """Fetch news since the cursor and upsert idempotently."""

    cursor_ts = await store.cursor_ts(config.feed)
    if cursor_ts is None:
        start = now - timedelta(days=config.lookback_days)
    else:
        # Re-pull from the newest item seen; the upsert makes overlap free.
        start = cursor_ts
    items = await source.get_news(
        http_client,
        symbols=list(config.symbols),
        start=start.isoformat(),
        limit=config.page_limit,
    )
    inserted = await store.upsert_items(config.feed, items, now)
    return FetchCounts(fetched=len(items), inserted=inserted)


async def score_pass(
    store: NewsStore,
    llm: CompletionClient,
    publisher: Publisher,
    config: NewsRunConfig,
) -> ScoreCounts:
    """Score unscored items, escalate material ones, publish signals.

    Each item is marked scored individually after its verdict(s) land in the
    history table, so a crash mid-batch resumes on the still-unscored items.
    An LLM failure propagates and stops the pass (systemic — likely Ollama
    down); the remaining items stay unscored for the next pass.
    """

    items = await store.select_unscored(config.score_max_items)
    scored = 0
    relevant = 0
    published = 0
    escalated = 0
    for item in items:
        prompt = build_prompt(item.headline, item.summary, item.symbols)
        local = await llm.complete(
            tier=config.local_tier,
            prompt=prompt,
            system=NEWS_SYSTEM_PROMPT,
            json_mode=True,
            think=False,
        )
        verdict = parse_news_response(item.item_id, local.content, item.symbols)
        # History row first (KI-007): a crash before the mark re-scores the
        # item; the extra history row is harmless. The reverse order loses it.
        await store.append_verdict(
            verdict, NEWS_PROMPT_VERSION, config.local_tier, local.model, datetime.now(UTC)
        )
        final = verdict
        final_model = local.model
        if verdict.materiality >= config.escalation_threshold:
            escalated += 1
            cloud = await llm.complete(
                tier=config.cloud_tier,
                prompt=prompt,
                system=NEWS_SYSTEM_PROMPT,
                json_mode=True,
                think=False,
            )
            cloud_verdict = parse_news_response(item.item_id, cloud.content, item.symbols)
            await store.append_verdict(
                cloud_verdict,
                NEWS_PROMPT_VERSION,
                config.cloud_tier,
                cloud.model,
                datetime.now(UTC),
            )
            chosen = higher_verdict(verdict, cloud_verdict)
            if chosen is cloud_verdict:
                final = cloud_verdict
                final_model = cloud.model
        await store.mark_scored(final, NEWS_PROMPT_VERSION, final_model, datetime.now(UTC))
        scored += 1
        if final.relevant:
            relevant += 1
        if final.materiality >= config.publish_threshold:
            await publisher.publish(
                stream=STREAM_INTELLIGENCE_SIGNAL,
                produced_by=PRODUCED_BY,
                schema_version=SCHEMA_VERSION,
                payload=build_signal_payload(item, final),
            )
            published += 1
            log.info(
                "news_analyzer.published",
                item_ref=item.item_id,
                category=final.category,
                materiality=final.materiality,
                symbols=list(final.symbols),
            )
    return ScoreCounts(scored=scored, relevant=relevant, published=published, escalated=escalated)


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass


async def _interruptible_sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run_loop(
    source: NewsSource,
    http_client: AsyncHttpClient,
    store: NewsStore,
    redis: PhaseRedis,
    publisher: Publisher,
    llm: CompletionClient,
    config: NewsRunConfig,
    stop: asyncio.Event,
) -> None:
    """Run fetch → score passes until ``stop``, on the phase-driven cadence.

    Fetch and score fail independently: an Ollama outage stops scoring for the
    pass without touching ingest, and a fetch error leaves the score pass to
    work the backlog. The heartbeat always fires so the Health Monitor sees
    freshness.
    """

    while not stop.is_set():
        phase = await read_latest_phase(redis)
        interval = interval_for_phase(
            phase, config.active_interval_seconds, config.idle_interval_seconds
        )
        fetch_counts = FetchCounts(0, 0)
        score_counts = ScoreCounts(0, 0, 0, 0)
        try:
            fetch_counts = await fetch_pass(source, http_client, store, config, datetime.now(UTC))
            log.info(
                "news_analyzer.fetch_complete",
                fetched=fetch_counts.fetched,
                inserted=fetch_counts.inserted,
            )
        except Exception:
            log.exception("news_analyzer.fetch_failed")
        try:
            score_counts = await score_pass(store, llm, publisher, config)
            log.info(
                "news_analyzer.score_complete",
                scored=score_counts.scored,
                relevant=score_counts.relevant,
                published=score_counts.published,
                escalated=score_counts.escalated,
            )
        except Exception:
            log.exception("news_analyzer.score_failed")
        try:
            await publisher.publish(
                stream=STREAM_INGESTION_HEARTBEAT,
                produced_by=PRODUCED_BY,
                schema_version=SCHEMA_VERSION,
                payload={
                    "agent": PRODUCED_BY,
                    "phase": phase,
                    "interval_seconds": interval,
                    "fetched": fetch_counts.fetched,
                    "inserted": fetch_counts.inserted,
                    "scored": score_counts.scored,
                    "relevant": score_counts.relevant,
                    "published": score_counts.published,
                    "escalated": score_counts.escalated,
                },
            )
        except Exception:
            log.exception("news_analyzer.heartbeat_failed")
        await _interruptible_sleep(stop, interval)


async def run(
    redis_url: str,
    postgres_dsn: str,
    market_data_settings: AlpacaMarketDataSettings,
    config: NewsRunConfig,
    service_name: str = "news-analyzer",
    log_level: str = "INFO",
    llm_env: dict[str, str] | None = None,
    http_timeout: float = 60.0,
) -> None:
    """Run the News Analyzer service until SIGINT/SIGTERM."""

    configure_logging(service_name, log_level)
    log.info(
        "news_analyzer.starting",
        redis_url=redis_url,
        postgres_dsn="***",
        alpaca=market_data_settings.redacted(),
        symbols=list(config.symbols),
        feed=config.feed,
        escalation_threshold=config.escalation_threshold,
        publish_threshold=config.publish_threshold,
        active_interval_seconds=config.active_interval_seconds,
        idle_interval_seconds=config.idle_interval_seconds,
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    redis: Redis = Redis.from_url(redis_url, decode_responses=True, socket_timeout=30)
    pool = await create_asyncpg_pool(postgres_dsn)
    store = PostgresNewsStore(pool)
    await store.ensure_schema()
    source = AlpacaNewsClient(market_data_settings)
    async with httpx.AsyncClient(timeout=http_timeout) as http:
        registry = TierRegistry(llm_env if llm_env is not None else dict(os.environ))
        llm = TierLLMClient(registry, cast(Any, http))
        try:
            await run_loop(
                source,
                cast(AsyncHttpClient, http),
                store,
                cast(PhaseRedis, redis),
                EventPublisher(cast(RedisPublisher, redis)),
                llm,
                config,
                stop,
            )
        finally:
            await redis.aclose()
            await pool.close()
            log.info("news_analyzer.stopped")


__all__ = [
    "PRODUCED_BY",
    "SCHEMA_VERSION",
    "STREAM_INGESTION_HEARTBEAT",
    "STREAM_INTELLIGENCE_SIGNAL",
    "STREAM_MARKET_PHASE",
    "FetchCounts",
    "NewsRunConfig",
    "ScoreCounts",
    "build_signal_payload",
    "fetch_pass",
    "interval_for_phase",
    "read_latest_phase",
    "run",
    "run_loop",
    "score_pass",
]
