"""Tier-transition operations and the five transition events (ADR-0012).

Every Tier 3 mutation in this module runs only from an explicit decision —
``seed`` records a Tier 2 watch entry, ``stage`` assembles a proposal for Mike,
``approve``/``reject`` execute his decision, ``extend``/``expire`` maintain the
watch soft cap, and ``load_launch_list`` seeds the locked launch set directly
into Tier 3 through the event path. There is no auto-promotion anywhere: a
Curator bug can only shrink attention (a wrongly expired watch entry), never
make a name tradeable without Mike.

The five events, published through the canonical ADR-0006 publisher:

- ``research.universe-watch-added``     Tier 1 → 2 (seed)
- ``research.universe-watch-expired``   Tier 2 → 1 (expire / sweep)
- ``research.universe-promoted``        Tier 2/1 → 3 (approve, launch load)
- ``research.universe-evicted``         Tier 3 → 1 (approve of eviction / paired)
- ``research.universe-proposal-rejected`` deny path (malformed seed, Mike reject)

Payload minimum (Curator spec §Transition event contract):
``{ticker, source_tier, destination_tier, mechanism, evidence_ref}``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from ulid import ULID

from shrap.events import EventPublisher
from shrap.research.strategy_registry import (
    STATUS_LIVE_PAPER,
    STATUS_PAPER,
    STATUS_SMALL_SIZE_PAPER,
)
from shrap.research.universe_curator.launch_list import (
    LAUNCH_CIKS,
    LAUNCH_LIST,
    LAUNCH_PROFILE_PATHS,
    TIER3_CAP,
)
from shrap.research.universe_curator.store import (
    DISPOSITION_REJECTED,
    KIND_EVICTION,
    KIND_PROMOTION,
    TIER_ACTIVE,
    TIER_DISCOVERY,
    TIER_WATCH,
    PostgresUniverseStore,
)

log = structlog.get_logger(__name__)

PRODUCED_BY = "universe-curator"
SCHEMA_VERSION = "1.0.0"

STREAM_WATCH_ADDED = "research.universe-watch-added"
STREAM_WATCH_EXPIRED = "research.universe-watch-expired"
STREAM_PROMOTED = "research.universe-promoted"
STREAM_EVICTED = "research.universe-evicted"
STREAM_PROPOSAL_REJECTED = "research.universe-proposal-rejected"

MECHANISM_FUNNEL = "funnel-candidate"
MECHANISM_FORCED_PROXY = "forced-proxy"
MECHANISM_STRUCTURAL = "structural-finding"
MECHANISM_MIKE_SEED = "mike-seed"
MECHANISMS = frozenset(
    {MECHANISM_FUNNEL, MECHANISM_FORCED_PROXY, MECHANISM_STRUCTURAL, MECHANISM_MIKE_SEED}
)

# The DQ-004 lock-in decision, referenced as the launch load's evidence.
LAUNCH_EVIDENCE_REF = "docs/status/decision-queue.md#dq-004 (locked 2026-07-23)"

# Strategy stages that count as "live or paper-stage" for consequence
# annotation (Curator spec §Tier 3 promotion, consequence annotation).
CONSEQUENCE_STATUSES: tuple[str, ...] = (STATUS_PAPER, STATUS_SMALL_SIZE_PAPER, STATUS_LIVE_PAPER)


class RedisStreamClient(Protocol):
    async def xadd(self, stream: str, fields: dict[str, str]) -> str: ...


class CuratorError(Exception):
    """A requested tier operation is invalid — refused, fail-closed."""


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """Outcome of a tier operation, for the CLI to print and tests to assert."""

    ticker: str
    stream: str | None
    detail: str
    staging_id: str | None = None
    consequences: list[dict[str, Any]] = field(default_factory=list)


def transition_payload(
    *,
    ticker: str,
    source_tier: str,
    destination_tier: str,
    mechanism: str,
    evidence_ref: str,
    **extra: Any,
) -> dict[str, Any]:
    """Build a payload with the spec's five required fields plus optional extras."""

    payload: dict[str, Any] = {
        "ticker": ticker,
        "source_tier": source_tier,
        "destination_tier": destination_tier,
        "mechanism": mechanism,
        "evidence_ref": evidence_ref,
    }
    for key, value in extra.items():
        if value is not None:
            payload[key] = value
    return payload


def repo_profile_exists(repo_root: str) -> Callable[[str], bool]:
    """Return a predicate: does ``docs/universe/<ticker>.md`` exist under root?"""

    from pathlib import Path

    root = Path(repo_root)

    def _exists(ticker: str) -> bool:
        return (root / "docs" / "universe" / f"{ticker.lower()}.md").is_file()

    return _exists


