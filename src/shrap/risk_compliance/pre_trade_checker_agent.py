"""Event-loop wrapper for the deterministic pre-trade checker.

Month 1 deliberately starts from ``0-0`` so the risk gate replays queued
``trading.decision.intent`` events after startup/recovery. Consumer groups with
explicit XACKs are the post-sprint upgrade.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any, Protocol, cast

import structlog
from redis.asyncio import Redis

from shrap.common.logging import configure_logging
from shrap.events import EventPublisher, EventSubscriber, PublishedEvent, ReceivedEvent
from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy
from shrap.risk_compliance.rate_limit import RateLimitConfig, RateLimitRedis, RedisRateLimiter

log = structlog.get_logger(__name__)

STREAM_DECISION_INTENT = "trading.decision.intent"
STREAM_RISK_APPROVED = "risk.intent.approved"
STREAM_RISK_VETOED = "risk.intent.vetoed"
PRODUCED_BY = "risk/pre-trade-checker"
SCHEMA_VERSION = "1.0.0"


class RedisStreamClient(Protocol):
    async def xadd(self, stream: str, fields: dict[str, str]) -> str: ...

    async def xread(
        self,
        streams: dict[Any, Any],
        count: int | None = None,
        block: int | None = None,
    ) -> Any: ...


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


async def process_intent_event(
    redis: RedisStreamClient,
    event: ReceivedEvent,
    policy: RiskPolicy,
    produced_by: str = PRODUCED_BY,
    rate_limiter: RedisRateLimiter | None = None,
) -> PublishedEvent:
    """Run the pure pre-trade check (plus rate guardrails) and publish the result.

    Rate limits apply after the deterministic policy check and only to
    already-approved intents: an intent the policy would veto never consumes
    a rate slot.
    """

    decision_payload = build_risk_decision_payload(event, policy)
    if decision_payload["approved"] and rate_limiter is not None:
        rate_veto = await rate_limiter.acquire(str(decision_payload.get("ticker", "")))
        if rate_veto is not None:
            decision_payload["approved"] = False
            decision_payload["reason_code"] = rate_veto
            decision_payload["reason"] = rate_veto
            decision_payload["approved_quantity"] = 0
            decision_payload.pop("approved_intent_payload", None)
            reasons = decision_payload.get("reasons")
            if isinstance(reasons, list):
                reasons.append(f"rate guardrail: {rate_veto}")
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
    last_ids: dict[str, str],
    start_id: str,
    count: int,
    block_ms: int,
    rate_limiter: RedisRateLimiter | None = None,
) -> int:
    """Read one batch of decision intents and publish risk decisions."""

    last_ids.setdefault(STREAM_DECISION_INTENT, start_id)
    subscriber = EventSubscriber(redis)
    try:
        events = await subscriber.read(streams=last_ids, count=count, block_ms=block_ms)
    except Exception:
        log.exception("pre_trade_checker.read_failed", streams=dict(last_ids))
        return 0
    processed = 0
    for event in events:
        try:
            result = await process_intent_event(redis, event, policy, rate_limiter=rate_limiter)
            last_ids[event.stream] = event.redis_stream_id
            processed += 1
            log.info(
                "pre_trade_checker.decision_published",
                intent_event_id=event.envelope.event_id,
                stream=result.stream,
                risk_event_id=result.envelope.event_id,
            )
        except Exception:
            log.exception(
                "pre_trade_checker.intent_failed",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                intent_event_id=event.envelope.event_id,
            )
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
) -> None:
    """Run the pre-trade checker loop until ``stop`` is set.

    The Month 1 default ``start_id='0-0'`` intentionally replays queued
    intents on startup; Redis consumer groups with explicit acknowledgments are
    deferred to a future card. The Redis-backed rate limiter is the guard that
    makes that replay safe: already-approved intents hit the cooldown/daily
    cap instead of minting fresh orders.
    """

    last_ids: dict[str, str] = {}
    while not stop.is_set():
        try:
            processed = await poll_once(
                redis=redis,
                policy=policy,
                last_ids=last_ids,
                start_id=start_id,
                count=count,
                block_ms=block_ms,
                rate_limiter=rate_limiter,
            )
            if processed:
                log.info("pre_trade_checker.batch", processed=processed, last_ids=dict(last_ids))
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
) -> None:
    """Run the Pre-Trade Checker service until SIGINT/SIGTERM."""

    configure_logging(service_name, log_level)
    log.info(
        "pre_trade_checker.starting",
        redis_url=redis_url,
        start_id=start_id,
        count=count,
        block_ms=block_ms,
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
        )
    finally:
        await redis.aclose()
        log.info("pre_trade_checker.stopped")


__all__ = [
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
