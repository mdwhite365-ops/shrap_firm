"""Reconciliation Agent core.

One reconciliation pass reads the broker snapshot and the persisted order
trail, compares them, and publishes the outcome through ADR-0006 events:
``operations.reconciliation-discrepancy`` per divergence, then
``operations.reconciliation-completed`` with the run summary. All events in
one pass share a correlation ID so consumers can group them.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import structlog
from ulid import ULID

from shrap.agents.operations.reconciliation_agent.broker import BrokerSnapshotReader
from shrap.agents.operations.reconciliation_agent.records import (
    ReconciliationReport,
    StoredOrderState,
    compare_orders,
)

log = structlog.get_logger(__name__)

STREAM_RECONCILIATION_COMPLETED = "operations.reconciliation-completed"
STREAM_RECONCILIATION_DISCREPANCY = "operations.reconciliation-discrepancy"
SCHEMA_VERSION = "1.0.0"
DEFAULT_PRODUCED_BY = "operations/reconciliation-agent"
DEFAULT_BROKER = "alpaca-paper"


class OrderStateRepository(Protocol):
    async def latest_order_states(
        self, broker: str, since: object | None = None
    ) -> Sequence[StoredOrderState]: ...


class AccountSnapshotSink(Protocol):
    async def record(self, event_id: str, broker: str, account: dict[str, Any]) -> None: ...


class Publisher(Protocol):
    async def publish(
        self,
        stream: str,
        produced_by: str,
        schema_version: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> object: ...


async def reconcile_once(
    broker_reader: BrokerSnapshotReader,
    repository: OrderStateRepository,
    publisher: Publisher,
    produced_by: str = DEFAULT_PRODUCED_BY,
    broker: str = DEFAULT_BROKER,
    correlation_id: str | None = None,
    snapshot_sink: AccountSnapshotSink | None = None,
    lookback_days: float | None = None,
) -> ReconciliationReport:
    """Run one reconciliation pass and publish its outcome.

    The account read runs first: it verifies broker connectivity and its
    status travels in the completed event. A broker or database failure
    raises before any event is published — a pass either reports a full
    comparison or reports nothing.
    """

    run_id = correlation_id or str(ULID())
    cutoff: datetime | None = None
    if lookback_days is not None and lookback_days > 0:
        cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

    account = await broker_reader.get_account()
    broker_orders = await broker_reader.list_orders(since=cutoff.isoformat() if cutoff else None)
    stored = await repository.latest_order_states(broker, since=cutoff)

    report = compare_orders(stored=stored, broker_orders=broker_orders, broker=broker)

    for discrepancy in report.discrepancies:
        await publisher.publish(
            stream=STREAM_RECONCILIATION_DISCREPANCY,
            produced_by=produced_by,
            schema_version=SCHEMA_VERSION,
            payload={
                "broker": broker,
                "kind": discrepancy.kind,
                "broker_order_id": discrepancy.broker_order_id,
                "stored_status": discrepancy.stored_status,
                "broker_status": discrepancy.broker_status,
                "symbol": discrepancy.symbol,
            },
            correlation_id=run_id,
        )
        log.warning(
            "reconciliation.discrepancy",
            broker=broker,
            kind=discrepancy.kind,
            broker_order_id=discrepancy.broker_order_id,
            stored_status=discrepancy.stored_status,
            broker_status=discrepancy.broker_status,
        )

    account_summary = {
        "status": _optional_str(account.get("status")),
        "currency": _optional_str(account.get("currency")),
        "cash": _optional_str(account.get("cash")),
        "equity": _optional_str(account.get("equity")),
        "buying_power": _optional_str(account.get("buying_power")),
        "portfolio_value": _optional_str(account.get("portfolio_value")),
    }
    if snapshot_sink is not None:
        await snapshot_sink.record(run_id, broker, account)
    await publisher.publish(
        stream=STREAM_RECONCILIATION_COMPLETED,
        produced_by=produced_by,
        schema_version=SCHEMA_VERSION,
        payload={
            "broker": broker,
            "account_status": str(account.get("status", "")),
            "account": account_summary,
            "stored_orders": report.stored_orders,
            "broker_orders": report.broker_orders,
            "matched": report.matched,
            "discrepancies": len(report.discrepancies),
            "clean": report.is_clean,
            "lookback_days": lookback_days,
        },
        correlation_id=run_id,
    )
    log.info(
        "reconciliation.completed",
        broker=broker,
        stored_orders=report.stored_orders,
        broker_orders=report.broker_orders,
        matched=report.matched,
        discrepancies=len(report.discrepancies),
        clean=report.is_clean,
    )
    return report


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


__all__ = [
    "DEFAULT_BROKER",
    "DEFAULT_PRODUCED_BY",
    "SCHEMA_VERSION",
    "STREAM_RECONCILIATION_COMPLETED",
    "STREAM_RECONCILIATION_DISCREPANCY",
    "OrderStateRepository",
    "Publisher",
    "reconcile_once",
]
