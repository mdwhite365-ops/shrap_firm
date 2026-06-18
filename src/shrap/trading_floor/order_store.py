"""Persistence helpers for paper execution order events.

This is the Month 1 storage seam for submitted orders, status updates, and
fills. It intentionally persists execution events as append-only records keyed
by ADR-0006 event ID; higher-level position/reconciliation state is built later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from shrap.events import ReceivedEvent

CREATE_TRADING_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS trading"

CREATE_PAPER_ORDER_EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS trading.paper_order_events (
    event_id TEXT PRIMARY KEY,
    event_topic TEXT NOT NULL,
    redis_stream_id TEXT NOT NULL,
    correlation_id TEXT,
    broker TEXT NOT NULL,
    broker_order_id TEXT NOT NULL,
    status TEXT,
    symbol TEXT,
    side TEXT,
    quantity TEXT,
    filled_quantity TEXT,
    filled_avg_price TEXT,
    submitted_order JSONB,
    broker_response JSONB,
    payload_json JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (event_topic, redis_stream_id)
)
""".strip()

CREATE_PAPER_ORDER_EVENTS_BROKER_ORDER_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS paper_order_events_broker_order_idx
ON trading.paper_order_events (broker, broker_order_id, occurred_at DESC)
""".strip()

CREATE_PAPER_ORDER_EVENTS_STATUS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS paper_order_events_status_idx
ON trading.paper_order_events (status, occurred_at DESC)
""".strip()

INSERT_PAPER_ORDER_EVENT_SQL = """
INSERT INTO trading.paper_order_events (
    event_id,
    event_topic,
    redis_stream_id,
    correlation_id,
    broker,
    broker_order_id,
    status,
    symbol,
    side,
    quantity,
    filled_quantity,
    filled_avg_price,
    submitted_order,
    broker_response,
    payload_json,
    occurred_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14::jsonb, $15::jsonb, $16)
ON CONFLICT (event_id) DO NOTHING
""".strip()


@dataclass(frozen=True, slots=True)
class PaperOrderRecord:
    """Append-only paper order event record."""

    event_id: str
    event_topic: str
    redis_stream_id: str
    correlation_id: str | None
    broker: str
    broker_order_id: str
    status: str | None
    symbol: str | None
    side: str | None
    quantity: str | None
    filled_quantity: str | None
    filled_avg_price: str | None
    submitted_order: dict[str, Any] | None
    broker_response: dict[str, Any] | None
    payload_json: dict[str, Any]
    occurred_at: object


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


class PostgresPaperOrderSink:
    """Append-only PostgreSQL sink for execution order events."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_TRADING_SCHEMA_SQL)
            await conn.execute(CREATE_PAPER_ORDER_EVENTS_TABLE_SQL)
            await conn.execute(CREATE_PAPER_ORDER_EVENTS_BROKER_ORDER_INDEX_SQL)
            await conn.execute(CREATE_PAPER_ORDER_EVENTS_STATUS_INDEX_SQL)

    async def upsert(self, record: PaperOrderRecord) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_PAPER_ORDER_EVENT_SQL,
                record.event_id,
                record.event_topic,
                record.redis_stream_id,
                record.correlation_id,
                record.broker,
                record.broker_order_id,
                record.status,
                record.symbol,
                record.side,
                record.quantity,
                record.filled_quantity,
                record.filled_avg_price,
                _json_or_none(record.submitted_order),
                _json_or_none(record.broker_response),
                json.dumps(record.payload_json, separators=(",", ":")),
                record.occurred_at,
            )


def _json_or_none(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, separators=(",", ":"))


def record_from_execution_event(event: ReceivedEvent) -> PaperOrderRecord:
    """Map execution order/status/fill events to persistent records."""

    payload = event.envelope.payload
    if payload is None:
        raise ValueError("execution event must carry an inline payload")
    broker = str(payload.get("broker", "alpaca-paper"))
    broker_order_id = str(payload.get("broker_order_id", "")).strip()
    if not broker_order_id:
        raise ValueError("execution event must include broker_order_id")

    submitted_order = _submitted_order(payload)
    broker_response = _dict_or_none(payload.get("broker_response"))
    symbol = _symbol(payload, submitted_order, broker_response)
    side = _side(payload, submitted_order, broker_response)
    quantity = _quantity(payload, submitted_order, broker_response)

    return PaperOrderRecord(
        event_id=event.envelope.event_id,
        event_topic=event.stream,
        redis_stream_id=event.redis_stream_id,
        correlation_id=event.envelope.correlation_id,
        broker=broker,
        broker_order_id=broker_order_id,
        status=_optional_str(payload.get("status")),
        symbol=symbol,
        side=side,
        quantity=quantity,
        filled_quantity=_optional_str(payload.get("filled_qty")),
        filled_avg_price=_optional_str(payload.get("filled_avg_price")),
        submitted_order=submitted_order,
        broker_response=broker_response,
        payload_json=payload,
        occurred_at=event.envelope.produced_at,
    )


def _dict_or_none(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    return None


def _submitted_order(payload: dict[str, Any]) -> dict[str, Any] | None:
    direct = _dict_or_none(payload.get("submitted_order"))
    if direct is not None:
        return direct
    submitted_payload = _dict_or_none(payload.get("submitted_payload"))
    if submitted_payload is None:
        return None
    return _dict_or_none(submitted_payload.get("submitted_order"))


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _symbol(
    payload: dict[str, Any],
    submitted_order: dict[str, Any] | None,
    broker_response: dict[str, Any] | None,
) -> str | None:
    return _first_str(
        payload.get("symbol"),
        submitted_order.get("symbol") if submitted_order else None,
        broker_response.get("symbol") if broker_response else None,
    )


def _side(
    payload: dict[str, Any],
    submitted_order: dict[str, Any] | None,
    broker_response: dict[str, Any] | None,
) -> str | None:
    return _first_str(
        payload.get("side"),
        submitted_order.get("side") if submitted_order else None,
        broker_response.get("side") if broker_response else None,
    )


def _quantity(
    payload: dict[str, Any],
    submitted_order: dict[str, Any] | None,
    broker_response: dict[str, Any] | None,
) -> str | None:
    return _first_str(
        payload.get("quantity"),
        submitted_order.get("qty") if submitted_order else None,
        broker_response.get("qty") if broker_response else None,
    )


def _first_str(*values: object) -> str | None:
    for value in values:
        if value is not None:
            return str(value)
    return None