def _profile_path_for(ticker: str) -> str:
    return LAUNCH_PROFILE_PATHS.get(ticker.upper(), f"docs/universe/{ticker.lower()}.md")


async def seed_watch(
    store: PostgresUniverseStore,
    redis: RedisStreamClient,
    *,
    ticker: str,
    evidence_ref: str,
    mechanism: str = MECHANISM_MIKE_SEED,
    expiry: datetime | None = None,
    falsifier: str | None = None,
    cik: str | None = None,
) -> TransitionResult:
    """Record a Tier 2 watch entry. Requires evidence_ref AND (expiry OR falsifier).

    A malformed elevation (bad mechanism, missing evidence, neither expiry nor
    falsifier) is refused and emits ``research.universe-proposal-rejected`` —
    the deny path is as auditable as the allow path.
    """

    symbol = ticker.strip().upper()
    evidence = evidence_ref.strip()
    falsifier_clean = falsifier.strip() if falsifier and falsifier.strip() else None
    publisher = EventPublisher(redis)

    reason: str | None = None
    if mechanism not in MECHANISMS:
        reason = f"unknown mechanism {mechanism!r}; allowed: {sorted(MECHANISMS)}"
    elif not symbol:
        reason = "a watch seed requires a ticker"
    elif not evidence:
        reason = "a watch seed requires a resolvable evidence_ref"
    elif expiry is None and falsifier_clean is None:
        reason = "a watch seed requires an expiry or a falsifier — the soft cap"

    if reason is not None:
        await publisher.publish(
            stream=STREAM_PROPOSAL_REJECTED,
            produced_by=PRODUCED_BY,
            schema_version=SCHEMA_VERSION,
            payload=transition_payload(
                ticker=symbol or ticker.strip(),
                source_tier=TIER_DISCOVERY,
                destination_tier=TIER_WATCH,
                mechanism=mechanism,
                evidence_ref=evidence,
                reason=reason,
            ),
        )
        raise CuratorError(reason)

    existing = await store.get_tier_row(symbol)
    if existing is not None:
        # Dedupe (spec step 2): no tier transition occurred, so no event. The
        # accrual of new evidence onto an existing record is later scope.
        raise CuratorError(f"{symbol} is already in tier {existing['tier']!r}; re-seed is a no-op")

    entered_at = datetime.now(UTC)
    await store.insert_watch(
        ticker=symbol,
        cik=cik,
        mechanism=mechanism,
        evidence_ref=evidence,
        entered_at=entered_at,
        expiry=expiry,
        falsifier=falsifier_clean,
    )
    await publisher.publish(
        stream=STREAM_WATCH_ADDED,
        produced_by=PRODUCED_BY,
        schema_version=SCHEMA_VERSION,
        payload=transition_payload(
            ticker=symbol,
            source_tier=TIER_DISCOVERY,
            destination_tier=TIER_WATCH,
            mechanism=mechanism,
            evidence_ref=evidence,
            expiry=expiry.isoformat() if expiry else None,
            falsifier=falsifier_clean,
        ),
    )
    log.info("universe_curator.watch_added", ticker=symbol, mechanism=mechanism)
    return TransitionResult(ticker=symbol, stream=STREAM_WATCH_ADDED, detail="watch-added")


