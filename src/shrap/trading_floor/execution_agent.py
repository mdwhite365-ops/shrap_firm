"""Paper Execution Agent for the Month 1 inner-loop spine.

Consumes approved risk decisions and submits paper orders through an injected
broker client. The agent is deliberately paper-only: it refuses any approved
intent whose preserved mode is not ``paper``.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any, Protocol, cast

import httpx
import structlog
from redis.asyncio import Redis

from shrap.common.logging import configure_logging
from shrap.events import EventPublisher, EventSubscriber, PublishedEvent, ReceivedEvent
from shrap.trading_floor.alpaca import AlpacaPaperClient, AlpacaPaperSettings, AsyncHttpClient

log = structlog.get_logger(__name__)

STREAM_RISK_APPROVED = "risk.intent.approved"
STREAM_EXECUTION_ORDER_SUBMITTED = "execution.order.submitted"
STREAM_EXECUTION_ORDER_STATUS_UPDATED = "execution.order.status-updated"
STREAM_EXECUTION_ORDER_FILLED = "execution.order.filled"
PRODUCED_BY = "trading-floor/execution-agent"
SCHEMA_VERSION = "1.0.0"


class RedisStreamClient(Protocol):
    async def xadd(self, stream: str, fields: dict[str, str]) -> str: ...

    async def xread(
        self,
        streams: dict[Any, Any],
        count: int | None = None,
        block: int | None = None,
    ) -> Any: ...


class PaperBroker(Protocol):
    async def submit_order(self, order: dict[str, Any]) -> dict[str, Any]: ...

    async def get_order(self, order_id: str) -> dict[str, Any]: ...


class AlpacaPaperBroker:
    """Adapter from Execution Agent's broker protocol to Alpaca paper HTTP."""

    def __init__(self, settings: AlpacaPaperSettings, http_client: AsyncHttpClient) -> None:
        self._client = AlpacaPaperClient(settings)
        self._http_client = http_client

    async def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        return await self._client.submit_order(self._http_client, order)

    async def get_order(self, order_id: str) -> dict[str, Any]:
        return await self._client.get_order(self._http_client, order_id)


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass


def build_paper_order(event: ReceivedEvent) -> dict[str, Any]:
    """Build the broker order payload from an approved risk event."""

    risk_payload = event.envelope.payload
    if risk_payload is None:
        raise ValueError("risk.intent.approved must carry an inline payload")
    if risk_payload.get("approved") is not True:
        raise ValueError("execution only consumes approved risk decisions")

    approved_intent = risk_payload.get("approved_intent_payload")
    if not isinstance(approved_intent, dict):
        raise ValueError("approved risk decision must include approved_intent_payload")
    if approved_intent.get("mode") != "paper":
        raise ValueError("execution agent refuses non-paper approved intents")

    ticker = str(approved_intent.get("ticker", "")).strip().upper()
    if not ticker:
        raise ValueError("approved intent ticker is required")
    side = str(approved_intent.get("side", "")).strip().lower()
    if side not in {"buy", "sell"}:
        raise ValueError("approved intent side must be buy or sell")
    quantity = int(approved_intent.get("quantity", 0))
    if quantity <= 0:
        raise ValueError("approved intent quantity must be positive")

    return {
        "symbol": ticker,
        "qty": str(quantity),
        "side": side,
        "type": "market",
        "time_in_force": "day",
        "client_order_id": event.envelope.event_id,
    }


def build_order_submitted_payload(
    event: ReceivedEvent,
    order: dict[str, Any],
    broker_response: dict[str, Any],
) -> dict[str, Any]:
    """Build the event payload recording a submitted paper order."""

    return {
        "risk_event_id": event.envelope.event_id,
        "risk_stream": event.stream,
        "risk_redis_stream_id": event.redis_stream_id,
        "broker": "alpaca-paper",
        "broker_order_id": str(broker_response.get("id", "")),
        "status": broker_response.get("status"),
        "submitted_order": order,
        "broker_response": broker_response,
        "risk_payload": event.envelope.payload,
    }


