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

    assert await limiter.acquire("AAPL", account_id="ACC1") is None
    assert await limiter.acquire("aapl", account_id="ACC1") == SYMBOL_COOLDOWN_REASON
    assert await limiter.acquire("NVDA", account_id="ACC1") is None
    assert redis.expires["risk:cooldown:ACC1:AAPL"] == 300


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


# ---------------------------------------------------------------------------
# Account scoping and slot-aware dedup (2026-09-18).
#
# Every test below reproduces something that happened, or something that would
# have happened the first time an intraday strategy traded. None of these fail
# loudly in production: a rate veto is a normal, logged outcome, so a wrongly
# vetoed stop-loss looks exactly like a correctly vetoed duplicate.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_accounts_cooldown_does_not_block_another() -> None:
    """The 2026-09-18 defect, in miniature.

    A ``U`` intent on PA3KQN57WVXY claimed the global ``risk:cooldown:U`` key.
    Four minutes later the firm's first stop-loss fired on ``U`` on
    PA3HEG2CLXLU, hit that key, and was vetoed. It executed 15 minutes late,
    at -10.47% instead of -10.05%. Three accounts are three books; one book's
    traffic must not gate another's risk control.
    """

    redis = FakeRateRedis()
    limiter = RedisRateLimiter(redis, RateLimitConfig(max_orders_per_day=100))

    assert await limiter.acquire("U", account_id="PA3KQN57WVXY") is None
    assert await limiter.acquire("U", account_id="PA3HEG2CLXLU") is None
    # ...and each account still deduplicates against itself.
    assert await limiter.acquire("U", account_id="PA3HEG2CLXLU") == SYMBOL_COOLDOWN_REASON


@pytest.mark.asyncio
async def test_one_accounts_daily_cap_does_not_spend_anothers() -> None:
    redis = FakeRateRedis()
    limiter = RedisRateLimiter(redis, RateLimitConfig(max_orders_per_day=2))

    assert await limiter.acquire("AAPL", account_id="ACC1") is None
    assert await limiter.acquire("NVDA", account_id="ACC1") is None
    assert await limiter.acquire("TSLA", account_id="ACC1") == DAILY_CAP_REASON
    # ACC2 has spent nothing.
    assert await limiter.acquire("TSLA", account_id="ACC2") is None


@pytest.mark.asyncio
async def test_a_new_slot_releases_the_symbol() -> None:
    """The intraday case the fixed 300s window would have silently vetoed.

    A five-minute strategy re-decides every five minutes, and a 300s cooldown
    is the same number — so roughly every other decision would have come back
    SYMBOL_COOLDOWN_ACTIVE depending on which side of the window it landed.
    Keyed on the slot, each decision point gets exactly one order.
    """

    redis = FakeRateRedis()
    limiter = RedisRateLimiter(redis, RateLimitConfig(max_orders_per_day=100))

    assert await limiter.acquire("AAPL", account_id="ACC1", slot="09:45") is None
    assert await limiter.acquire("AAPL", account_id="ACC1", slot="09:45") == SYMBOL_COOLDOWN_REASON
    assert await limiter.acquire("AAPL", account_id="ACC1", slot="09:50") is None


@pytest.mark.asyncio
async def test_a_daily_slot_allows_one_order_per_session() -> None:
    redis = FakeRateRedis()
    limiter = RedisRateLimiter(redis, RateLimitConfig(max_orders_per_day=100))

    assert await limiter.acquire("AAPL", account_id="ACC1", slot="session") is None
    assert (
        await limiter.acquire("AAPL", account_id="ACC1", slot="session") == SYMBOL_COOLDOWN_REASON
    )


@pytest.mark.asyncio
async def test_a_slot_key_outlives_its_slot() -> None:
    """A TTL shorter than the slot would let the same decision through twice.

    The time-boxed window is 300s; a 15-minute slot is 900. So the slot key
    cannot borrow the cooldown's TTL — it takes the day-length one, and the
    date in the key is what stops today's 09:45 silencing tomorrow's.
    """

    redis = FakeRateRedis()
    limiter = RedisRateLimiter(
        redis, RateLimitConfig(max_orders_per_day=100, symbol_cooldown_seconds=300)
    )
    await limiter.acquire("AAPL", account_id="ACC1", slot="09:45")

    key = next(k for k in redis.expires if "09:45" in k)
    assert redis.expires[key] > 15 * 60
    assert limiter._today() in key


@pytest.mark.asyncio
async def test_an_exit_without_a_slot_keeps_the_time_window() -> None:
    """Exits answer the book, not a schedule, so they have no slot to key on.

    The time window stays their cross-restart backstop — the Runner's own
    suppression is in-process and lost on restart.
    """

    redis = FakeRateRedis()
    limiter = RedisRateLimiter(
        redis, RateLimitConfig(max_orders_per_day=100, symbol_cooldown_seconds=300)
    )

    assert await limiter.acquire("AAPL", account_id="ACC1") is None
    assert await limiter.acquire("AAPL", account_id="ACC1") == SYMBOL_COOLDOWN_REASON
    assert redis.expires["risk:cooldown:ACC1:AAPL"] == 300


@pytest.mark.asyncio
async def test_an_unnamed_account_gets_its_own_bucket() -> None:
    """Never a shared key: an unattributed intent must not gate a real book."""

    redis = FakeRateRedis()
    limiter = RedisRateLimiter(redis, RateLimitConfig(max_orders_per_day=100))

    assert await limiter.acquire("AAPL", account_id="") is None
    assert await limiter.acquire("AAPL", account_id=None) == SYMBOL_COOLDOWN_REASON
    assert await limiter.acquire("AAPL", account_id="ACC1") is None
    assert "risk:cooldown:unattributed:AAPL" in redis.flags


