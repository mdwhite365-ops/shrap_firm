"""Live compose-stack paper-spine smoke (Card 15/16).

Unlike ``paper_spine_smoke`` (which drives the pipeline in-process with fakes),
this smoke talks to a RUNNING service stack: it publishes one hand-crafted
paper intent to Redis and then watches the deployed Pre-Trade Checker,
Execution Agent, Paper Order Store, and Audit Logger do their jobs. It never
touches the broker itself — every downstream event must come from the real
services or the smoke fails.

Checks, in order:

1. Intent published to ``trading.decision.intent``.
2. Deployed Pre-Trade Checker approves it (``risk.intent.approved``).
3. Deployed Execution Agent submits it (``execution.order.submitted``).
4. A broker status event follows (``execution.order.status-updated`` or
   ``execution.order.filled``).
5. ``trading.paper_order_events`` holds the persisted rows.
6. ``ops.audit_events`` holds the audit rows for every event in the chain.
7. Optional ``--wait-fill`` (Card 16): a fill event is observed and persisted.
8. Optional ``--wait-reconciliation`` (Card 16): the next reconciliation pass
   completes clean.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ulid import ULID

from shrap.events import Envelope, EventPublisher, normalize_redis_fields
from shrap.trading_floor.intent import build_handcrafted_intent

STREAM_INTENT = "trading.decision.intent"
STREAM_RISK_APPROVED = "risk.intent.approved"
STREAM_RISK_VETOED = "risk.intent.vetoed"
STREAM_ORDER_SUBMITTED = "execution.order.submitted"
STREAM_ORDER_STATUS = "execution.order.status-updated"
STREAM_ORDER_FILLED = "execution.order.filled"
STREAM_RECONCILIATION_COMPLETED = "operations.reconciliation-completed"

SCHEMA_VERSION = "1.0.0"
PRODUCED_BY = "trading-floor/spine-smoke"

SMOKE_JUSTIFICATION = (
    "Card 15/16 compose-stack smoke order. Why this might be wrong: it is a "
    "deliberate single-share paper probe, not an alpha signal."
)


class SmokeRedis(Protocol):
    async def xadd(self, stream: str, fields: dict[str, str]) -> str: ...

    async def xrevrange(
        self, stream: str, max: str = "+", min: str = "-", count: int = 1
    ) -> Any: ...

    async def xread(
        self,
        streams: dict[Any, Any],
        count: int | None = None,
        block: int | None = None,
    ) -> Any: ...


class SmokeDb(Protocol):
    async def fetch(self, sql: str, *args: object) -> list[Any]: ...


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class SmokeReport:
    checks: list[CheckResult] = field(default_factory=list)

    def record(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append(CheckResult(name=name, passed=passed, detail=detail))
        marker = "PASS" if passed else "FAIL"
        print(f"[{marker}] {name}: {detail}", flush=True)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


async def _stream_cutoff(redis: SmokeRedis, stream: str) -> str:
    """Return the last existing entry ID so the smoke only sees new events."""

    entries = await redis.xrevrange(stream, count=1)
    if entries:
        entry_id, _ = entries[0]
        return entry_id if isinstance(entry_id, str) else entry_id.decode("utf-8")
    return "0-0"


async def _wait_for_event(
    redis: SmokeRedis,
    cutoffs: dict[str, str],
    matcher: Callable[[str, Envelope], bool],
    timeout_seconds: float,
    poll_block_ms: int = 2000,
) -> tuple[str, Envelope] | None:
    """Read the watched streams until ``matcher`` accepts an event or time runs out."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        response = await redis.xread(streams=dict(cutoffs), count=100, block=poll_block_ms)
        for stream, entries in response:
            stream_name = stream if isinstance(stream, str) else stream.decode("utf-8")
            for entry_id, fields in entries:
                entry_id_str = entry_id if isinstance(entry_id, str) else entry_id.decode("utf-8")
                cutoffs[stream_name] = entry_id_str
                envelope = Envelope.from_redis_fields(normalize_redis_fields(fields))
                if matcher(stream_name, envelope):
                    return stream_name, envelope
    return None


async def _wait_for_rows(
    fetch_count: Callable[[], Awaitable[int]],
    minimum: int,
    timeout_seconds: float,
    poll_interval_seconds: float = 2.0,
) -> int:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    count = 0
    while loop.time() < deadline:
        count = await fetch_count()
        if count >= minimum:
            return count
        await asyncio.sleep(poll_interval_seconds)
    return count