def build_order_status_payload(
    event: ReceivedEvent,
    broker_response: dict[str, Any],
) -> dict[str, Any]:
    """Build an execution status/fill payload for one submitted order."""

    submitted_payload = event.envelope.payload
    if submitted_payload is None:
        raise ValueError("execution.order.submitted must carry an inline payload")
    broker_order_id = str(submitted_payload.get("broker_order_id", "")).strip()
    if not broker_order_id:
        raise ValueError("execution.order.submitted must include broker_order_id")

    return {
        "submitted_event_id": event.envelope.event_id,
        "submitted_stream": event.stream,
        "submitted_redis_stream_id": event.redis_stream_id,
        "broker": submitted_payload.get("broker", "alpaca-paper"),
        "broker_order_id": broker_order_id,
        "status": broker_response.get("status"),
        "filled_qty": broker_response.get("filled_qty"),
        "filled_avg_price": broker_response.get("filled_avg_price"),
        "filled_at": broker_response.get("filled_at"),
        "submitted_payload": submitted_payload,
        "broker_response": broker_response,
    }


def _order_status_stream(status: object) -> str:
    if str(status).lower() == "filled":
        return STREAM_EXECUTION_ORDER_FILLED
    return STREAM_EXECUTION_ORDER_STATUS_UPDATED


async def process_order_status_event(
    redis: RedisStreamClient,
    broker: PaperBroker,
    event: ReceivedEvent,
    produced_by: str = PRODUCED_BY,
) -> PublishedEvent:
    """Fetch broker order status and publish status/fill event."""

    submitted_payload = event.envelope.payload
    if submitted_payload is None:
        raise ValueError("execution.order.submitted must carry an inline payload")
    broker_order_id = str(submitted_payload.get("broker_order_id", "")).strip()
    if not broker_order_id:
        raise ValueError("execution.order.submitted must include broker_order_id")

    broker_response = await broker.get_order(broker_order_id)
    payload = build_order_status_payload(event, broker_response)
    return await EventPublisher(redis).publish(
        stream=_order_status_stream(payload["status"]),
        produced_by=produced_by,
        schema_version=SCHEMA_VERSION,
        payload=payload,
        correlation_id=event.envelope.event_id,
    )


async def process_risk_event(
    redis: RedisStreamClient,
    broker: PaperBroker,
    event: ReceivedEvent,
    produced_by: str = PRODUCED_BY,
) -> PublishedEvent:
    """Submit one approved paper order and publish the execution event."""

    order = build_paper_order(event)
    broker_response = await broker.submit_order(order)
    payload = build_order_submitted_payload(event, order, broker_response)
    return await EventPublisher(redis).publish(
        stream=STREAM_EXECUTION_ORDER_SUBMITTED,
        produced_by=produced_by,
        schema_version=SCHEMA_VERSION,
        payload=payload,
        correlation_id=event.envelope.event_id,
    )


async def poll_once(
    redis: RedisStreamClient,
    broker: PaperBroker,
    last_ids: dict[str, str],
    start_id: str,
    count: int,
    block_ms: int,
) -> int:
    """Read approved risk decisions and submit paper orders."""

    last_ids.setdefault(STREAM_RISK_APPROVED, start_id)
    subscriber = EventSubscriber(redis)
    try:
        events = await subscriber.read(streams=last_ids, count=count, block_ms=block_ms)
    except Exception:
        log.exception("execution_agent.read_failed", streams=dict(last_ids))
        return 0

    processed = 0
    for event in events:
        try:
            result = await process_risk_event(redis, broker, event)
            last_ids[event.stream] = event.redis_stream_id
            processed += 1
            log.info(
                "execution_agent.order_submitted",
                risk_event_id=event.envelope.event_id,
                execution_event_id=result.envelope.event_id,
                stream=result.stream,
            )
        except Exception:
            log.exception(
                "execution_agent.risk_event_failed",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                risk_event_id=event.envelope.event_id,
            )
            break
    return processed