# ---------------------------------------------------------------------------
# Gate ordering: the portfolio gate runs BEFORE the rate guardrail.
# ---------------------------------------------------------------------------


class _VetoingOfficer:
    """A Risk Officer that refuses everything, the way BELOW_BROKER_MINIMUM does."""

    class _Store:
        async def record_decision(self, row: Any) -> None:
            return None

    def __init__(self) -> None:
        self.store = self._Store()

    async def assess(self, **kwargs: Any) -> Any:
        from shrap.risk_compliance.risk_officer.officer import RiskAssessment

        return RiskAssessment(
            approved=False,
            approved_quantity=0.0,
            reason_code="BELOW_BROKER_MINIMUM",
            notes=["below the broker minimum"],
            account_id="PA3KQN57WVXY",
        )


@pytest.mark.asyncio
async def test_a_portfolio_vetoed_intent_consumes_no_rate_slot() -> None:
    """The 2026-09-18 defect at its root, and the reason the gates were reordered.

    A ``U`` sell of 0.0126 shares passed the deterministic policy check, claimed
    ``risk:cooldown:U``, and was vetoed ``BELOW_BROKER_MINIMUM`` one line later.
    No order was ever sent, and the key was held for 300 seconds anyway — long
    enough to veto the firm's first stop-loss. A slot is consumed by an *order*,
    so it must not be claimed while a gate that can still refuse the order has
    not run.
    """

    stream_redis = FakeStreamRedis()
    rate_redis = FakeRateRedis()
    limiter = RedisRateLimiter(
        rate_redis, RateLimitConfig(max_orders_per_day=100, symbol_cooldown_seconds=300)
    )
    policy = RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=1)

    result = await process_intent_event(
        stream_redis,
        _intent_event("AAPL"),
        policy,
        rate_limiter=limiter,
        officer=_VetoingOfficer(),  # type: ignore[arg-type]
    )

    assert result.stream == "risk.intent.vetoed"
    # The whole point: nothing was spent on an order that never existed.
    assert rate_redis.flags == {}
    assert rate_redis.counters == {}


@pytest.mark.asyncio
async def test_a_portfolio_veto_leaves_the_symbol_free_for_another_intent() -> None:
    """The consequence, stated as behaviour rather than as key hygiene."""

    stream_redis = FakeStreamRedis()
    rate_redis = FakeRateRedis()
    limiter = RedisRateLimiter(
        rate_redis, RateLimitConfig(max_orders_per_day=100, symbol_cooldown_seconds=300)
    )
    policy = RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=1)

    await process_intent_event(
        stream_redis,
        _intent_event("AAPL"),
        policy,
        rate_limiter=limiter,
        officer=_VetoingOfficer(),  # type: ignore[arg-type]
    )
    # The next intent for the same symbol — the stop-loss, in the real incident.
    second = await process_intent_event(
        stream_redis, _intent_event("AAPL"), policy, rate_limiter=limiter
    )

    assert second.stream == "risk.intent.approved"


# ---------------------------------------------------------------------------
# The slot has to survive the whole path, not just the limiter.
# ---------------------------------------------------------------------------


def test_the_decision_maker_carries_the_slot_through() -> None:
    """The Decision Maker rebuilds the intent from an allowlist, so fields drop.

    Two fields have already been lost this way — `strategy_ids` (20 signals, 20
    vetoes, zero orders on 2026-08-03) and `size_hint` (narrowed to int, which
    truncated every fractional quantity). Testing the rate limiter alone would
    not have caught a third: the limiter would be correct, the Runner would be
    correct, and the slot would simply never arrive.
    """

    from shrap.trading_floor.decision_maker_stub import build_stub_intent

    signal = {
        "strategy_id": "01TEST",
        "account_id": "PA3YPMG9AD4Z",
        "ticker": "AAPL",
        "side": "buy",
        "size_hint": 1.5,
        "quantity": 1.5,
        "confidence": 0.9,
        "urgency": "normal",
        "slot": "09:45",
        "regime_label": "risk-on",
        "justification_text": "test",
    }
    assert build_stub_intent(signal, threshold=0.7)["slot"] == "09:45"


def test_an_exit_intent_carries_no_slot_rather_than_an_empty_one() -> None:
    """Absence must stay absence: the checker reads it as "not scheduled"."""

    from shrap.trading_floor.decision_maker_stub import build_stub_intent

    signal = {
        "strategy_id": "01TEST",
        "account_id": "PA3YPMG9AD4Z",
        "ticker": "AAPL",
        "side": "sell",
        "size_hint": 1.5,
        "quantity": 1.5,
        "confidence": 0.9,
        "justification_text": "stop-loss",
    }
    assert "slot" not in build_stub_intent(signal, threshold=0.7)


def test_the_runner_stamps_its_slot_on_every_entry_signal() -> None:
    """The producer end of the same path."""

    from shrap.research.strategy_runner.engine import (
        RunnerSignalConfig,
        build_payload,
    )

    payload = build_payload(
        strategy_id="01TEST",
        ticker="AAPL",
        side="buy",
        quantity=1.0,
        account_id="PA3YPMG9AD4Z",
        config=RunnerSignalConfig(),
        regime_label="risk-on",
        justification="test",
        slot="09:45",
    )
    assert payload["slot"] == "09:45"
