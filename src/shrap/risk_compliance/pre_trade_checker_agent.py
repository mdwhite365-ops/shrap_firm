"""Event-loop wrapper for the deterministic pre-trade checker.

Reads ``trading.decision.intent`` through a Redis consumer group (KI-006):
offsets persist in Redis, so a restart resumes where the group left off
instead of replaying stream history. ``start_id`` only positions the group
the first time it is created.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any, Protocol, cast

import structlog
from redis.asyncio import Redis

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.events import EventPublisher, PublishedEvent, ReceivedEvent
from shrap.events.groups import GroupEventSubscriber, RedisGroupClient
from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy
from shrap.risk_compliance.rate_limit import RateLimitConfig, RateLimitRedis, RedisRateLimiter
from shrap.risk_compliance.tier3_membership import Tier3MembershipGate

log = structlog.get_logger(__name__)

STREAM_DECISION_INTENT = "trading.decision.intent"
STREAM_RISK_APPROVED = "risk.intent.approved"
STREAM_RISK_VETOED = "risk.intent.vetoed"
PRODUCED_BY = "risk/pre-trade-checker"
SCHEMA_VERSION = "1.0.0"
CONSUMER_GROUP = "pre-trade-checker"


class RedisStreamClient(Protocol):
    async def xadd(self, stream: str, fields: dict[str, str]) -> str: ...

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",
        mkstream: bool = False,
    ) -> Any: ...

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[Any, Any],
        count: int | None = None,
        block: int | None = None,
    ) -> Any: ...

    async def xack(self, name: str, groupname: str, *ids: str) -> Any: ...


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass


def build_risk_decision_payload(event: ReceivedEvent, policy: RiskPolicy) -> dict[str, Any]:
    """Build a deterministic risk decision payload for one intent event."""

    intent_payload = event.envelope.payload
    if intent_payload is None:
        raise ValueError("trading.decision.intent must carry an inline payload")

    decision = PreTradeChecker(policy).check(intent_payload)
    payload = decision.to_event_payload()
    payload["intent_event_id"] = event.envelope.event_id
    payload["intent_stream"] = event.stream
    payload["intent_redis_stream_id"] = event.redis_stream_id
    payload["intent_payload"] = intent_payload
    payload["reason"] = decision.reason_code
    payload["strategy_ids"] = intent_payload.get("strategy_ids", [])
    if decision.approved:
        approved_intent = dict(intent_payload)
        approved_intent["quantity"] = decision.approved_quantity
        payload["approved_intent_payload"] = approved_intent
    return payload


def _downgrade_to_veto(decision_payload: dict[str, Any], reason_code: str, note: str) -> None:
    """Flip an already-approved decision to a veto with ``reason_code``.

    Shared by the stateful gates (Tier 3 membership, rate guardrails) that run
    after the pure policy check and can only ever tighten an approval.
    """

    decision_payload["approved"] = False
    decision_payload["reason_code"] = reason_code
    decision_payload["reason"] = reason_code
    decision_payload["approved_quantity"] = 0
    decision_payload.pop("approved_intent_payload", None)
    reasons = decision_payload.get("reasons")
    if isinstance(reasons, list):
        reasons.append(note)


async def process_intent_event(
    redis: RedisStreamClient,
    event: ReceivedEvent,
    policy: RiskPolicy,
    produced_by: str = PRODUCED_BY,
    rate_limiter: RedisRateLimiter | None = None,
    tier3_gate: Tier3MembershipGate | None = None,
) -> PublishedEvent:
    """Run the pure pre-trade check (plus stateful gates) and publish the result.

    The stateful gates apply after the deterministic policy check and only to
    already-approved intents: an intent the policy would veto never consults
    Tier 3 state or consumes a rate slot. Tier 3 membership is checked before
    the rate guardrail so a non-tradeable ticker never claims a rate slot.
    """

    decision_payload = build_risk_decision_payload(event, policy)
    if decision_payload["approved"] and tier3_gate is not None:
        ticker = str(decision_payload.get("ticker", ""))
        tier3_veto = await tier3_gate.check(ticker)
        if tier3_veto is not None:
            _downgrade_to_veto(decision_payload, tier3_veto, f"tier-3 gate: {tier3_veto}")
            log.warning(
                "pre_trade_checker.tier3_vetoed",
                intent_event_id=event.envelope.event_id,
                ticker=ticker,
                reason=tier3_veto,
            )
    if decision_payload["approved"] and rate_limiter is not None:
        rate_veto = await rate_limiter.acquire(str(decision_payload.get("ticker", "")))
        if rate_veto is not None:
            _downgrade_to_veto(decision_payload, rate_veto, f"rate guardrail: {rate_veto}")
            log.warning(
                "pre_trade_checker.rate_vetoed",
                intent_event_id=event.envelope.event_id,
                ticker=decision_payload.get("ticker"),
                reason=rate_veto,
            )
    stream = STREAM_RISK_APPROVED if decision_payload["approved"] else STREAM_RISK_VETOED
    return await EventPublisher(redis).publish(
        stream=stream,
        produced_by=produced_by,
        schema_version=SCHEMA_VERSION,
        payload=decision_payload,
        correlation_id=event.envelope.event_id,
    )


async def poll_once(
    redis: RedisStreamClient,
    policy: RiskPolicy,
    subscriber: GroupEventSubscriber,
    count: int,
    block_ms: int,
    rate_limiter: RedisRateLimiter | None = None,
    tier3_gate: Tier3MembershipGate | None = None,
    retry_delay_seconds: float = 0.0,
) -> int:
    """Read one batch of decision intents and publish risk decisions.

    Successful and permanently-invalid events are acknowledged; a systemic
    failure leaves the event pending so the group redelivers it first on the
    next cycle, after ``retry_delay_seconds``.
    """

    try:
        events = await subscriber.read(
            streams=[STREAM_DECISION_INTENT], count=count, block_ms=block_ms
        )
    except Exception:
        log.exception("pre_trade_checker.read_failed", group=subscriber.group)
        await asyncio.sleep(retry_delay_seconds)
        return 0
    processed = 0
    for event in events:
        try:
            result = await process_intent_event(
                redis, event, policy, rate_limiter=rate_limiter, tier3_gate=tier3_gate
            )
            await subscriber.ack(event)
            processed += 1
            log.info(
                "pre_trade_checker.decision_published",
                intent_event_id=event.envelope.event_id,
                stream=result.stream,
                risk_event_id=result.envelope.event_id,
            )
        except ValueError:
            # Malformed intent: permanent for this event. Ack and skip it or
            # the gate stalls forever on a poison message.
            log.exception(
                "pre_trade_checker.intent_invalid_skipped",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                intent_event_id=event.envelope.event_id,
            )
            await subscriber.ack(event)
            continue
        except Exception:
            # Systemic error: do NOT ack, so the same event retries next cycle.
            log.exception(
                "pre_trade_checker.intent_failed",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                intent_event_id=event.envelope.event_id,
            )
            await asyncio.sleep(retry_delay_seconds)
            break
    return processed


async def run_loop(
    redis: RedisStreamClient,
    policy: RiskPolicy,
    stop: asyncio.Event,
    start_id: str = "0-0",
    count: int = 100,
    block_ms: int = 5000,
    retry_delay_seconds: float = 1.0,
    rate_limiter: RedisRateLimiter | None = None,
    tier3_gate: Tier3MembershipGate | None = None,
    group: str = CONSUMER_GROUP,
    consumer: str | None = None,
) -> None:
    """Run the pre-trade checker loop until ``stop`` is set.

    Offsets persist in the ``group`` consumer group (KI-006); ``start_id``
    only positions the group the first time it is created on the stream. The
    Redis-backed rate limiter remains the guard against re-approving intents
    if the group is ever recreated: replayed approvals hit the cooldown/daily
    cap instead of minting fresh orders.
    """

    subscriber = GroupEventSubscriber(
        cast(RedisGroupClient, redis),
        group=group,
        consumer=consumer,
        start_id=start_id,
    )
    while not stop.is_set():
        try:
            processed = await poll_once(
                redis=redis,
                policy=policy,
                subscriber=subscriber,
                count=count,
                block_ms=block_ms,
                rate_limiter=rate_limiter,
                tier3_gate=tier3_gate,
                retry_delay_seconds=retry_delay_seconds,
            )
            if processed:
                log.info("pre_trade_checker.batch", processed=processed, group=group)
            else:
                await asyncio.sleep(0)
        except Exception:
            log.exception("pre_trade_checker.poll_failed")
            await asyncio.sleep(retry_delay_seconds)


async def run(
    redis_url: str,
    policy: RiskPolicy,
    service_name: str = "risk/pre-trade-checker",
    log_level: str = "INFO",
    start_id: str = "0-0",
    count: int = 100,
    block_ms: int = 5000,
    retry_delay_seconds: float = 1.0,
    rate_limit_config: RateLimitConfig | None = None,
    tier3_enforcement: bool = False,
    postgres_dsn: str = "",
    tier3_cache_ttl_seconds: float = 30.0,
    group: str = CONSUMER_GROUP,
    consumer: str | None = None,
) -> None:
    """Run the Pre-Trade Checker service until SIGINT/SIGTERM."""

    configure_logging(service_name, log_level)
    log.info(
        "pre_trade_checker.starting",
        redis_url=redis_url,
        start_id=start_id,
        count=count,
        block_ms=block_ms,
        group=group,
        consumer=consumer or group,
        tier3_enforcement=tier3_enforcement,
        rate_limit=(
            {
                "max_orders_per_day": rate_limit_config.max_orders_per_day,
                "symbol_cooldown_seconds": rate_limit_config.symbol_cooldown_seconds,
            }
            if rate_limit_config
            else None
        ),
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    redis: Redis = Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_timeout=(block_ms / 1000) + 10,
    )
    rate_limiter = (
        RedisRateLimiter(cast(RateLimitRedis, redis), rate_limit_config)
        if rate_limit_config
        else None
    )
    # Tier 3 enforcement is opt-in (ADR-0012). When off, the rule is skipped
    # entirely and no Postgres connection is opened — the permissive default is
    # an explicit human choice, logged once here. When on, the gate fails closed
    # on any Tier 3 state error (see tier3_membership.Tier3MembershipGate).
    pool: Any = None
    tier3_gate: Tier3MembershipGate | None = None
    if tier3_enforcement:
        log.info(
            "pre_trade_checker.tier3_enforcement_on",
            cache_ttl_seconds=tier3_cache_ttl_seconds,
            note="rejecting any ticker not currently in Tier 3; unavailable state fails closed",
        )
        pool = await create_asyncpg_pool(postgres_dsn)
        tier3_gate = Tier3MembershipGate(pool, ttl_seconds=tier3_cache_ttl_seconds)
    else:
        log.info(
            "pre_trade_checker.tier3_enforcement_off",
            note="Tier 3 membership filter disabled by config; no tier-membership vetoes",
        )
    try:
        await run_loop(
            cast(RedisStreamClient, redis),
            policy=policy,
            stop=stop,
            start_id=start_id,
            count=count,
            block_ms=block_ms,
            retry_delay_seconds=retry_delay_seconds,
            rate_limiter=rate_limiter,
            tier3_gate=tier3_gate,
            group=group,
            consumer=consumer,
        )
    finally:
        await redis.aclose()
        if pool is not None:
            await pool.close()
        log.info("pre_trade_checker.stopped")


__all__ = [
    "CONSUMER_GROUP",
    "PRODUCED_BY",
    "SCHEMA_VERSION",
    "STREAM_DECISION_INTENT",
    "STREAM_RISK_APPROVED",
    "STREAM_RISK_VETOED",
    "build_risk_decision_payload",
    "poll_once",
    "process_intent_event",
    "run",
    "run_loop",
]
