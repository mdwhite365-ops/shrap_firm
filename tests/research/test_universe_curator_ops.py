"""Tests for the Universe Curator tier-transition operations (ADR-0012).

An in-memory fake store stands in for ``PostgresUniverseStore`` so the tests
exercise the real business logic — validation, gate checks, cap enforcement,
event payloads — rather than SQL wiring (that is covered in
``test_universe_curator_store.py``). A ``FakeRedis`` captures published events.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from shrap.research.universe_curator.curator import (
    LAUNCH_EVIDENCE_REF,
    MECHANISM_MIKE_SEED,
    STREAM_EVICTED,
    STREAM_PROMOTED,
    STREAM_PROPOSAL_REJECTED,
    STREAM_WATCH_ADDED,
    STREAM_WATCH_EXPIRED,
    CuratorError,
    approve_staged,
    expire_watch,
    expiry_sweep,
    extend_watch,
    reject_staged,
    seed_watch,
    stage_transition,
)
from shrap.research.universe_curator.store import TIER_ACTIVE, TIER_WATCH


class FakeRedis:
    def __init__(self) -> None:
        self.streams: list[str] = []
        self.payloads: list[dict[str, Any]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.streams.append(stream)
        self.payloads.append(json.loads(fields["payload"]))
        return f"{len(self.streams)}-0"


class FakeStore:
    """In-memory stand-in for PostgresUniverseStore."""

    def __init__(self) -> None:
        self.tiers: dict[str, dict[str, Any]] = {}
        self.staging: dict[str, dict[str, Any]] = {}
        self.strategies: dict[str, list[dict[str, Any]]] = {}

    async def get_tier_row(self, ticker: str) -> dict[str, Any] | None:
        row = self.tiers.get(ticker)
        return dict(row) if row is not None else None

    async def list_by_tier(self, tier: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.tiers.values() if r["tier"] == tier]

    async def active_count(self) -> int:
        return sum(1 for r in self.tiers.values() if r["tier"] == TIER_ACTIVE)

    async def expired_watch(self, now: datetime) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.tiers.values()
            if r["tier"] == TIER_WATCH and r["expiry"] is not None and r["expiry"] <= now
        ]

    async def insert_watch(
        self,
        *,
        ticker: str,
        cik: str | None,
        mechanism: str,
        evidence_ref: str,
        entered_at: datetime,
        expiry: datetime | None,
        falsifier: str | None,
        profile_path: str | None = None,
    ) -> None:
        self.tiers[ticker] = {
            "ticker": ticker,
            "cik": cik,
            "tier": TIER_WATCH,
            "mechanism": mechanism,
            "evidence_ref": evidence_ref,
            "entered_at": entered_at,
            "expiry": expiry,
            "falsifier": falsifier,
            "profile_path": profile_path,
        }

    async def upsert_active(
        self,
        *,
        ticker: str,
        cik: str | None,
        mechanism: str,
        evidence_ref: str,
        entered_at: datetime,
        profile_path: str | None,
    ) -> None:
        existing = self.tiers.get(ticker, {})
        self.tiers[ticker] = {
            "ticker": ticker,
            "cik": cik if cik is not None else existing.get("cik"),
            "tier": TIER_ACTIVE,
            "mechanism": mechanism,
            "evidence_ref": evidence_ref,
            "entered_at": entered_at,
            "expiry": None,
            "falsifier": None,
            "profile_path": profile_path
            if profile_path is not None
            else existing.get("profile_path"),
        }

    async def update_watch_expiry(self, ticker: str, expiry: datetime) -> bool:
        row = self.tiers.get(ticker)
        if row is None or row["tier"] != TIER_WATCH:
            return False
        row["expiry"] = expiry
        return True

    async def delete_ticker(self, ticker: str) -> bool:
        return self.tiers.pop(ticker, None) is not None

    async def insert_staging(
        self,
        *,
        staging_id: str,
        ticker: str,
        kind: str,
        source_tier: str,
        destination_tier: str,
        mechanism: str,
        evidence_ref: str,
        paired_eviction_ticker: str | None,
        consequences: list[dict[str, Any]],
        staged_at: datetime,
    ) -> None:
        self.staging[staging_id] = {
            "staging_id": staging_id,
            "ticker": ticker,
            "kind": kind,
            "source_tier": source_tier,
            "destination_tier": destination_tier,
            "mechanism": mechanism,
            "evidence_ref": evidence_ref,
            "paired_eviction_ticker": paired_eviction_ticker,
            "consequences": consequences,
            "disposition": "pending",
            "note": None,
            "staged_at": staged_at,
            "resolved_at": None,
        }

    async def get_staging_row(self, staging_id: str) -> dict[str, Any] | None:
        row = self.staging.get(staging_id)
        return dict(row) if row is not None else None

    async def pending_staging(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.staging.values() if r["disposition"] == "pending"]

    async def resolve_staging(
        self, staging_id: str, *, disposition: str, note: str | None, resolved_at: datetime
    ) -> bool:
        row = self.staging.get(staging_id)
        if row is None or row["disposition"] != "pending":
            return False
        row["disposition"] = disposition
        row["note"] = note
        row["resolved_at"] = resolved_at
        return True

    async def apply_promotion(
        self,
        *,
        ticker: str,
        cik: str | None,
        mechanism: str,
        evidence_ref: str,
        entered_at: datetime,
        profile_path: str | None,
        staging_id: str,
        note: str | None,
        resolved_at: datetime,
        evict_ticker: str | None = None,
    ) -> None:
        if evict_ticker is not None:
            self.tiers.pop(evict_ticker, None)
        await self.upsert_active(
            ticker=ticker,
            cik=cik,
            mechanism=mechanism,
            evidence_ref=evidence_ref,
            entered_at=entered_at,
            profile_path=profile_path,
        )
        await self.resolve_staging(
            staging_id, disposition="approved", note=note, resolved_at=resolved_at
        )

    async def apply_eviction(
        self, *, ticker: str, staging_id: str, note: str | None, resolved_at: datetime
    ) -> None:
        self.tiers.pop(ticker, None)
        await self.resolve_staging(
            staging_id, disposition="approved", note=note, resolved_at=resolved_at
        )

    async def strategies_referencing(self, ticker: str, statuses: Any) -> list[dict[str, Any]]:
        return self.strategies.get(ticker, [])


def _always(_: str) -> bool:
    return True


def _never(_: str) -> bool:
    return False


# --------------------------------------------------------------------------- #
# seed
# --------------------------------------------------------------------------- #


async def test_seed_records_watch_and_emits_watch_added() -> None:
    store = FakeStore()
    redis = FakeRedis()
    expiry = datetime(2026, 12, 31, tzinfo=UTC)

    result = await seed_watch(
        store,  # type: ignore[arg-type]
        redis,  # type: ignore[arg-type]
        ticker="rklb",
        evidence_ref="intelligence.filings#0001",
        mechanism="structural-finding",
        expiry=expiry,
    )

    assert result.stream == STREAM_WATCH_ADDED
    assert store.tiers["RKLB"]["tier"] == TIER_WATCH
    assert redis.streams == [STREAM_WATCH_ADDED]
    payload = redis.payloads[0]
    assert payload["ticker"] == "RKLB"
    assert payload["source_tier"] == "discovery"
    assert payload["destination_tier"] == "watch"
    assert payload["mechanism"] == "structural-finding"
    assert payload["evidence_ref"] == "intelligence.filings#0001"


async def test_seed_with_falsifier_only_succeeds() -> None:
    store = FakeStore()
    redis = FakeRedis()

    result = await seed_watch(
        store,  # type: ignore[arg-type]
        redis,  # type: ignore[arg-type]
        ticker="RKLB",
        evidence_ref="ref",
        falsifier="collar floor breaks before close",
    )

    assert result.stream == STREAM_WATCH_ADDED
    assert store.tiers["RKLB"]["falsifier"] == "collar floor breaks before close"


async def test_seed_without_expiry_or_falsifier_emits_rejection() -> None:
    store = FakeStore()
    redis = FakeRedis()

    with pytest.raises(CuratorError, match="expiry or a falsifier"):
        await seed_watch(
            store,  # type: ignore[arg-type]
            redis,  # type: ignore[arg-type]
            ticker="RKLB",
            evidence_ref="ref",
        )

    assert redis.streams == [STREAM_PROPOSAL_REJECTED]
    assert redis.payloads[0]["ticker"] == "RKLB"
    assert "expiry or a falsifier" in redis.payloads[0]["reason"]
    assert "RKLB" not in store.tiers


async def test_seed_without_evidence_emits_rejection() -> None:
    store = FakeStore()
    redis = FakeRedis()

    with pytest.raises(CuratorError, match="evidence_ref"):
        await seed_watch(
            store,  # type: ignore[arg-type]
            redis,  # type: ignore[arg-type]
            ticker="RKLB",
            evidence_ref="  ",
            falsifier="x",
        )
    assert redis.streams == [STREAM_PROPOSAL_REJECTED]


async def test_seed_unknown_mechanism_emits_rejection() -> None:
    store = FakeStore()
    redis = FakeRedis()

    with pytest.raises(CuratorError, match="unknown mechanism"):
        await seed_watch(
            store,  # type: ignore[arg-type]
            redis,  # type: ignore[arg-type]
            ticker="RKLB",
            evidence_ref="ref",
            mechanism="vibes",
            falsifier="x",
        )
    assert redis.streams == [STREAM_PROPOSAL_REJECTED]


async def test_seed_dedupe_refuses_without_event() -> None:
    store = FakeStore()
    redis = FakeRedis()
    await seed_watch(
        store,  # type: ignore[arg-type]
        redis,  # type: ignore[arg-type]
        ticker="RKLB",
        evidence_ref="ref",
        falsifier="x",
    )
    redis.streams.clear()
    redis.payloads.clear()

    with pytest.raises(CuratorError, match="already in tier"):
        await seed_watch(
            store,  # type: ignore[arg-type]
            redis,  # type: ignore[arg-type]
            ticker="RKLB",
            evidence_ref="ref2",
            falsifier="y",
        )
    assert redis.streams == []


# --------------------------------------------------------------------------- #
# stage gate checks
# --------------------------------------------------------------------------- #


def _watch(store: FakeStore, ticker: str) -> None:
    store.tiers[ticker] = {
        "ticker": ticker,
        "cik": None,
        "tier": TIER_WATCH,
        "mechanism": "structural-finding",
        "evidence_ref": "ref",
        "entered_at": datetime.now(UTC),
        "expiry": datetime(2026, 12, 31, tzinfo=UTC),
        "falsifier": None,
        "profile_path": None,
    }


def _active(store: FakeStore, ticker: str) -> None:
    store.tiers[ticker] = {
        "ticker": ticker,
        "cik": None,
        "tier": TIER_ACTIVE,
        "mechanism": "mike-seed",
        "evidence_ref": "ref",
        "entered_at": datetime.now(UTC),
        "expiry": None,
        "falsifier": None,
        "profile_path": "docs/universe/x.md",
    }


async def test_stage_promotion_requires_profile() -> None:
    store = FakeStore()
    _watch(store, "RKLB")

    with pytest.raises(CuratorError, match="behavioral profile"):
        await stage_transition(
            store,  # type: ignore[arg-type]
            ticker="RKLB",
            kind="promotion",
            profile_exists=_never,
        )
    assert store.staging == {}


async def test_stage_promotion_with_headroom_stages_and_annotates() -> None:
    store = FakeStore()
    _watch(store, "RKLB")
    store.strategies["RKLB"] = [{"strategy_id": "01S", "name": "rklb-collar", "status": "paper"}]

    result = await stage_transition(
        store,  # type: ignore[arg-type]
        ticker="RKLB",
        kind="promotion",
        profile_exists=_always,
    )

    assert result.staging_id is not None
    staged = store.staging[result.staging_id]
    assert staged["kind"] == "promotion"
    assert staged["source_tier"] == TIER_WATCH
    assert staged["destination_tier"] == TIER_ACTIVE
    assert result.consequences[0]["name"] == "rklb-collar"


async def test_stage_promotion_at_cap_requires_eviction() -> None:
    store = FakeStore()
    for i in range(50):
        _active(store, f"N{i}")
    _watch(store, "RKLB")

    with pytest.raises(CuratorError, match="at cap"):
        await stage_transition(
            store,  # type: ignore[arg-type]
            ticker="RKLB",
            kind="promotion",
            profile_exists=_always,
        )


async def test_stage_promotion_at_cap_with_valid_eviction_stages() -> None:
    store = FakeStore()
    for i in range(49):
        _active(store, f"N{i}")
    _active(store, "OLD")
    _watch(store, "RKLB")

    result = await stage_transition(
        store,  # type: ignore[arg-type]
        ticker="RKLB",
        kind="promotion",
        profile_exists=_always,
        evict_ticker="OLD",
    )
    assert result.staging_id is not None
    assert store.staging[result.staging_id]["paired_eviction_ticker"] == "OLD"


async def test_stage_promotion_evict_must_be_active() -> None:
    store = FakeStore()
    for i in range(50):
        _active(store, f"N{i}")
    _watch(store, "RKLB")

    with pytest.raises(CuratorError, match="not currently Active"):
        await stage_transition(
            store,  # type: ignore[arg-type]
            ticker="RKLB",
            kind="promotion",
            profile_exists=_always,
            evict_ticker="RKLB",  # a watch entry, not active
        )


async def test_stage_eviction_of_non_active_refused() -> None:
    store = FakeStore()
    _watch(store, "RKLB")

    with pytest.raises(CuratorError, match="not currently Active"):
        await stage_transition(
            store,  # type: ignore[arg-type]
            ticker="RKLB",
            kind="eviction",
            profile_exists=_always,
        )


# --------------------------------------------------------------------------- #
# approve / reject
# --------------------------------------------------------------------------- #


async def test_approve_promotion_mutates_tier_and_emits_promoted() -> None:
    store = FakeStore()
    _watch(store, "RKLB")
    result = await stage_transition(
        store,  # type: ignore[arg-type]
        ticker="RKLB",
        kind="promotion",
        profile_exists=_always,
    )
    redis = FakeRedis()
    assert result.staging_id is not None

    decision = await approve_staged(
        store,  # type: ignore[arg-type]
        redis,  # type: ignore[arg-type]
        staging_id=result.staging_id,
        note="earned it",
    )

    assert decision.stream == STREAM_PROMOTED
    assert store.tiers["RKLB"]["tier"] == TIER_ACTIVE
    assert store.staging[result.staging_id]["disposition"] == "approved"
    assert redis.streams == [STREAM_PROMOTED]
    payload = redis.payloads[0]
    assert payload["source_tier"] == TIER_WATCH
    assert payload["destination_tier"] == TIER_ACTIVE
    assert payload["mechanism"] == "structural-finding"
    assert payload["note"] == "earned it"


async def test_approve_paired_promotion_emits_promoted_and_evicted() -> None:
    store = FakeStore()
    for i in range(49):
        _active(store, f"N{i}")
    _active(store, "OLD")
    _watch(store, "RKLB")
    result = await stage_transition(
        store,  # type: ignore[arg-type]
        ticker="RKLB",
        kind="promotion",
        profile_exists=_always,
        evict_ticker="OLD",
    )
    redis = FakeRedis()
    assert result.staging_id is not None

    await approve_staged(
        store,  # type: ignore[arg-type]
        redis,  # type: ignore[arg-type]
        staging_id=result.staging_id,
    )

    assert redis.streams == [STREAM_PROMOTED, STREAM_EVICTED]
    assert "OLD" not in store.tiers
    assert store.tiers["RKLB"]["tier"] == TIER_ACTIVE
    assert await store.active_count() == 50
    evicted = redis.payloads[1]
    assert evicted["ticker"] == "OLD"
    assert evicted["source_tier"] == TIER_ACTIVE
    assert evicted["destination_tier"] == "discovery"


async def test_approve_eviction_emits_evicted() -> None:
    store = FakeStore()
    _active(store, "OLD")
    result = await stage_transition(
        store,  # type: ignore[arg-type]
        ticker="OLD",
        kind="eviction",
        profile_exists=_always,
    )
    redis = FakeRedis()
    assert result.staging_id is not None

    await approve_staged(
        store,  # type: ignore[arg-type]
        redis,  # type: ignore[arg-type]
        staging_id=result.staging_id,
    )
    assert redis.streams == [STREAM_EVICTED]
    assert "OLD" not in store.tiers


async def test_approve_missing_or_resolved_refuses() -> None:
    store = FakeStore()
    redis = FakeRedis()

    with pytest.raises(CuratorError, match="does not exist"):
        await approve_staged(store, redis, staging_id="nope")  # type: ignore[arg-type]

    _active(store, "OLD")
    result = await stage_transition(
        store,  # type: ignore[arg-type]
        ticker="OLD",
        kind="eviction",
        profile_exists=_always,
    )
    assert result.staging_id is not None
    await approve_staged(store, redis, staging_id=result.staging_id)  # type: ignore[arg-type]
    with pytest.raises(CuratorError, match="already"):
        await approve_staged(store, redis, staging_id=result.staging_id)  # type: ignore[arg-type]


async def test_reject_resolves_staging_and_emits_rejected() -> None:
    store = FakeStore()
    _watch(store, "RKLB")
    result = await stage_transition(
        store,  # type: ignore[arg-type]
        ticker="RKLB",
        kind="promotion",
        profile_exists=_always,
    )
    redis = FakeRedis()
    assert result.staging_id is not None

    await reject_staged(
        store,  # type: ignore[arg-type]
        redis,  # type: ignore[arg-type]
        staging_id=result.staging_id,
        note="liquidity too thin",
    )

    assert redis.streams == [STREAM_PROPOSAL_REJECTED]
    assert store.staging[result.staging_id]["disposition"] == "rejected"
    assert store.staging[result.staging_id]["note"] == "liquidity too thin"
    # watch entry survives the rejection
    assert store.tiers["RKLB"]["tier"] == TIER_WATCH
    payload = redis.payloads[0]
    assert payload["reason"] == "mike-rejected"
    assert payload["note"] == "liquidity too thin"


async def test_reject_requires_note() -> None:
    store = FakeStore()
    redis = FakeRedis()
    with pytest.raises(CuratorError, match="note"):
        await reject_staged(store, redis, staging_id="x", note="  ")  # type: ignore[arg-type]
    assert redis.streams == []


# --------------------------------------------------------------------------- #
# extend / expire / sweep
# --------------------------------------------------------------------------- #


async def test_extend_updates_expiry_without_event() -> None:
    store = FakeStore()
    _watch(store, "RKLB")
    new_expiry = datetime(2027, 6, 30, tzinfo=UTC)

    result = await extend_watch(store, ticker="RKLB", expiry=new_expiry)  # type: ignore[arg-type]

    assert result.stream is None
    assert store.tiers["RKLB"]["expiry"] == new_expiry


async def test_extend_non_watch_refused() -> None:
    store = FakeStore()
    _active(store, "AAPL")
    with pytest.raises(CuratorError, match="not a Tier 2 watch"):
        await extend_watch(store, ticker="AAPL", expiry=datetime(2027, 1, 1, tzinfo=UTC))  # type: ignore[arg-type]


async def test_expire_watch_emits_watch_expired() -> None:
    store = FakeStore()
    _watch(store, "RKLB")
    redis = FakeRedis()

    await expire_watch(store, redis, ticker="RKLB")  # type: ignore[arg-type]

    assert redis.streams == [STREAM_WATCH_EXPIRED]
    assert "RKLB" not in store.tiers
    payload = redis.payloads[0]
    assert payload["source_tier"] == TIER_WATCH
    assert payload["destination_tier"] == "discovery"
    assert payload["reason"] == "expired"


async def test_expiry_sweep_expires_due_entries_only() -> None:
    store = FakeStore()
    now = datetime(2026, 8, 1, tzinfo=UTC)
    # one past due, one in the future
    store.tiers["OLD"] = {
        "ticker": "OLD",
        "cik": None,
        "tier": TIER_WATCH,
        "mechanism": "mike-seed",
        "evidence_ref": "ref",
        "entered_at": now - timedelta(days=200),
        "expiry": now - timedelta(days=1),
        "falsifier": None,
        "profile_path": None,
    }
    store.tiers["FRESH"] = {
        "ticker": "FRESH",
        "cik": None,
        "tier": TIER_WATCH,
        "mechanism": "mike-seed",
        "evidence_ref": "ref",
        "entered_at": now,
        "expiry": now + timedelta(days=30),
        "falsifier": None,
        "profile_path": None,
    }
    redis = FakeRedis()

    expired = await expiry_sweep(store, redis, now=now)  # type: ignore[arg-type]

    assert expired == ["OLD"]
    assert "OLD" not in store.tiers
    assert "FRESH" in store.tiers
    assert redis.streams == [STREAM_WATCH_EXPIRED]
    assert redis.payloads[0]["reason"] == "expired"


async def test_seed_launch_evidence_constant_is_dq004() -> None:
    # Guardrail: the launch evidence ref points at the DQ-004 lock-in decision.
    assert LAUNCH_EVIDENCE_REF == "docs/status/decision-queue.md#dq-004 (locked 2026-07-23)"
    assert MECHANISM_MIKE_SEED == "mike-seed"
