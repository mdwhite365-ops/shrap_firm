"""Filing Processor service loop (spec §Processing / §Trigger).

Each pass runs three stages on the market-phase cadence:

1. **poll** — read new ``research.raw_source_items`` 8-K rows since this
   agent's own cursor, resolve each to the Tier 3 roster by CIK, record matched
   filings as pending, and advance the cursor past every row seen.
2. **fetch** — dereference pending filings' full text from EDGAR under the SEC
   ``User-Agent`` convention, extract declared item codes, store both. A
   429/403 backs off for the pass; other failures leave the filing pending.
3. **score** — split each fetched filing by item code, bulk-score each item on
   the local tier, escalate material items to the cloud tier, publish
   materiality>=1 signals on ``intelligence.signal``.

Every verdict appends to the history table BEFORE the filing is marked scored
(KI-007 crash-ordering). Full 8-K text can be large, so it lives only in
Postgres — signals carry summaries and an ``<accession>#<item_code>`` reference,
never the body (ADR-0006 16KB inline limit).
"""

from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

import httpx
import structlog
from redis.asyncio import Redis

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.events import EventPublisher, RedisPublisher
from shrap.intelligence.filing_processor.client import (
    FILING_SOURCE,
    EdgarFilingClient,
    FilingFetchError,
    HTTPClient,
    Tier3Roster,
    accession_from_item_id,
    derive_company,
    derive_headline,
    extract_item_codes,
    parse_cik,
    split_item_sections,
)
from shrap.intelligence.filing_processor.scorer import (
    FILING_PROMPT_VERSION,
    FILING_SYSTEM_PROMPT,
    CompletionClient,
    FilingVerdict,
    build_prompt,
    higher_verdict,
    parse_filing_response,
)
from shrap.intelligence.filing_processor.store import (
    CandidateRow,
    PendingFetch,
    PendingFiling,
    PostgresFilingStore,
    ScorableFiling,
)
from shrap.intelligence.market_phase import (
    DEFAULT_ACTIVE_INTERVAL_SECONDS,
    DEFAULT_IDLE_INTERVAL_SECONDS,
    PhaseRedis,
    interval_for_phase,
    read_latest_phase,
)
from shrap.llm import TierLLMClient, TierRegistry
from shrap.llm.registry import TIER_CLOUD_DEFAULT, TIER_LOCAL_CLASSIFICATION

log = structlog.get_logger(__name__)

PRODUCED_BY = "intelligence/filing-processor"
SCHEMA_VERSION = "1.0.0"
SIGNAL_TYPE = "filing"

DEFAULT_FEED = f"{FILING_SOURCE}-8k"

STREAM_INTELLIGENCE_SIGNAL = "intelligence.signal"
STREAM_INGESTION_HEARTBEAT = "ingestion.heartbeat"

# Poll floor when the cursor has never advanced — before any real EDGAR item.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class FilingSource(Protocol):
    async def fetch_filing_text(
        self, http: HTTPClient, cik: str, accession: str, timeout: float = 30.0
    ) -> str: ...


