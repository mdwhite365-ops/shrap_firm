"""Tests for the Tier 3 membership gate (ADR-0012)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from shrap.events import Envelope, ReceivedEvent, normalize_redis_fields
from shrap.risk_compliance.pre_trade import RiskPolicy
from shrap.risk_compliance.pre_trade_checker_agent import process_intent_event
from shrap.risk_compliance.rate_limit import RateLimitConfig, RedisRateLimiter
from shrap.risk_compliance.tier3_membership import (
    TICKER_NOT_IN_TIER3,
    TIER3_STATE_UNAVAILABLE,
    Tier3MembershipGate,
)


class FakeTierConn:
    """fetchrow fake over research.universe_tiers, keyed by ticker."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.queries: list[tuple[str, tuple[object, ...]]] = []
        self.raise_error: Exception | None = None

    async def fetchrow(self, sql: str, *args: object) -> Mapping[str, Any] | None:
        self.queries.append((sql, args))
        if self.raise_error is not None:
            raise self.raise_error
        return self.rows.get(str(args[0]))


class FakeTierAcquire:
    def __init__(self, conn: FakeTierConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeTierConn:
        return self._conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakeTierPool:
    def __init__(self) -> None:
        self.conn = FakeTierConn()

    def acquire(self) -> FakeTierAcquire:
        return FakeTierAcquire(self.conn)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeStreamRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        return f"178012860000{len(self.calls)}-0"


class FakeRateRedis:
    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.flags: dict[str, str] = {}

    async def incr(self, name: str) -> int:
        self.counters[name] = self.counters.get(name, 0) + 1
        return self.counters[name]

    async def expire(self, name: str, time: int) -> bool:
        return True

    async def set(
        self,
        name: str,
        value: str,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | None:
        if nx and name in self.flags:
            return None
        self.flags[name] = value
        return True


def _gate(
    pool: FakeTierPool, ttl: float = 30.0, clock: FakeClock | None = None
) -> Tier3MembershipGate:
    return Tier3MembershipGate(pool, ttl_seconds=ttl, clock=clock or FakeClock())


def _intent_event(ticker: str = "AAPL") -> ReceivedEvent:
    return ReceivedEvent(
        stream="trading.decision.intent",
        redis_stream_id="1780128600001-0",
        envelope=Envelope(
            event_id="01KINTENT00000000000000001",
            schema_version="1.0.0",
            produced_at=datetime.now(UTC),
            produced_by="trading-floor/spine-smoke",
            payload={
                "source": "handcrafted",
                "ticker": ticker,
                "side": "buy",
                "quantity": 1,
                "strategy_ids": ["smoke"],
                "mode": "paper",
            },
        ),
    )


def _last_payload(stream_redis: FakeStreamRedis) -> dict[str, Any]:
    envelope = Envelope.from_redis_fields(normalize_redis_fields(stream_redis.calls[-1][1]))
    assert envelope.payload is not None
    return envelope.payload


@pytest.mark.asyncio
async def test_flag_off_skips_tier3_rule_and_orders_flow() -> None:
    """With enforcement off no gate is wired in, and orders flow untouched."""

    stream_redis = FakeStreamRedis()
    policy = RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=1)

    result = await process_intent_event(stream_redis, _intent_event(), policy, tier3_gate=None)

    assert result.stream == "risk.intent.approved"


@pytest.mark.asyncio
async def test_tier3_member_passes() -> None:
    stream_redis = FakeStreamRedis()
    pool = FakeTierPool()
    pool.conn.rows["AAPL"] = {"tier": "active"}
    policy = RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=1)

    result = await process_intent_event(
        stream_redis, _intent_event(), policy, tier3_gate=_gate(pool)
    )

    assert result.stream == "risk.intent.approved"
    assert len(pool.conn.queries) == 1


@pytest.mark.asyncio
async def test_ticker_absent_vetoes_with_not_in_tier3() -> None:
    stream_redis = FakeStreamRedis()
    pool = FakeTierPool()  # no rows: research.universe_tiers has no entry
    policy = RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=1)

    result = await process_intent_event(
        stream_redis, _intent_event(), policy, tier3_gate=_gate(pool)
    )

    assert result.stream == "risk.intent.vetoed"
    payload = _last_payload(stream_redis)
    assert payload["approved"] is False
    assert payload["reason_code"] == TICKER_NOT_IN_TIER3
    assert payload["reason"] == TICKER_NOT_IN_TIER3
    assert "approved_intent_payload" not in payload


@pytest.mark.asyncio
async def test_query_error_fails_closed_with_state_unavailable() -> None:
    """Table missing / Postgres unreachable => veto, never approve."""

    stream_redis = FakeStreamRedis()
    pool = FakeTierPool()
    pool.conn.rows["AAPL"] = {"tier": "active"}
    pool.conn.raise_error = RuntimeError('relation "research.universe_tiers" does not exist')
    policy = RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=1)

    result = await process_intent_event(
        stream_redis, _intent_event(), policy, tier3_gate=_gate(pool)
    )

    assert result.stream == "risk.intent.vetoed"
    payload = _last_payload(stream_redis)
    assert payload["reason_code"] == TIER3_STATE_UNAVAILABLE
    assert "approved_intent_payload" not in payload