def _payload_field(envelope: Envelope, key: str) -> object:
    if envelope.payload is None:
        return None
    return envelope.payload.get(key)


async def run_spine_smoke(
    redis: SmokeRedis,
    db: SmokeDb,
    ticker: str = "AAPL",
    side: str = "buy",
    quantity: int = 1,
    event_timeout_seconds: float = 60.0,
    db_timeout_seconds: float = 60.0,
    wait_fill: bool = False,
    fill_timeout_seconds: float = 300.0,
    wait_reconciliation: bool = False,
    reconciliation_timeout_seconds: float = 420.0,
) -> SmokeReport:
    """Run the live compose-stack spine smoke and return per-check results."""

    report = SmokeReport()
    strategy_id = f"smoke-{ULID()}"
    watched = [
        STREAM_RISK_APPROVED,
        STREAM_RISK_VETOED,
        STREAM_ORDER_SUBMITTED,
        STREAM_ORDER_STATUS,
        STREAM_ORDER_FILLED,
        STREAM_RECONCILIATION_COMPLETED,
    ]
    cutoffs = {stream: await _stream_cutoff(redis, stream) for stream in watched}

    # 1. Publish the intent.
    intent_payload = build_handcrafted_intent(
        ticker=ticker,
        side=side,
        quantity=quantity,
        strategy_id=strategy_id,
        justification=SMOKE_JUSTIFICATION,
    )
    intent = await EventPublisher(redis).publish(
        stream=STREAM_INTENT,
        produced_by=PRODUCED_BY,
        schema_version=SCHEMA_VERSION,
        payload=intent_payload,
    )
    intent_event_id = intent.envelope.event_id
    report.record(
        "intent-published",
        True,
        f"{ticker} {side} x{quantity} strategy={strategy_id} event_id={intent_event_id}",
    )

    # 2. Deployed Pre-Trade Checker decision.
    def _is_our_decision(stream: str, envelope: Envelope) -> bool:
        if stream not in (STREAM_RISK_APPROVED, STREAM_RISK_VETOED):
            return False
        return (
            envelope.correlation_id == intent_event_id
            or _payload_field(envelope, "intent_event_id") == intent_event_id
        )

    decision = await _wait_for_event(redis, cutoffs, _is_our_decision, event_timeout_seconds)
    if decision is None:
        report.record(
            "risk-decision",
            False,
            f"no decision within {event_timeout_seconds}s — is the Pre-Trade Checker running?",
        )
        return report
    decision_stream, decision_envelope = decision
    if decision_stream != STREAM_RISK_APPROVED:
        reasons = _payload_field(decision_envelope, "reasons")
        report.record("risk-decision", False, f"intent was VETOED: {reasons}")
        return report
    risk_event_id = decision_envelope.event_id
    report.record("risk-decision", True, f"approved, event_id={risk_event_id}")

    # 3. Deployed Execution Agent submission.
    def _is_our_submission(stream: str, envelope: Envelope) -> bool:
        if stream != STREAM_ORDER_SUBMITTED:
            return False
        if envelope.correlation_id == risk_event_id:
            return True
        risk_payload = _payload_field(envelope, "risk_payload")
        if isinstance(risk_payload, dict):
            return risk_payload.get("intent_event_id") == intent_event_id
        return False

    submission = await _wait_for_event(redis, cutoffs, _is_our_submission, event_timeout_seconds)
    if submission is None:
        report.record(
            "order-submitted",
            False,
            f"no submission within {event_timeout_seconds}s — is the Execution Agent running "
            "with valid Alpaca paper credentials?",
        )
        return report
    _, submitted_envelope = submission
    broker_order_id = str(_payload_field(submitted_envelope, "broker_order_id") or "")
    if not broker_order_id:
        report.record("order-submitted", False, "submission event lacks broker_order_id")
        return report
    report.record(
        "order-submitted",
        True,
        f"broker_order_id={broker_order_id} event_id={submitted_envelope.event_id}",
    )

    # 4. Broker status event.
    def _is_our_status(stream: str, envelope: Envelope) -> bool:
        if stream not in (STREAM_ORDER_STATUS, STREAM_ORDER_FILLED):
            return False
        return _payload_field(envelope, "broker_order_id") == broker_order_id

    status = await _wait_for_event(redis, cutoffs, _is_our_status, event_timeout_seconds)
    if status is None:
        report.record("order-status", False, f"no status event within {event_timeout_seconds}s")
        return report
    status_stream, status_envelope = status
    status_value = _payload_field(status_envelope, "status")
    report.record("order-status", True, f"stream={status_stream} status={status_value}")

    # 5. Persisted order events.
    async def _order_row_count() -> int:
        rows = await db.fetch(
            "SELECT count(*) AS n FROM trading.paper_order_events WHERE broker_order_id = $1",
            broker_order_id,
        )
        return int(rows[0]["n"]) if rows else 0

    order_rows = await _wait_for_rows(
        _order_row_count, minimum=2, timeout_seconds=db_timeout_seconds
    )
    report.record(
        "paper-order-events-persisted",
        order_rows >= 2,
        f"{order_rows} rows for broker_order_id={broker_order_id} (expected >= 2) — "
        "is the Paper Order Store running?"
        if order_rows < 2
        else f"{order_rows} rows for broker_order_id={broker_order_id}",
    )

    # 6. Audit trail.
    chain_event_ids = [
        intent_event_id,
        risk_event_id,
        submitted_envelope.event_id,
        status_envelope.event_id,
    ]

    async def _audit_row_count() -> int:
        rows = await db.fetch(
            "SELECT count(*) AS n FROM ops.audit_events WHERE event_id = ANY($1::text[])",
            chain_event_ids,
        )
        return int(rows[0]["n"]) if rows else 0

    audited = await _wait_for_rows(
        _audit_row_count, minimum=len(chain_event_ids), timeout_seconds=db_timeout_seconds
    )
    report.record(
        "audit-trail",
        audited >= len(chain_event_ids),
        f"{audited}/{len(chain_event_ids)} chain events in ops.audit_events",
    )

    # 7. Optional: live fill (Card 16).
    if wait_fill:
        fill_envelope: Envelope | None = None
        if status_stream == STREAM_ORDER_FILLED:
            fill_envelope = status_envelope
        else:

            def _is_our_fill(stream: str, envelope: Envelope) -> bool:
                if stream != STREAM_ORDER_FILLED:
                    return False
                return _payload_field(envelope, "broker_order_id") == broker_order_id

            fill = await _wait_for_event(redis, cutoffs, _is_our_fill, fill_timeout_seconds)
            fill_envelope = fill[1] if fill is not None else None
        if fill_envelope is None:
            report.record(
                "order-filled",
                False,
                f"no fill within {fill_timeout_seconds}s — market open? liquid symbol?",
            )
            return report
        report.record(
            "order-filled",
            True,
            f"filled_qty={_payload_field(fill_envelope, 'filled_qty')} "
            f"filled_avg_price={_payload_field(fill_envelope, 'filled_avg_price')}",
        )

        async def _fill_row_count() -> int:
            rows = await db.fetch(
                "SELECT count(*) AS n FROM trading.paper_order_events "
                "WHERE broker_order_id = $1 AND event_topic = $2",
                broker_order_id,
                STREAM_ORDER_FILLED,
            )
            return int(rows[0]["n"]) if rows else 0

        fill_rows = await _wait_for_rows(
            _fill_row_count, minimum=1, timeout_seconds=db_timeout_seconds
        )
        report.record(
            "fill-persisted",
            fill_rows >= 1,
            f"{fill_rows} fill rows persisted for broker_order_id={broker_order_id}",
        )

    # 8. Optional: reconciliation pass (Card 16).
    if wait_reconciliation:

        def _is_reconciliation(stream: str, envelope: Envelope) -> bool:
            return stream == STREAM_RECONCILIATION_COMPLETED

        reconciliation = await _wait_for_event(
            redis, cutoffs, _is_reconciliation, reconciliation_timeout_seconds
        )
        if reconciliation is None:
            report.record(
                "reconciliation",
                False,
                f"no reconciliation pass within {reconciliation_timeout_seconds}s — "
                "is the Reconciliation Agent running?",
            )
            return report
        _, reconciliation_envelope = reconciliation
        clean = _payload_field(reconciliation_envelope, "clean")
        discrepancies = _payload_field(reconciliation_envelope, "discrepancies")
        report.record(
            "reconciliation",
            clean is True,
            f"clean={clean} discrepancies={discrepancies}",
        )

    return report
