"""Tests for the Redis-backed order-rate guardrails."""

from __future__ import annotations

from typing import Any

import pytest

from shrap.events import Envelope, ReceivedEvent, normalize_redis_fields
from shrap.risk_compliance.pre_trade import RiskPolicy
from shrap.risk_compliance.pre_trade_checker_agent import process_intent_event
from shrap.risk_compliance.rate_limit import (
    DAILY_CAP_REASON,
    SYMBOL_COOLDOWN_REASON,
    RateLimitConfig,
    RedisRateLimiter,
)


class FakeRateRedis:
    """Counter/flag fake for INCR/EXPIRE/SET-NX-EX."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.flags: dict[str, str] = {}
        self.expires: dict[str, int] = {}

    async def incr(self, name: str) -> int:
        self.counters[name] = self.counters.get(name, 0) + 1
        return self.counters[name]

    async def expire(self, name: str, time: int) -> bool:
        self.expires[name] = time
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
        if ex is not None:
            self.expires[name] = ex
        return True


@pytest.mark.asyncio
async def test_daily_cap_vetoes_after_limit() -> None:
    redis = FakeRateRedis()
    limiter = RedisRateLimiter(redis, RateLimitConfig(max_orders_per_day=2))

    assert await limiter.acquire("AAPL") is None
    assert await limiter.acquire("NVDA") is None
    assert await limiter.acquire("TSLA") == DAILY_CAP_REASON
    # Attempts keep counting for the audit trail.
    assert await limiter.acquire("SPY") == DAILY_CAP_REASON


@pytest.mark.asyncio
async def test_symbol_cooldown_vetoes_repeat_symbol_but_not_others() -> None:
    redis = FakeRateRedis()
    limiter = RedisRateLimiter(
        redis, RateLimitConfig(max_orders_per_day=100, symbol_cooldown_seconds=300)
    )

    assert await limiter.acquire("AAPL") is None
    assert await limiter.acquire("aapl") == SYMBOL_COOLDOWN_REASON  # case-insensitive
    assert await limiter.acquire("NVDA") is None
    assert redis.expires["risk:cooldown:AAPL"] == 300


@pytest.mark.asyncio
async def test_zero_values_disable_the_limits() -> None:
    redis = FakeRateRedis()
    limiter = RedisRateLimiter(
        redis, RateLimitConfig(max_orders_per_day=0, symbol_cooldown_seconds=0)
    )

    for _ in range(5):
        assert await limiter.acquire("AAPL") is None
    assert redis.counters == {}
    assert redis.flags == {}


class FakeStreamRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        return f"178012860000{len(self.calls)}-0"

    async def xread(
        self, streams: dict[Any, Any], count: int | None = None, block: int | None = None
    ) -> Any:
        return []


def _intent_event(ticker: str = "AAPL") -> ReceivedEvent:
    from datetime import UTC, datetime

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


@pytest.mark.asyncio
async def test_rate_vetoed_intent_publishes_to_veto_stream_with_reason() -> None:
    stream_redis = FakeStreamRedis()
    rate_redis = FakeRateRedis()
    limiter = RedisRateLimiter(
        rate_redis, RateLimitConfig(max_orders_per_day=100, symbol_cooldown_seconds=300)
    )
    policy = RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=1)

    first = await process_intent_event(stream_redis, _intent_event(), policy, rate_limiter=limiter)
    assert first.stream == "risk.intent.approved"

    second = await process_intent_event(stream_redis, _intent_event(), policy, rate_limiter=limiter)
    assert second.stream == "risk.intent.vetoed"
    envelope = Envelope.from_redis_fields(normalize_redis_fields(stream_redis.calls[-1][1]))
    assert envelope.payload is not None
    assert envelope.payload["approved"] is False
    assert envelope.payload["reason_code"] == SYMBOL_COOLDOWN_REASON
    assert "approved_intent_payload" not in envelope.payload


@pytest.mark.asyncio
async def test_policy_vetoed_intent_consumes_no_rate_slot() -> None:
    stream_redis = FakeStreamRedis()
    rate_redis = FakeRateRedis()
    limiter = RedisRateLimiter(
        rate_redis, RateLimitConfig(max_orders_per_day=100, symbol_cooldown_seconds=300)
    )
    policy = RiskPolicy(allowed_universe={"NVDA"}, max_quantity_per_order=1)  # AAPL not allowed

    result = await process_intent_event(
        stream_redis, _intent_event("AAPL"), policy, rate_limiter=limiter
    )

    assert result.stream == "risk.intent.vetoed"
    assert rate_redis.counters == {}
    assert rate_redis.flags == {}


def test_settings_expose_rate_limit_config(monkeypatch: Any) -> None:
    from shrap.agents.risk_compliance.pre_trade_checker.config import Settings

    monkeypatch.setenv("PRE_TRADE_CHECKER_MAX_ORDERS_PER_DAY", "5")
    monkeypatch.setenv("PRE_TRADE_CHECKER_SYMBOL_COOLDOWN_SECONDS", "120")

    config = Settings().rate_limit_config()
    assert config.max_orders_per_day == 5
    assert config.symbol_cooldown_seconds == 120

    redacted = Settings().redacted()
    assert redacted["max_orders_per_day"] == 5
    assert redacted["symbol_cooldown_seconds"] == 120


def test_compose_wires_rate_limit_env() -> None:
    from pathlib import Path

    compose = Path("infra/docker-compose.yml").read_text()
    assert "PRE_TRADE_CHECKER_MAX_ORDERS_PER_DAY" in compose
    assert "PRE_TRADE_CHECKER_SYMBOL_COOLDOWN_SECONDS" in compose