async def stage_transition(
    store: PostgresUniverseStore,
    *,
    ticker: str,
    kind: str,
    profile_exists: Callable[[str], bool],
    evidence_ref: str | None = None,
    mechanism: str | None = None,
    evict_ticker: str | None = None,
) -> TransitionResult:
    """Assemble a Tier 3 proposal into staging, with deterministic gate checks.

    No event fires at stage time — a staged proposal sits until Mike decides.
    Gate checks (promotion): a behavioral profile must exist (future-promotion
    prerequisite per Mike's 2026-07-23 grandfather ruling), and there must be
    cap headroom or a paired eviction of a currently-active name.
    """

    symbol = ticker.strip().upper()
    if kind not in (KIND_PROMOTION, KIND_EVICTION):
        raise CuratorError(f"unknown stage kind {kind!r}; allowed: promotion, eviction")

    existing = await store.get_tier_row(symbol)

    if kind == KIND_PROMOTION:
        if not profile_exists(symbol):
            raise CuratorError(
                f"{symbol}: a behavioral profile docs/universe/{symbol.lower()}.md must "
                "exist before promotion (Mike 2026-07-23: prerequisite for future promotions)"
            )
        source_tier = str(existing["tier"]) if existing else TIER_DISCOVERY
        if existing and existing["tier"] == TIER_ACTIVE:
            raise CuratorError(f"{symbol} is already Active; nothing to promote")
        mech = mechanism or (str(existing["mechanism"]) if existing else None)
        evidence = evidence_ref or (str(existing["evidence_ref"]) if existing else None)
        if not mech or not evidence:
            raise CuratorError(
                f"{symbol}: promotion of a name not on watch needs --mechanism and --evidence-ref"
            )
        if mech not in MECHANISMS:
            raise CuratorError(f"unknown mechanism {mech!r}; allowed: {sorted(MECHANISMS)}")

        paired = evict_ticker.strip().upper() if evict_ticker and evict_ticker.strip() else None
        active_count = await store.active_count()
        if active_count >= TIER3_CAP and paired is None:
            raise CuratorError(
                f"Tier 3 is at cap ({active_count}/{TIER3_CAP}); promotion must name "
                "an eviction candidate (--evict TICKER)"
            )
        if paired is not None:
            evict_row = await store.get_tier_row(paired)
            if evict_row is None or evict_row["tier"] != TIER_ACTIVE:
                raise CuratorError(f"eviction candidate {paired} is not currently Active")

        consequences = await store.strategies_referencing(symbol, CONSEQUENCE_STATUSES)
        if paired is not None:
            consequences += await store.strategies_referencing(paired, CONSEQUENCE_STATUSES)
        destination_tier = TIER_ACTIVE

    else:  # eviction
        if existing is None or existing["tier"] != TIER_ACTIVE:
            raise CuratorError(f"{symbol} is not currently Active; nothing to evict")
        source_tier = TIER_ACTIVE
        destination_tier = TIER_DISCOVERY
        mech = str(existing["mechanism"])
        evidence = evidence_ref or str(existing["evidence_ref"])
        paired = None
        consequences = await store.strategies_referencing(symbol, CONSEQUENCE_STATUSES)

    staging_id = str(ULID())
    await store.insert_staging(
        staging_id=staging_id,
        ticker=symbol,
        kind=kind,
        source_tier=source_tier,
        destination_tier=destination_tier,
        mechanism=mech,
        evidence_ref=evidence,
        paired_eviction_ticker=paired,
        consequences=consequences,
        staged_at=datetime.now(UTC),
    )
    log.info(
        "universe_curator.staged",
        staging_id=staging_id,
        ticker=symbol,
        kind=kind,
        evict=paired,
        consequences=len(consequences),
    )
    detail = f"staged {kind} {staging_id}"
    if paired is not None:
        detail += f" (evicts {paired})"
    return TransitionResult(
        ticker=symbol,
        stream=None,
        detail=detail,
        staging_id=staging_id,
        consequences=consequences,
    )


async def approve_staged(
    store: PostgresUniverseStore,
    redis: RedisStreamClient,
    *,
    staging_id: str,
    note: str | None = None,
) -> TransitionResult:
    """Execute Mike's approval of a staged proposal — the only Tier 3 mutation path."""

    row = await store.get_staging_row(staging_id)
    if row is None:
        raise CuratorError(f"staging {staging_id} does not exist")
    if row["disposition"] != "pending":
        raise CuratorError(f"staging {staging_id} is already {row['disposition']!r}, not pending")

    publisher = EventPublisher(redis)
    symbol = str(row["ticker"])
    mechanism = str(row["mechanism"])
    evidence_ref = str(row["evidence_ref"])
    resolved_at = datetime.now(UTC)

    if row["kind"] == KIND_PROMOTION:
        paired = row["paired_eviction_ticker"]
        evict_row = await store.get_tier_row(str(paired)) if paired else None
        existing = await store.get_tier_row(symbol)
        cik = str(existing["cik"]) if existing and existing["cik"] else None
        await store.apply_promotion(
            ticker=symbol,
            cik=cik,
            mechanism=mechanism,
            evidence_ref=evidence_ref,
            entered_at=resolved_at,
            profile_path=_profile_path_for(symbol),
            staging_id=staging_id,
            note=note,
            resolved_at=resolved_at,
            evict_ticker=str(paired) if paired else None,
        )
        await publisher.publish(
            stream=STREAM_PROMOTED,
            produced_by=PRODUCED_BY,
            schema_version=SCHEMA_VERSION,
            payload=transition_payload(
                ticker=symbol,
                source_tier=str(row["source_tier"]),
                destination_tier=TIER_ACTIVE,
                mechanism=mechanism,
                evidence_ref=evidence_ref,
                note=note,
            ),
        )
        detail = f"promoted {symbol}"
        if paired:
            evict_mech = str(evict_row["mechanism"]) if evict_row else MECHANISM_MIKE_SEED
            evict_evidence = str(evict_row["evidence_ref"]) if evict_row else evidence_ref
            await publisher.publish(
                stream=STREAM_EVICTED,
                produced_by=PRODUCED_BY,
                schema_version=SCHEMA_VERSION,
                payload=transition_payload(
                    ticker=str(paired),
                    source_tier=TIER_ACTIVE,
                    destination_tier=TIER_DISCOVERY,
                    mechanism=evict_mech,
                    evidence_ref=evict_evidence,
                    reason="paired-eviction",
                    note=note,
                ),
            )
            detail += f", evicted {paired}"
        log.info("universe_curator.promoted", ticker=symbol, evict=paired)
        return TransitionResult(ticker=symbol, stream=STREAM_PROMOTED, detail=detail)

    # eviction
    await store.apply_eviction(
        ticker=symbol, staging_id=staging_id, note=note, resolved_at=resolved_at
    )
    await publisher.publish(
        stream=STREAM_EVICTED,
        produced_by=PRODUCED_BY,
        schema_version=SCHEMA_VERSION,
        payload=transition_payload(
            ticker=symbol,
            source_tier=TIER_ACTIVE,
            destination_tier=TIER_DISCOVERY,
            mechanism=mechanism,
            evidence_ref=evidence_ref,
            note=note,
        ),
    )
    log.info("universe_curator.evicted", ticker=symbol)
    return TransitionResult(ticker=symbol, stream=STREAM_EVICTED, detail=f"evicted {symbol}")