class FilingStore(Protocol):
    async def cursor_ts(self, feed: str) -> datetime | None: ...

    async def select_candidates(self, since: datetime, limit: int) -> list[CandidateRow]: ...

    async def record_and_advance(
        self,
        feed: str,
        pendings: list[PendingFiling],
        last_fetched_at: datetime | None,
        seen: int,
        now: datetime,
    ) -> int: ...

    async def select_pending_fetch(self, limit: int) -> list[PendingFetch]: ...

    async def mark_fetched(
        self, accession: str, full_text: str, item_codes: list[str], fetched_at: datetime
    ) -> None: ...

    async def select_unscored(self, limit: int) -> list[ScorableFiling]: ...

    async def append_verdict(
        self,
        verdict: FilingVerdict,
        prompt_version: int,
        tier: str,
        model: str,
        decided_at: datetime,
    ) -> None: ...

    async def mark_scored(
        self, accession: str, verdicts: list[dict[str, Any]], scored_at: datetime
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
class FilingRunConfig:
    """Roster and knobs for the Filing Processor passes."""

    roster: Tier3Roster
    feed: str = DEFAULT_FEED
    poll_max_items: int = 200
    fetch_max_items: int = 50
    score_max_items: int = 100
    escalation_threshold: int = 2
    publish_threshold: int = 1
    local_tier: str = TIER_LOCAL_CLASSIFICATION
    cloud_tier: str = TIER_CLOUD_DEFAULT
    fetch_throttle_seconds: float = 0.2
    http_timeout: float = 30.0
    active_interval_seconds: float = DEFAULT_ACTIVE_INTERVAL_SECONDS
    idle_interval_seconds: float = DEFAULT_IDLE_INTERVAL_SECONDS


@dataclass(frozen=True, slots=True)
class PollCounts:
    seen: int
    matched: int
    recorded: int


@dataclass(frozen=True, slots=True)
class FetchCounts:
    fetched: int
    failed: int


@dataclass(frozen=True, slots=True)
class ScoreCounts:
    filings_scored: int
    items_scored: int
    published: int
    escalated: int


def build_signal_payload(filing: ScorableFiling, verdict: FilingVerdict) -> dict[str, Any]:
    """ADR-0006 payload for one ``intelligence.signal`` filing event."""

    return {
        "signal_type": SIGNAL_TYPE,
        "symbols": list(verdict.symbols),
        "category": verdict.category,
        "materiality": verdict.materiality,
        "item_code": verdict.item_code,
        "headline": derive_headline(verdict.item_code, filing.company),
        "summary": verdict.summary,
        "source": FILING_SOURCE,
        "published_at": filing.filing_date.isoformat() if filing.filing_date else None,
        "item_ref": f"{filing.accession}#{verdict.item_code}",
    }


def _verdict_row(verdict: FilingVerdict, model: str) -> dict[str, Any]:
    """The per-item verdict shape stored on ``intelligence.filings.verdicts``."""

    return {
        "item_code": verdict.item_code,
        "relevant": verdict.relevant,
        "symbols": list(verdict.symbols),
        "category": verdict.category,
        "materiality": verdict.materiality,
        "summary": verdict.summary,
        "model": model,
        "prompt_version": FILING_PROMPT_VERSION,
    }


async def poll_pass(store: FilingStore, config: FilingRunConfig, now: datetime) -> PollCounts:
    """Discover Tier 3-matched 8-Ks and record them as pending filings.

    Advances the poll cursor past every candidate seen this pass, matched or
    not, so re-polling never re-scans the Tech Watcher's market-wide backlog.
    """

    cursor = await store.cursor_ts(config.feed)
    since = cursor if cursor is not None else _EPOCH
    candidates = await store.select_candidates(since, config.poll_max_items)
    pendings: list[PendingFiling] = []
    max_ts = cursor
    for candidate in candidates:
        if candidate.fetched_at is not None and (max_ts is None or candidate.fetched_at > max_ts):
            max_ts = candidate.fetched_at
        accession = accession_from_item_id(candidate.item_id)
        if accession is None:
            continue
        symbol = config.roster.ticker_for(parse_cik(candidate.url))
        if symbol is None:
            continue  # not a Tier 3 name — dropped here, never upstream at ingest
        pendings.append(
            PendingFiling(
                accession=accession,
                cik=parse_cik(candidate.url) or "",
                symbol=symbol,
                title=candidate.title,
                company=derive_company(candidate.title),
                filing_url=candidate.url,
                filing_date=candidate.filing_date,
                payload={"item_id": candidate.item_id, "url": candidate.url},
            )
        )
    recorded = await store.record_and_advance(config.feed, pendings, max_ts, len(candidates), now)
    return PollCounts(seen=len(candidates), matched=len(pendings), recorded=recorded)


async def fetch_pass(
    source: FilingSource,
    http: HTTPClient,
    store: FilingStore,
    config: FilingRunConfig,
) -> FetchCounts:
    """Fetch full text for pending filings and extract declared item codes.

    A 429/403 backs off — it stops the pass and retries next tick — rather than
    hammering EDGAR in-pass. Any other fetch failure leaves that filing pending
    for the next pass; nothing is lost.
    """

    pending = await store.select_pending_fetch(config.fetch_max_items)
    fetched = 0
    failed = 0
    for index, filing in enumerate(pending):
        try:
            text = await source.fetch_filing_text(
                http, filing.cik, filing.accession, timeout=config.http_timeout
            )
        except FilingFetchError as exc:
            failed += 1
            if exc.status_code in (429, 403):
                log.warning(
                    "filing_processor.rate_limited_backoff",
                    accession=filing.accession,
                    status=exc.status_code,
                )
                break
            log.warning(
                "filing_processor.fetch_failed",
                accession=filing.accession,
                status=exc.status_code,
            )
            continue
        except Exception:
            failed += 1
            log.exception("filing_processor.fetch_error", accession=filing.accession)
            continue
        item_codes = extract_item_codes(text)
        await store.mark_fetched(filing.accession, text, item_codes, datetime.now(UTC))
        fetched += 1
        if index + 1 < len(pending):
            await asyncio.sleep(config.fetch_throttle_seconds)
    return FetchCounts(fetched=fetched, failed=failed)


async def score_pass(
    store: FilingStore,
    llm: CompletionClient,
    publisher: Publisher,
    config: FilingRunConfig,
) -> ScoreCounts:
    """Score each fetched filing's item sections, escalate, and publish.

    Each item's verdict lands in the history table before the filing is marked
    scored (KI-007), so a crash mid-filing re-scores it rather than losing the
    verdict. An LLM failure propagates and stops the pass (systemic — likely
    Ollama down); the still-unscored filings resume next pass.
    """

    filings = await store.select_unscored(config.score_max_items)
    filings_scored = 0
    items_scored = 0
    published = 0
    escalated = 0
    for filing in filings:
        sections = split_item_sections(filing.full_text)
        fallback_symbols = (filing.symbol,)
        verdict_rows: list[dict[str, Any]] = []
        for item_code in filing.item_codes:
            prompt = build_prompt(
                filing.company, item_code, sections.get(item_code, ""), fallback_symbols
            )
            local = await llm.complete(
                tier=config.local_tier,
                prompt=prompt,
                system=FILING_SYSTEM_PROMPT,
                json_mode=True,
                think=False,
            )
            verdict = parse_filing_response(
                filing.accession, item_code, local.content, fallback_symbols
            )
            # History row first (KI-007): a crash before the mark re-scores the
            # item; the extra history row is harmless. The reverse order loses it.
            await store.append_verdict(
                verdict, FILING_PROMPT_VERSION, config.local_tier, local.model, datetime.now(UTC)
            )
            final = verdict
            final_model = local.model
            if verdict.materiality >= config.escalation_threshold:
                escalated += 1
                cloud = await llm.complete(
                    tier=config.cloud_tier,
                    prompt=prompt,
                    system=FILING_SYSTEM_PROMPT,
                    json_mode=True,
                    think=False,
                )
                cloud_verdict = parse_filing_response(
                    filing.accession, item_code, cloud.content, fallback_symbols
                )
                await store.append_verdict(
                    cloud_verdict,
                    FILING_PROMPT_VERSION,
                    config.cloud_tier,
                    cloud.model,
                    datetime.now(UTC),
                )
                if higher_verdict(verdict, cloud_verdict) is cloud_verdict:
                    final = cloud_verdict
                    final_model = cloud.model
            items_scored += 1
            verdict_rows.append(_verdict_row(final, final_model))
            if final.materiality >= config.publish_threshold:
                await publisher.publish(
                    stream=STREAM_INTELLIGENCE_SIGNAL,
                    produced_by=PRODUCED_BY,
                    schema_version=SCHEMA_VERSION,
                    payload=build_signal_payload(filing, final),
                )
                published += 1
                log.info(
                    "filing_processor.published",
                    item_ref=f"{filing.accession}#{final.item_code}",
                    category=final.category,
                    materiality=final.materiality,
                    symbols=list(final.symbols),
                )
        await store.mark_scored(filing.accession, verdict_rows, datetime.now(UTC))
        filings_scored += 1
    return ScoreCounts(
        filings_scored=filings_scored,
        items_scored=items_scored,
        published=published,
        escalated=escalated,
    )


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
    source: FilingSource,
    http: HTTPClient,
    store: FilingStore,
    redis: PhaseRedis,
    publisher: Publisher,
    llm: CompletionClient,
    config: FilingRunConfig,
    stop: asyncio.Event,
) -> None:
    """Run poll → fetch → score passes until ``stop``, on the phase cadence.

    The three stages fail independently: a Tech Watcher poll error, an EDGAR
    outage, and an Ollama outage each degrade one stage and leave the others
    working. The heartbeat always fires so the Health Monitor sees freshness.
    """

    while not stop.is_set():
        phase = await read_latest_phase(redis)
        interval = interval_for_phase(
            phase, config.active_interval_seconds, config.idle_interval_seconds
        )
        poll_counts = PollCounts(0, 0, 0)
        fetch_counts = FetchCounts(0, 0)
        score_counts = ScoreCounts(0, 0, 0, 0)
        try:
            poll_counts = await poll_pass(store, config, datetime.now(UTC))
            log.info(
                "filing_processor.poll_complete",
                seen=poll_counts.seen,
                matched=poll_counts.matched,
                recorded=poll_counts.recorded,
            )
        except Exception:
            log.exception("filing_processor.poll_failed")
        try:
            fetch_counts = await fetch_pass(source, http, store, config)
            log.info(
                "filing_processor.fetch_complete",
                fetched=fetch_counts.fetched,
                failed=fetch_counts.failed,
            )
        except Exception:
            log.exception("filing_processor.fetch_failed")
        try:
            score_counts = await score_pass(store, llm, publisher, config)
            log.info(
                "filing_processor.score_complete",
                filings_scored=score_counts.filings_scored,
                items_scored=score_counts.items_scored,
                published=score_counts.published,
                escalated=score_counts.escalated,
            )
        except Exception:
            log.exception("filing_processor.score_failed")
        try:
            await publisher.publish(
                stream=STREAM_INGESTION_HEARTBEAT,
                produced_by=PRODUCED_BY,
                schema_version=SCHEMA_VERSION,
                payload={
                    "agent": PRODUCED_BY,
                    "phase": phase,
                    "interval_seconds": interval,
                    "seen": poll_counts.seen,
                    "matched": poll_counts.matched,
                    "recorded": poll_counts.recorded,
                    "fetched": fetch_counts.fetched,
                    "fetch_failed": fetch_counts.failed,
                    "filings_scored": score_counts.filings_scored,
                    "items_scored": score_counts.items_scored,
                    "published": score_counts.published,
                    "escalated": score_counts.escalated,
                },
            )
        except Exception:
            log.exception("filing_processor.heartbeat_failed")
        await _interruptible_sleep(stop, interval)


