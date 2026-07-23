"""Backfill orchestration for the Filing Processor (spec §Trigger, on-demand).

The service card (PR #68) deliberately deferred the "On-demand: Mike-initiated
backfill over an accession-number or date range" trigger
(``docs/agents/intelligence/filing-processor.md`` §Trigger) to this card. This
module is the testable core: resolve an explicit accession list or filing-date
range to Tier 3 candidates, record them, decide which already have a verdict
and should be skipped, then run the exact same fetch/score/publish path as the
service loop (:mod:`shrap.intelligence.filing_processor.service`) scoped to
just those filings.

Two things this module is careful about:

- **Never perturbs the live poll cursor.** Recorded pendings go through
  ``record_and_advance`` under :data:`BACKFILL_FEED`, a cursor row distinct
  from the service's own ``DEFAULT_FEED`` — a backfill run can never rewind or
  fast-forward the service's own poll position (spec Failure behavior: replay
  safety).
- **Skip is a CLI-level decision, not a store-level one.** Already-scored
  filings are left out of the accession set handed to :func:`~shrap.
  intelligence.filing_processor.service.score_pass` unless ``rescore`` is set;
  the store's ``select_scorable_by_accession`` never re-derives that decision,
  so a rescore always appends new verdict-history rows and never overwrites
  the old ones (KI-007).

The real-infra wrapper (:func:`run`) mirrors :func:`shrap.intelligence.
filing_processor.service.run`'s wiring shape exactly, so the two entrypoints
stay easy to compare. Env parsing and argparse live one layer up in
:mod:`shrap.agents.intelligence.filing_processor.backfill`, the console-script
entrypoint, which reuses the same ``Settings`` as the live service.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import structlog
from redis.asyncio import Redis

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.events import EventPublisher, RedisPublisher
from shrap.intelligence.filing_processor.client import (
    FILING_SOURCE,
    EdgarFilingClient,
    HTTPClient,
    accession_from_item_id,
)
from shrap.intelligence.filing_processor.scorer import CompletionClient
from shrap.intelligence.filing_processor.service import (
    FetchCounts,
    FilingRunConfig,
    FilingSource,
    Publisher,
    ScoreCounts,
    fetch_pass,
    match_candidate,
    score_pass,
)
from shrap.intelligence.filing_processor.store import (
    CandidateRow,
    PendingFiling,
    PostgresFilingStore,
)
from shrap.llm import TierLLMClient, TierRegistry

log = structlog.get_logger(__name__)

# A cursor row distinct from the service's own DEFAULT_FEED (``sec-edgar-8k``)
# so a backfill run never advances or rewinds the live poll position.
BACKFILL_FEED = f"{FILING_SOURCE}-8k-backfill"

# Upper bound when --until is omitted: the window is open toward the future.
_FAR_FUTURE = datetime(9999, 12, 31, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class BackfillSummary:
    """Plain-text-summarizable outcome of one backfill run."""

    discovered: int
    fetched: int
    scored: int
    published: int
    skipped: int

    def as_dict(self) -> dict[str, int]:
        return {
            "discovered": self.discovered,
            "fetched": self.fetched,
            "scored": self.scored,
            "published": self.published,
            "skipped": self.skipped,
        }

    def render(self) -> str:
        """One-line plain-text summary the CLI prints on exit."""

        return " ".join(f"{key}={value}" for key, value in self.as_dict().items())


def parse_date_range(since: str, until: str | None) -> tuple[datetime, datetime]:
    """Parse ``--since``/``--until`` strings into a ``[since, until)`` UTC window.

    ``since`` is inclusive from midnight UTC that day. ``until``, if given, is
    inclusive of its whole day (the exclusive upper bound is the following
    midnight); omitted, the window has no upper bound. Raises ``ValueError`` on
    a malformed date, same as ``datetime.strptime``.
    """

    since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=UTC)
    if until is None:
        return since_dt, _FAR_FUTURE
    until_dt = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=UTC) + timedelta(days=1)
    return since_dt, until_dt


async def _resolve_candidates(
    store: PostgresFilingStore,
    *,
    accessions: Sequence[str] | None,
    since: datetime | None,
    until: datetime | None,
) -> list[CandidateRow]:
    if accessions is not None:
        return await store.select_candidates_by_accessions(accessions)
    if since is None:
        raise ValueError("backfill requires either --accession or --since")
    return await store.select_candidates_by_date_range(
        since, until if until is not None else _FAR_FUTURE
    )


def _warn_missing_accessions(requested: Sequence[str], candidates: Sequence[CandidateRow]) -> None:
    found = {accession_from_item_id(c.item_id) for c in candidates}
    for accession in requested:
        if accession not in found:
            log.warning("filing_backfill.accession_not_found", accession=accession)


async def _record(
    store: PostgresFilingStore, pendings: Sequence[PendingFiling], now: datetime
) -> None:
    """Insert matched pendings under the dedicated backfill feed (idempotent)."""

    if pendings:
        await store.record_and_advance(BACKFILL_FEED, list(pendings), now, len(pendings), now)


async def _select_targets(
    store: PostgresFilingStore, accessions: Sequence[str], *, rescore: bool
) -> tuple[list[str], int]:
    """Split discovered accessions into (targets to score, already-scored count).

    Already-scored filings are skipped by default; ``--rescore`` forces them
    back through scoring, where new verdict-history rows append and never
    overwrite (KI-007).
    """

    if not accessions:
        return [], 0
    if rescore:
        return list(accessions), 0
    states = await store.select_filing_states(accessions)
    skipped = [
        a
        for a in accessions
        if (state := states.get(a)) is not None and state.scored_at is not None
    ]
    skipped_set = frozenset(skipped)
    target = [a for a in accessions if a not in skipped_set]
    return target, len(skipped)


async def backfill_pass(
    store: PostgresFilingStore,
    source: FilingSource,
    http: HTTPClient,
    llm: CompletionClient,
    publisher: Publisher,
    config: FilingRunConfig,
    *,
    accessions: Sequence[str] | None,
    since: datetime | None,
    until: datetime | None,
    rescore: bool,
    now: datetime,
) -> BackfillSummary:
    """Resolve -> record -> fetch -> score -> publish for exactly the requested filings.

    Mirrors the service's poll/fetch/score passes but scoped to an explicit
    accession list or filing-date range instead of the live cursor, respecting
    the same EDGAR throttle (``config.fetch_throttle_seconds``, applied inside
    :func:`~shrap.intelligence.filing_processor.service.fetch_pass`).
    Already-scored filings are skipped unless ``rescore`` is set.
    """

    candidates = await _resolve_candidates(store, accessions=accessions, since=since, until=until)
    if accessions is not None:
        _warn_missing_accessions(accessions, candidates)
    pendings = [p for c in candidates if (p := match_candidate(c, config.roster)) is not None]
    await _record(store, pendings, now)

    discovered_accessions = [p.accession for p in pendings]
    target, skipped = await _select_targets(store, discovered_accessions, rescore=rescore)
    target_set = frozenset(target)

    fetch_counts = FetchCounts(fetched=0, failed=0)
    score_counts = ScoreCounts(filings_scored=0, items_scored=0, published=0, escalated=0)
    if target_set:
        fetch_counts = await fetch_pass(source, http, store, config, accessions=target_set)
        score_counts = await score_pass(store, llm, publisher, config, accessions=target_set)

    return BackfillSummary(
        discovered=len(pendings),
        fetched=fetch_counts.fetched,
        scored=score_counts.filings_scored,
        published=score_counts.published,
        skipped=skipped,
    )


async def run(
    redis_url: str,
    postgres_dsn: str,
    sec_user_agent: str,
    config: FilingRunConfig,
    *,
    accessions: Sequence[str] | None,
    since: datetime | None,
    until: datetime | None,
    rescore: bool,
    service_name: str = "filing-processor-backfill",
    log_level: str = "INFO",
    llm_env: dict[str, str] | None = None,
    http_timeout: float = 30.0,
) -> BackfillSummary:
    """Run one Mike-initiated backfill pass and return its summary.

    Same container, same env, same fetch -> score -> publish path as the
    service loop (:func:`shrap.intelligence.filing_processor.service.run`).
    No new Dockerfile — this runs inside the existing filing-processor
    container via ``docker exec``, the same way ``shrap-tech-watcher-promote``
    runs inside the tech-watcher container.
    """

    configure_logging(service_name, log_level)
    log.info(
        "filing_backfill.starting",
        accessions=list(accessions) if accessions is not None else None,
        since=since.isoformat() if since else None,
        until=until.isoformat() if until else None,
        rescore=rescore,
        roster_size=len(config.roster),
    )
    redis: Redis = Redis.from_url(redis_url, decode_responses=True, socket_timeout=30)
    pool = await create_asyncpg_pool(postgres_dsn)
    store = PostgresFilingStore(pool)
    await store.ensure_schema()
    source = EdgarFilingClient(sec_user_agent)
    try:
        async with httpx.AsyncClient(timeout=http_timeout, follow_redirects=True) as http:
            registry = TierRegistry(llm_env if llm_env is not None else dict(os.environ))
            llm = TierLLMClient(registry, cast(Any, http))
            summary = await backfill_pass(
                store,
                cast(FilingSource, source),
                cast(HTTPClient, http),
                llm,
                EventPublisher(cast(RedisPublisher, redis)),
                config,
                accessions=accessions,
                since=since,
                until=until,
                rescore=rescore,
                now=datetime.now(UTC),
            )
    finally:
        await redis.aclose()
        await pool.close()
    log.info("filing_backfill.complete", **summary.as_dict())
    return summary


__all__ = [
    "BACKFILL_FEED",
    "BackfillSummary",
    "backfill_pass",
    "parse_date_range",
    "run",
]