async def reject_staged(
    store: PostgresUniverseStore,
    redis: RedisStreamClient,
    *,
    staging_id: str,
    note: str,
) -> TransitionResult:
    """Execute Mike's rejection: resolve the staging row and emit the deny event.

    A rejected promotion leaves the watch entry in Tier 2 with its expiry clock
    running (spec §Tier 3 promotion, step 4).
    """

    if not note.strip():
        raise CuratorError("a rejection requires a note — the deny path is auditable")
    row = await store.get_staging_row(staging_id)
    if row is None:
        raise CuratorError(f"staging {staging_id} does not exist")
    if row["disposition"] != "pending":
        raise CuratorError(f"staging {staging_id} is already {row['disposition']!r}, not pending")

    resolved_at = datetime.now(UTC)
    await store.resolve_staging(
        staging_id, disposition=DISPOSITION_REJECTED, note=note, resolved_at=resolved_at
    )
    publisher = EventPublisher(redis)
    await publisher.publish(
        stream=STREAM_PROPOSAL_REJECTED,
        produced_by=PRODUCED_BY,
        schema_version=SCHEMA_VERSION,
        payload=transition_payload(
            ticker=str(row["ticker"]),
            source_tier=str(row["source_tier"]),
            destination_tier=str(row["destination_tier"]),
            mechanism=str(row["mechanism"]),
            evidence_ref=str(row["evidence_ref"]),
            reason="mike-rejected",
            note=note,
        ),
    )
    log.info("universe_curator.proposal_rejected", staging_id=staging_id, ticker=row["ticker"])
    return TransitionResult(
        ticker=str(row["ticker"]), stream=STREAM_PROPOSAL_REJECTED, detail="proposal-rejected"
    )


async def extend_watch(
    store: PostgresUniverseStore,
    *,
    ticker: str,
    expiry: datetime,
) -> TransitionResult:
    """Renew a Tier 2 watch entry's expiry clock. No tier transition, no event."""

    symbol = ticker.strip().upper()
    row = await store.get_tier_row(symbol)
    if row is None or row["tier"] != TIER_WATCH:
        raise CuratorError(f"{symbol} is not a Tier 2 watch entry; cannot extend")
    await store.update_watch_expiry(symbol, expiry)
    log.info("universe_curator.watch_extended", ticker=symbol, expiry=expiry.isoformat())
    return TransitionResult(
        ticker=symbol, stream=None, detail=f"extended {symbol} to {expiry.date().isoformat()}"
    )


async def expire_watch(
    store: PostgresUniverseStore,
    redis: RedisStreamClient,
    *,
    ticker: str,
    reason: str = "expired",
) -> TransitionResult:
    """Expire a Tier 2 watch entry (on-demand). Emits ``universe-watch-expired``."""

    symbol = ticker.strip().upper()
    row = await store.get_tier_row(symbol)
    if row is None or row["tier"] != TIER_WATCH:
        raise CuratorError(f"{symbol} is not a Tier 2 watch entry; cannot expire")
    await store.delete_ticker(symbol)
    await _publish_expired(redis, row, reason=reason)
    log.info("universe_curator.watch_expired", ticker=symbol, reason=reason)
    return TransitionResult(ticker=symbol, stream=STREAM_WATCH_EXPIRED, detail=f"expired {symbol}")


