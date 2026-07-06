"""Paper Execution Agent for the Month 1 inner-loop spine.

Consumes approved risk decisions and submits paper orders through an injected
broker client. The agent is deliberately paper-only: it refuses any approved
intent whose preserved mode is not ``paper``.
"""

from __future__ import annotations

import asyncio
import signal
from dataclasses import dataclass
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


def is_duplicate_order_error(exc: Exception) -> bool:
    """True when Alpaca rejected the order because client_order_id already exists.

    Alpaca returns 422 with a message like 'client order id must be unique'.
    Because client_order_id is the risk event ID, this only happens when the
    same approved intent is replayed (e.g. restart with start_id 0-0) — the
    order was already submitted, so the replay is safely skippable.
    """

    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    if exc.response.status_code != 422:
        return False
    try:
        body = exc.response.json()
    except Exception:
        return False
    if not isinstance(body, dict):
        return False
    message = str(body.get("message", "")).lower().replace("_", " ")
    return "client order id" in message


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


TERMINAL_ORDER_STATUSES = frozenset({"filled", "canceled", "cancelled", "expired", "rejected"})


def is_terminal_order_status(status: object) -> bool:
    """True when the broker will never change this order's status again."""

    return str(status).lower() in TERMINAL_ORDER_STATUSES


@dataclass(slots=True)
class PendingOrder:
    """A submitted order whose status is not yet terminal.

    Kept in memory only. On restart the loop replays the submitted stream from
    ``start_id`` and pending orders re-enter the watch set on their first
    status check.
    """

    submitted_event: ReceivedEvent
    broker_order_id: str
    last_status: str
    next_check_at: float


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
        except ValueError:
            # Malformed event: permanent for this event. Skip it or the loop
            # stalls forever on a poison message and never reaches new intents.
            log.exception(
                "execution_agent.risk_event_invalid_skipped",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                risk_event_id=event.envelope.event_id,
            )
            last_ids[event.stream] = event.redis_stream_id
            continue
        except Exception as exc:
            if is_duplicate_order_error(exc):
                # Restart replay: this intent's order already exists at the
                # broker (client_order_id is the risk event ID, so replays are
                # deduplicated broker-side). Reconciliation covers any order
                # whose submitted event never persisted.
                log.warning(
                    "execution_agent.duplicate_order_skipped",
                    stream=event.stream,
                    redis_stream_id=event.redis_stream_id,
                    risk_event_id=event.envelope.event_id,
                )
                last_ids[event.stream] = event.redis_stream_id
                continue
            # Systemic error (broker down, bad credentials, network): stop the
            # batch WITHOUT advancing so the same event retries next cycle.
            log.exception(
                "execution_agent.risk_event_failed",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                risk_event_id=event.envelope.event_id,
            )
            break
    return processed


def _track_if_pending(
    pending: dict[str, PendingOrder] | None,
    event: ReceivedEvent,
    status_event: PublishedEvent,
    now: float,
    poll_interval_seconds: float,
) -> None:
    """Add a non-terminal order to the pending watch set; drop a terminal one."""

    if pending is None:
        return
    payload = status_event.envelope.payload or {}
    broker_order_id = str(payload.get("broker_order_id", ""))
    if not broker_order_id:
        return
    status = str(payload.get("status", ""))
    if is_terminal_order_status(status):
        pending.pop(broker_order_id, None)
        return
    pending[broker_order_id] = PendingOrder(
        submitted_event=event,
        broker_order_id=broker_order_id,
        last_status=status,
        next_check_at=now + poll_interval_seconds,
    )


async def repoll_pending_once(
    redis: RedisStreamClient,
    broker: PaperBroker,
    pending: dict[str, PendingOrder],
    now: float,
    poll_interval_seconds: float,
    produced_by: str = PRODUCED_BY,
) -> int:
    """Re-check pending orders that are due; publish only on status change.

    This is what closes KI-003: without it a fill that lands after the single
    post-submission status check is never observed. Publishing only on change
    keeps the status stream and the order-event table free of no-op rows.
    """

    checked = 0
    for broker_order_id, entry in list(pending.items()):
        if entry.next_check_at > now:
            continue
        try:
            broker_response = await broker.get_order(broker_order_id)
        except Exception:
            log.exception(
                "execution_agent.repoll_failed",
                broker_order_id=broker_order_id,
            )
            entry.next_check_at = now + poll_interval_seconds
            continue
        checked += 1
        status = str(broker_response.get("status", ""))
        if status.lower() != entry.last_status.lower():
            payload = build_order_status_payload(entry.submitted_event, broker_response)
            result = await EventPublisher(redis).publish(
                stream=_order_status_stream(status),
                produced_by=produced_by,
                schema_version=SCHEMA_VERSION,
                payload=payload,
                correlation_id=entry.submitted_event.envelope.event_id,
            )
            log.info(
                "execution_agent.pending_status_changed",
                broker_order_id=broker_order_id,
                previous_status=entry.last_status,
                status=status,
                status_event_id=result.envelope.event_id,
                stream=result.stream,
            )
        if is_terminal_order_status(status):
            del pending[broker_order_id]
        else:
            entry.last_status = status
            entry.next_check_at = now + poll_interval_seconds
    return checked


async def poll_order_status_once(
    redis: RedisStreamClient,
    broker: PaperBroker,
    last_ids: dict[str, str],
    start_id: str,
    count: int,
    block_ms: int,
    pending: dict[str, PendingOrder] | None = None,
    poll_interval_seconds: float = 5.0,
    now: float = 0.0,
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
            _track_if_pending(pending, event, result, now, poll_interval_seconds)
            log.info(
                "execution_agent.order_status_published",
                submitted_event_id=event.envelope.event_id,
                status_event_id=result.envelope.event_id,
                stream=result.stream,
            )
        except ValueError:
            # Malformed submitted event: permanent for this event; skip it so
            # the status loop cannot stall on a poison message.
            log.exception(
                "execution_agent.order_status_invalid_skipped",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                submitted_event_id=event.envelope.event_id,
            )
            last_ids[event.stream] = event.redis_stream_id
            continue
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
    status_poll_interval_seconds: float = 5.0,
) -> None:
    """Run the paper Execution Agent loop until ``stop`` is set."""

    loop = asyncio.get_running_loop()
    risk_last_ids: dict[str, str] = {}
    status_last_ids: dict[str, str] = {}
    pending: dict[str, PendingOrder] = {}
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
                pending=pending,
                poll_interval_seconds=status_poll_interval_seconds,
                now=loop.time(),
            )
            repolled = await repoll_pending_once(
                redis=redis,
                broker=broker,
                pending=pending,
                now=loop.time(),
                poll_interval_seconds=status_poll_interval_seconds,
            )
            processed = submitted + status_checked + repolled
            if processed:
                log.info(
                    "execution_agent.batch",
                    submitted=submitted,
                    status_checked=status_checked,
                    repolled=repolled,
                    pending=len(pending),
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
    status_poll_interval_seconds: float = 5.0,
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
                status_poll_interval_seconds=status_poll_interval_seconds,
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
    "TERMINAL_ORDER_STATUSES",
    "AlpacaPaperBroker",
    "PendingOrder",
    "build_order_status_payload",
    "build_order_submitted_payload",
    "build_paper_order",
    "is_duplicate_order_error",
    "is_terminal_order_status",
    "poll_once",
    "poll_order_status_once",
    "process_order_status_event",
    "process_risk_event",
    "repoll_pending_once",
    "run",
    "run_loop",
]