async def poll_order_status_once(
    redis: RedisStreamClient,
    broker: PaperBroker,
    last_ids: dict[str, str],
    start_id: str,
    count: int,
    block_ms: int,
) -> int:
    """Read submitted paper orders and publish current broker status/fill events."""

    last_ids.setdefault(STREAM_EXECUTION_ORDER_SUBMITTED, start_id)
    subscriber = EventSubscriber(redis)
    try:
        events = await subscriber.read(streams=last_ids, count=count, block_ms=block_ms)
    except Exception:
        log.exception("execution_agent.status_read_failed", streams=dict(last_ids))
        return 0

    processed = 0
    for event in events:
        try:
            result = await process_order_status_event(redis, broker, event)
            last_ids[event.stream] = event.redis_stream_id
            processed += 1
            log.info(
                "execution_agent.order_status_published",
                submitted_event_id=event.envelope.event_id,
                status_event_id=result.envelope.event_id,
                stream=result.stream,
            )
        except Exception:
            log.exception(
                "execution_agent.order_status_failed",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                submitted_event_id=event.envelope.event_id,
            )
            break
    return processed


async def run_loop(
    redis: RedisStreamClient,
    broker: PaperBroker,
    stop: asyncio.Event,
    start_id: str = "0-0",
    count: int = 100,
    block_ms: int = 5000,
    retry_delay_seconds: float = 1.0,
) -> None:
    """Run the paper Execution Agent loop until ``stop`` is set."""

    risk_last_ids: dict[str, str] = {}
    status_last_ids: dict[str, str] = {}
    while not stop.is_set():
        try:
            submitted = await poll_once(
                redis=redis,
                broker=broker,
                last_ids=risk_last_ids,
                start_id=start_id,
                count=count,
                block_ms=block_ms,
            )
            status_checked = await poll_order_status_once(
                redis=redis,
                broker=broker,
                last_ids=status_last_ids,
                start_id=start_id,
                count=count,
                block_ms=block_ms,
            )
            processed = submitted + status_checked
            if processed:
                log.info(
                    "execution_agent.batch",
                    submitted=submitted,
                    status_checked=status_checked,
                    risk_last_ids=dict(risk_last_ids),
                    status_last_ids=dict(status_last_ids),
                )
            else:
                await asyncio.sleep(0)
        except Exception:
            log.exception("execution_agent.poll_failed")
            await asyncio.sleep(retry_delay_seconds)


async def run(
    redis_url: str,
    alpaca_settings: AlpacaPaperSettings,
    service_name: str = "execution-agent",
    log_level: str = "INFO",
    start_id: str = "0-0",
    count: int = 100,
    block_ms: int = 5000,
    retry_delay_seconds: float = 1.0,
) -> None:
    """Run the paper Execution Agent service until SIGINT/SIGTERM."""

    configure_logging(service_name, log_level)
    log.info(
        "execution_agent.starting",
        redis_url=redis_url,
        alpaca=alpaca_settings.redacted(),
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
    async with httpx.AsyncClient(timeout=(block_ms / 1000) + 10) as http_client:
        broker = AlpacaPaperBroker(alpaca_settings, cast(AsyncHttpClient, http_client))
        try:
            await run_loop(
                cast(RedisStreamClient, redis),
                broker=broker,
                stop=stop,
                start_id=start_id,
                count=count,
                block_ms=block_ms,
                retry_delay_seconds=retry_delay_seconds,
            )
        finally:
            await redis.aclose()
            log.info("execution_agent.stopped")


__all__ = [
    "PRODUCED_BY",
    "SCHEMA_VERSION",
    "STREAM_EXECUTION_ORDER_FILLED",
    "STREAM_EXECUTION_ORDER_STATUS_UPDATED",
    "STREAM_EXECUTION_ORDER_SUBMITTED",
    "STREAM_RISK_APPROVED",
    "AlpacaPaperBroker",
    "build_order_status_payload",
    "build_order_submitted_payload",
    "build_paper_order",
    "poll_once",
    "poll_order_status_once",
    "process_order_status_event",
    "process_risk_event",
    "run",
    "run_loop",
]