async def run(
    redis_url: str,
    postgres_dsn: str,
    sec_user_agent: str,
    config: FilingRunConfig,
    service_name: str = "filing-processor",
    log_level: str = "INFO",
    llm_env: dict[str, str] | None = None,
    http_timeout: float = 30.0,
) -> None:
    """Run the Filing Processor service until SIGINT/SIGTERM."""

    configure_logging(service_name, log_level)
    log.info(
        "filing_processor.starting",
        redis_url=redis_url,
        postgres_dsn="***",
        sec_user_agent=sec_user_agent,
        feed=config.feed,
        roster_size=len(config.roster),
        escalation_threshold=config.escalation_threshold,
        publish_threshold=config.publish_threshold,
        active_interval_seconds=config.active_interval_seconds,
        idle_interval_seconds=config.idle_interval_seconds,
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    redis: Redis = Redis.from_url(redis_url, decode_responses=True, socket_timeout=30)
    pool = await create_asyncpg_pool(postgres_dsn)
    store = PostgresFilingStore(pool)
    await store.ensure_schema()
    source = EdgarFilingClient(sec_user_agent)
    async with httpx.AsyncClient(timeout=http_timeout, follow_redirects=True) as http:
        registry = TierRegistry(llm_env if llm_env is not None else dict(os.environ))
        llm = TierLLMClient(registry, cast(Any, http))
        try:
            await run_loop(
                cast(FilingSource, source),
                cast(HTTPClient, http),
                cast(FilingStore, store),
                cast(PhaseRedis, redis),
                EventPublisher(cast(RedisPublisher, redis)),
                llm,
                config,
                stop,
            )
        finally:
            await redis.aclose()
            await pool.close()
            log.info("filing_processor.stopped")


__all__ = [
    "DEFAULT_FEED",
    "PRODUCED_BY",
    "SCHEMA_VERSION",
    "STREAM_INGESTION_HEARTBEAT",
    "STREAM_INTELLIGENCE_SIGNAL",
    "FetchCounts",
    "FilingRunConfig",
    "PollCounts",
    "ScoreCounts",
    "build_signal_payload",
    "fetch_pass",
    "poll_pass",
    "run",
    "run_loop",
    "score_pass",
]
