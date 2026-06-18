"""End-to-end paper spine smoke harness.

This module is intentionally small and test-oriented. It exercises the Month 1
paper path with injected Redis and broker clients, proving the event contracts
compose without requiring live Alpaca credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shrap.events import EventPublisher, PublishedEvent, ReceivedEvent, RedisPublisher
from shrap.risk_compliance.pre_trade import RiskPolicy
from shrap.risk_compliance.pre_trade_checker_agent import process_intent_event
from shrap.trading_floor.decision_maker_stub import (
    SCHEMA_VERSION,
    STREAM_STRATEGY_SIGNAL,
    DecisionMakerStub,
)
from shrap.trading_floor.execution_agent import (
    PaperBroker,
    process_order_status_event,
    process_risk_event,
)


@dataclass(frozen=True, slots=True)
class PaperSpineSmokeResult:
    """Published events for one complete paper-spine smoke."""

    strategy_signal: PublishedEvent
    decision_intent: PublishedEvent
    risk_decision: PublishedEvent
    order_submitted: PublishedEvent
    order_status: PublishedEvent


def _as_received(event: PublishedEvent) -> ReceivedEvent:
    return ReceivedEvent(
        stream=event.stream,
        redis_stream_id=event.redis_stream_id,
        envelope=event.envelope,
    )


async def run_handcrafted_paper_spine_smoke(
    redis: RedisPublisher,
    broker: PaperBroker,
    signal: dict[str, Any],
    risk_policy: RiskPolicy,
    produced_by: str = "trading-floor/paper-spine-smoke",
) -> PaperSpineSmokeResult:
    """Run one signal -> decision -> risk -> execution -> status paper smoke.

    The signal payload is expected to match the Card 2 Decision Maker stub input
    shape. The broker is injected so unit/integration tests can prove the full
    event spine without live credentials.
    """

    publisher = EventPublisher(redis)
    strategy_signal = await publisher.publish(
        stream=STREAM_STRATEGY_SIGNAL,
        produced_by=produced_by,
        schema_version=SCHEMA_VERSION,
        payload=signal,
    )

    decision_result = await DecisionMakerStub(redis).process_once(last_id="0-0")  # type: ignore[arg-type]
    if decision_result is None:
        raise RuntimeError("Decision Maker stub did not emit an intent")
    decision_intent = decision_result.intent

    risk_decision = await process_intent_event(
        redis,  # type: ignore[arg-type]
        _as_received(decision_intent),
        risk_policy,
    )
    if risk_decision.stream != "risk.intent.approved":
        raise RuntimeError(f"Risk gate did not approve smoke intent: {risk_decision.stream}")

    order_submitted = await process_risk_event(
        redis,  # type: ignore[arg-type]
        broker,
        _as_received(risk_decision),
    )
    order_status = await process_order_status_event(
        redis,  # type: ignore[arg-type]
        broker,
        _as_received(order_submitted),
    )

    return PaperSpineSmokeResult(
        strategy_signal=strategy_signal,
        decision_intent=decision_intent,
        risk_decision=risk_decision,
        order_submitted=order_submitted,
        order_status=order_status,
    )