@pytest.mark.asyncio
async def test_unavailable_outcome_is_not_cached_so_recovery_is_rechecked() -> None:
    pool = FakeTierPool()
    pool.conn.rows["AAPL"] = {"tier": "active"}
    pool.conn.raise_error = RuntimeError("postgres unreachable")
    gate = _gate(pool)

    assert await gate.check("AAPL") == TIER3_STATE_UNAVAILABLE

    pool.conn.raise_error = None
    assert await gate.check("AAPL") is None
    assert len(pool.conn.queries) == 2


@pytest.mark.asyncio
async def test_cache_ttl_honored_second_check_within_ttl_emits_no_second_query() -> None:
    pool = FakeTierPool()
    pool.conn.rows["AAPL"] = {"tier": "active"}
    clock = FakeClock()
    gate = _gate(pool, ttl=30.0, clock=clock)

    assert await gate.check("AAPL") is None
    clock.now += 29.0
    assert await gate.check("AAPL") is None
    assert len(pool.conn.queries) == 1

    # Past the TTL the state is re-read — an eviction lands within one TTL.
    clock.now += 2.0
    del pool.conn.rows["AAPL"]
    assert await gate.check("AAPL") == TICKER_NOT_IN_TIER3
    assert len(pool.conn.queries) == 2


@pytest.mark.asyncio
async def test_negative_membership_is_cached_within_ttl_too() -> None:
    pool = FakeTierPool()
    clock = FakeClock()
    gate = _gate(pool, ttl=30.0, clock=clock)

    assert await gate.check("RKLB") == TICKER_NOT_IN_TIER3
    clock.now += 5.0
    assert await gate.check("RKLB") == TICKER_NOT_IN_TIER3
    assert len(pool.conn.queries) == 1


@pytest.mark.asyncio
async def test_expired_tier2_row_does_not_count_as_tier3() -> None:
    """A watch row is not tradeable regardless of its expiry state."""

    pool = FakeTierPool()
    pool.conn.rows["RKLB"] = {
        "tier": "watch",
        "expiry": datetime(2026, 1, 1, tzinfo=UTC),  # long expired
    }
    gate = _gate(pool)

    assert await gate.check("RKLB") == TICKER_NOT_IN_TIER3


@pytest.mark.asyncio
async def test_gate_normalizes_ticker_case() -> None:
    pool = FakeTierPool()
    pool.conn.rows["AAPL"] = {"tier": "active"}
    gate = _gate(pool)

    assert await gate.check(" aapl ") is None
    assert pool.conn.queries[0][1] == ("AAPL",)


@pytest.mark.asyncio
async def test_policy_vetoed_intent_never_consults_tier3_state() -> None:
    stream_redis = FakeStreamRedis()
    pool = FakeTierPool()
    policy = RiskPolicy(allowed_universe={"NVDA"}, max_quantity_per_order=1)  # AAPL not allowed

    result = await process_intent_event(
        stream_redis, _intent_event("AAPL"), policy, tier3_gate=_gate(pool)
    )

    assert result.stream == "risk.intent.vetoed"
    assert pool.conn.queries == []


@pytest.mark.asyncio
async def test_tier3_vetoed_intent_consumes_no_rate_slot() -> None:
    """Tier 3 runs before the rate guardrail: a non-tradeable name burns no slot."""

    stream_redis = FakeStreamRedis()
    pool = FakeTierPool()  # AAPL not in Tier 3
    rate_redis = FakeRateRedis()
    limiter = RedisRateLimiter(
        rate_redis, RateLimitConfig(max_orders_per_day=100, symbol_cooldown_seconds=300)
    )
    policy = RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=1)

    result = await process_intent_event(
        stream_redis, _intent_event(), policy, rate_limiter=limiter, tier3_gate=_gate(pool)
    )

    assert result.stream == "risk.intent.vetoed"
    assert _last_payload(stream_redis)["reason_code"] == TICKER_NOT_IN_TIER3
    assert rate_redis.counters == {}
    assert rate_redis.flags == {}


def test_settings_expose_tier3_config(monkeypatch: Any) -> None:
    from shrap.agents.risk_compliance.pre_trade_checker.config import Settings

    monkeypatch.setenv("PRE_TRADE_CHECKER_TIER3_ENFORCEMENT", "true")
    monkeypatch.setenv("PRE_TRADE_CHECKER_POSTGRES_DSN", "postgresql://u:secret@host:5432/db")
    monkeypatch.setenv("PRE_TRADE_CHECKER_TIER3_CACHE_TTL_SECONDS", "10.0")

    settings = Settings()
    assert settings.tier3_enforcement is True
    assert settings.postgres_dsn_value() == "postgresql://u:secret@host:5432/db"
    assert settings.tier3_cache_ttl_seconds == 10.0

    redacted = settings.redacted()
    assert redacted["tier3_enforcement"] is True
    assert redacted["postgres_dsn"] == "***"
    assert redacted["tier3_cache_ttl_seconds"] == 10.0
    assert "secret" not in str(redacted)


def test_tier3_enforcement_defaults_off() -> None:
    from shrap.agents.risk_compliance.pre_trade_checker.config import Settings

    assert Settings().tier3_enforcement is False


def test_compose_wires_tier3_env() -> None:
    from pathlib import Path

    compose = Path("infra/docker-compose.yml").read_text()
    assert "PRE_TRADE_CHECKER_TIER3_ENFORCEMENT" in compose
    assert "PRE_TRADE_CHECKER_POSTGRES_DSN" in compose
    assert "PRE_TRADE_CHECKER_TIER3_CACHE_TTL_SECONDS" in compose
