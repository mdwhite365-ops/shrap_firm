"""Tests for the live compose-stack spine smoke (Card 15/16)."""

from __future__ import annotations

from typing import Any

import pytest

from shrap.events import EventPublisher
from shrap.trading_floor.spine_smoke import (
    STREAM_INTENT,
    STREAM_ORDER_FILLED,
    STREAM_ORDER_STATUS,
    STREAM_ORDER_SUBMITTED,
    STREAM_RECONCILIATION_COMPLETED,
    STREAM_RISK_APPROVED,
    STREAM_RISK_VETOED,
    run_spine_smoke,
)


class ScriptedSpineRedis:
    """Fake Redis where scripted 'services' react to the published intent.

    When the smoke publishes to ``trading.decision.intent``, this fake appends
    the configured downstream events to their streams, correlated the same way
    the real services correlate them.
    """

    def __init__(
        self,
        decision_stream: str = STREAM_RISK_APPROVED,
        decision_payload_extra: dict[str, Any] | None = None,
        submit_order: bool = True,
        status_stream: str = STREAM_ORDER_STATUS,
        status_value: str = "accepted",
        fill_later: bool = False,
        reconciliation_payload: dict[str, Any] | None = None,
    ) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self._next_id = 0
        self._decision_stream = decision_stream
        self._decision_payload_extra = decision_payload_extra or {}
        self._submit_order = submit_order
        self._status_stream = status_stream
        self._status_value = status_value
        self._fill_later = fill_later
        self._reconciliation_payload = reconciliation_payload
        self._xread_calls = 0

    def _new_id(self) -> str:
        self._next_id += 1
        return f"1780128600{self._next_id:03d}-0"

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        entry_id = self._new_id()
        self.streams.setdefault(stream, []).append((entry_id, fields))
        if stream == STREAM_INTENT:
            await self._react_to_intent(fields)
        return entry_id

    async def _react_to_intent(self, intent_fields: dict[str, str]) -> None:
        intent_event_id = intent_fields["h_event_id"]
        publisher = EventPublisher(_RawRedis(self))
        decision = await publisher.publish(
            stream=self._decision_stream,
            produced_by="risk/pre-trade-checker",
            schema_version="1.0.0",
            payload={
                "approved": self._decision_stream == STREAM_RISK_APPROVED,
                "intent_event_id": intent_event_id,
                "reasons": ["scripted"],
                **self._decision_payload_extra,
            },
            correlation_id=intent_event_id,
        )
        if self._decision_stream != STREAM_RISK_APPROVED or not self._submit_order:
            return
        submitted = await publisher.publish(
            stream=STREAM_ORDER_SUBMITTED,
            produced_by="trading-floor/execution-agent",
            schema_version="1.0.0",
            payload={
                "broker": "alpaca-paper",
                "broker_order_id": "order-1",
                "status": "accepted",
                "risk_payload": {"intent_event_id": intent_event_id},
            },
            correlation_id=decision.envelope.event_id,
        )
        await publisher.publish(
            stream=self._status_stream,
            produced_by="trading-floor/execution-agent",
            schema_version="1.0.0",
            payload={
                "broker": "alpaca-paper",
                "broker_order_id": "order-1",
                "status": self._status_value,
                "filled_qty": "1" if self._status_value == "filled" else "0",
                "filled_avg_price": "185.25" if self._status_value == "filled" else None,
            },
            correlation_id=submitted.envelope.event_id,
        )
        if self._fill_later:
            await publisher.publish(
                stream=STREAM_ORDER_FILLED,
                produced_by="trading-floor/execution-agent",
                schema_version="1.0.0",
                payload={
                    "broker": "alpaca-paper",
                    "broker_order_id": "order-1",
                    "status": "filled",
                    "filled_qty": "1",
                    "filled_avg_price": "185.25",
                },
                correlation_id=submitted.envelope.event_id,
            )
        if self._reconciliation_payload is not None:
            await publisher.publish(
                stream=STREAM_RECONCILIATION_COMPLETED,
                produced_by="operations/reconciliation-agent",
                schema_version="1.0.0",
                payload=self._reconciliation_payload,
            )

    async def xrevrange(
        self, stream: str, max: str = "+", min: str = "-", count: int = 1
    ) -> list[tuple[str, dict[str, str]]]:
        entries = self.streams.get(stream, [])
        return entries[-count:][::-1] if entries else []

    async def xread(
        self,
        streams: dict[Any, Any],
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        self._xread_calls += 1
        response: list[tuple[str, list[tuple[str, dict[str, str]]]]] = []
        for stream, last_id in streams.items():
            entries = [
                (entry_id, fields)
                for entry_id, fields in self.streams.get(str(stream), [])
                if self._after(entry_id, str(last_id))
            ]
            if entries:
                response.append((str(stream), entries[: count or len(entries)]))
        return response

    @staticmethod
    def _after(entry_id: str, last_id: str) -> bool:
        if last_id in ("0", "0-0"):
            return True
        return entry_id > last_id


class _RawRedis:
    """Adapter so the scripted reactions append without re-triggering reactions."""

    def __init__(self, outer: ScriptedSpineRedis) -> None:
        self._outer = outer

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        entry_id = self._outer._new_id()
        self._outer.streams.setdefault(stream, []).append((entry_id, fields))
        return entry_id


class FakeDb:
    """Row counts keyed by substring of the SQL statement."""

    def __init__(self, order_rows: int = 2, audit_rows: int = 4, fill_rows: int = 1) -> None:
        self._order_rows = order_rows
        self._audit_rows = audit_rows
        self._fill_rows = fill_rows

    async def fetch(self, sql: str, *args: object) -> list[Any]:
        if "ops.audit_events" in sql:
            return [{"n": self._audit_rows}]
        if "event_topic" in sql:
            return [{"n": self._fill_rows}]
        return [{"n": self._order_rows}]


@pytest.mark.asyncio
async def test_spine_smoke_happy_path_passes_all_card_15_checks() -> None:
    redis = ScriptedSpineRedis()
    report = await run_spine_smoke(
        redis=redis,
        db=FakeDb(),
        event_timeout_seconds=1.0,
        db_timeout_seconds=1.0,
    )

    assert report.passed
    assert [check.name for check in report.checks] == [
        "intent-published",
        "risk-decision",
        "order-submitted",
        "order-status",
        "paper-order-events-persisted",
        "audit-trail",
    ]


@pytest.mark.asyncio
async def test_spine_smoke_fails_when_intent_is_vetoed() -> None:
    redis = ScriptedSpineRedis(decision_stream=STREAM_RISK_VETOED)
    report = await run_spine_smoke(
        redis=redis,
        db=FakeDb(),
        event_timeout_seconds=1.0,
        db_timeout_seconds=1.0,
    )

    assert not report.passed
    assert report.checks[-1].name == "risk-decision"
    assert "VETOED" in report.checks[-1].detail


@pytest.mark.asyncio
async def test_spine_smoke_fails_fast_when_no_services_respond() -> None:
    redis = ScriptedSpineRedis(decision_stream="never.published", submit_order=False)
    redis._decision_stream = "risk.never"  # nothing the smoke watches
    report = await run_spine_smoke(
        redis=redis,
        db=FakeDb(),
        event_timeout_seconds=0.2,
        db_timeout_seconds=0.2,
    )

    assert not report.passed
    assert report.checks[-1].name == "risk-decision"
    assert "Pre-Trade Checker" in report.checks[-1].detail


@pytest.mark.asyncio
async def test_spine_smoke_fails_when_persistence_rows_missing() -> None:
    redis = ScriptedSpineRedis()
    report = await run_spine_smoke(
        redis=redis,
        db=FakeDb(order_rows=0, audit_rows=0),
        event_timeout_seconds=1.0,
        db_timeout_seconds=0.2,
    )

    assert not report.passed
    failed = {check.name for check in report.checks if not check.passed}
    assert failed == {"paper-order-events-persisted", "audit-trail"}


@pytest.mark.asyncio
async def test_spine_smoke_wait_fill_observes_fill_and_persistence() -> None:
    redis = ScriptedSpineRedis(fill_later=True)
    report = await run_spine_smoke(
        redis=redis,
        db=FakeDb(),
        event_timeout_seconds=1.0,
        db_timeout_seconds=1.0,
        wait_fill=True,
        fill_timeout_seconds=1.0,
    )

    assert report.passed
    names = [check.name for check in report.checks]
    assert "order-filled" in names
    assert "fill-persisted" in names


@pytest.mark.asyncio
async def test_spine_smoke_wait_reconciliation_requires_clean_pass() -> None:
    redis = ScriptedSpineRedis(
        reconciliation_payload={"clean": False, "discrepancies": 2},
    )
    report = await run_spine_smoke(
        redis=redis,
        db=FakeDb(),
        event_timeout_seconds=1.0,
        db_timeout_seconds=1.0,
        wait_reconciliation=True,
        reconciliation_timeout_seconds=1.0,
    )

    assert not report.passed
    assert report.checks[-1].name == "reconciliation"
    assert "clean=False" in report.checks[-1].detail