async def expiry_sweep(
    store: PostgresUniverseStore,
    redis: RedisStreamClient,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Daily sweep: expire every Tier 2 entry past its expiry with no renewal.

    Date-expiry only — falsifier observation belongs to the proposing sources
    (spec default, open question 4). Returns the tickers expired this sweep.
    """

    at = now if now is not None else datetime.now(UTC)
    due = await store.expired_watch(at)
    expired: list[str] = []
    for row in due:
        symbol = str(row["ticker"])
        await store.delete_ticker(symbol)
        await _publish_expired(redis, row, reason="expired")
        expired.append(symbol)
    if expired:
        log.info("universe_curator.expiry_sweep", expired=expired)
    return expired


async def _publish_expired(redis: RedisStreamClient, row: dict[str, Any], *, reason: str) -> None:
    publisher = EventPublisher(redis)
    await publisher.publish(
        stream=STREAM_WATCH_EXPIRED,
        produced_by=PRODUCED_BY,
        schema_version=SCHEMA_VERSION,
        payload=transition_payload(
            ticker=str(row["ticker"]),
            source_tier=TIER_WATCH,
            destination_tier=TIER_DISCOVERY,
            mechanism=str(row["mechanism"]),
            evidence_ref=str(row["evidence_ref"]),
            reason=reason,
        ),
    )


async def load_launch_list(
    store: PostgresUniverseStore,
    redis: RedisStreamClient,
) -> list[str]:
    """Load the locked Tier 3 launch list directly into Active, idempotently.

    Each name goes straight to Tier 3 (no staging row) and emits one
    ``research.universe-promoted`` with mechanism ``mike-seed`` and the DQ-004
    lock-in as evidence — so day-one membership is as audit-answerable as
    everything after it. Names already Active are skipped (no event), which
    makes a re-run a no-op.
    """

    publisher = EventPublisher(redis)
    loaded: list[str] = []
    for entry in LAUNCH_LIST:
        ticker = entry.ticker
        existing = await store.get_tier_row(ticker)
        if existing is not None and existing["tier"] == TIER_ACTIVE:
            continue
        entered_at = datetime.now(UTC)
        source_tier = str(existing["tier"]) if existing else TIER_DISCOVERY
        await store.upsert_active(
            ticker=ticker,
            cik=LAUNCH_CIKS.get(ticker),
            mechanism=MECHANISM_MIKE_SEED,
            evidence_ref=LAUNCH_EVIDENCE_REF,
            entered_at=entered_at,
            profile_path=LAUNCH_PROFILE_PATHS.get(ticker),
        )
        await publisher.publish(
            stream=STREAM_PROMOTED,
            produced_by=PRODUCED_BY,
            schema_version=SCHEMA_VERSION,
            payload=transition_payload(
                ticker=ticker,
                source_tier=source_tier,
                destination_tier=TIER_ACTIVE,
                mechanism=MECHANISM_MIKE_SEED,
                evidence_ref=LAUNCH_EVIDENCE_REF,
                category=entry.category,
            ),
        )
        loaded.append(ticker)
    log.info("universe_curator.launch_list_loaded", loaded=len(loaded))
    return loaded


def format_consequences(consequences: Sequence[dict[str, Any]]) -> str:
    """Render the consequence annotation for the CLI operator."""

    if not consequences:
        return "  (no live or paper-stage strategies reference this name)"
    lines = []
    for c in consequences:
        lines.append(
            f"  - {c.get('name', '?')} [{c.get('status', '?')}] ({c.get('strategy_id', '?')})"
        )
    return "\n".join(lines)


__all__ = [
    "CONSEQUENCE_STATUSES",
    "LAUNCH_EVIDENCE_REF",
    "MECHANISMS",
    "MECHANISM_MIKE_SEED",
    "PRODUCED_BY",
    "SCHEMA_VERSION",
    "STREAM_EVICTED",
    "STREAM_PROMOTED",
    "STREAM_PROPOSAL_REJECTED",
    "STREAM_WATCH_ADDED",
    "STREAM_WATCH_EXPIRED",
    "CuratorError",
    "RedisStreamClient",
    "TransitionResult",
    "approve_staged",
    "expire_watch",
    "expiry_sweep",
    "extend_watch",
    "format_consequences",
    "load_launch_list",
    "reject_staged",
    "repo_profile_exists",
    "seed_watch",
    "stage_transition",
    "transition_payload",
]
