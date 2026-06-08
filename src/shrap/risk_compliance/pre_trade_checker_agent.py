"""Event-loop wrapper for the deterministic pre-trade checker."""

from __future__ import annotations

import asyncio
import signal
from typing import Any, Protocol, cast

import structlog
from redis.asyncio import Redis

from shrap.common.logging import configure_logging
from shrap.events import EventPublisher, EventSubscriber, PublishedEvent, ReceivedEvent
from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy

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
) -> PublishedEvent:
    """Run the pure pre-trade check and publish the risk result event."""

    decision_payload = build_risk_decision_payload(event, policy)
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
) -> int:
    """Read one batch of decision intents and publish risk decisions."""

    last_ids.setdefault(STREAM_DECISION_INTENT, start_id)
    subscriber = EventSubscriber(redis)
    events = await subscriber.read(streams=last_ids, count=count, block_ms=block_ms)
    processed = 0
    for event in events:
        try:
            result = await process_intent_event(redis, event, policy)
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
        finally:
            last_ids[event.stream] = event.redis_stream_id
    return processed


async def run_loop(
    redis: RedisStreamClient,
    policy: RiskPolicy,
    stop: asyncio.Event,
    start_id: str = "$",
    count: int = 100,
    block_ms: int = 5000,
    retry_delay_seconds: float = 1.0,
) -> None:
    """Run the pre-trade checker loop until ``stop`` is set."""

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
    start_id: str = "$",
    count: int = 100,
    block_ms: int = 5000,
    retry_delay_seconds: float = 1.0,
) -> None:
    """Run the Pre-Trade Checker service until SIGINT/SIGTERM."""

    configure_logging(service_name, log_level)
    log.info(
        "pre_trade_checker.starting",
        redis_url=redis_url,
        start_id=start_id,
        count=count,
        block_ms=block_ms,
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    redis: Redis = Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_timeout=(block_ms / 1000) + 10,
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
