"""Hand-crafted paper signal smoke path.

This module intentionally stops before broker order submission. It publishes the same
kind of intent and risk-decision events the later live inner loop will use, so the
Operations substrate can audit the path before any Alpaca order leaves the system.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shrap.events import EventPublisher, PublishedEvent, RedisPublisher
from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy
from shrap.trading_floor.intent import build_handcrafted_intent

STREAM_INTENT = "trading.decision.intent"
STREAM_RISK_APPROVED = "risk.intent.approved"
STREAM_RISK_VETOED = "risk.intent.vetoed"
SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class PaperSmokeResult:
    """Events published for one manual paper-signal smoke."""

    intent: PublishedEvent
    risk_decision: PublishedEvent


async def publish_handcrafted_signal(
    redis: RedisPublisher,
    produced_by: str,
    ticker: str,
    side: str,
    quantity: int,
    strategy_id: str,
    justification: str,
    policy: RiskPolicy,
) -> PaperSmokeResult:
    """Publish a hand-crafted paper intent and deterministic pre-trade decision."""
    publisher = EventPublisher(redis)
    intent_payload = build_handcrafted_intent(
        ticker=ticker,
        side=side,
        quantity=quantity,
        strategy_id=strategy_id,
        justification=justification,
    )
    intent_event = await publisher.publish(
        stream=STREAM_INTENT,
        produced_by=produced_by,
        schema_version=SCHEMA_VERSION,
        payload=intent_payload,
    )

    decision = PreTradeChecker(policy).check(intent_payload)
    decision_payload: dict[str, Any] = decision.to_event_payload()
    decision_payload["intent_event_id"] = intent_event.envelope.event_id
    decision_payload["strategy_ids"] = intent_payload["strategy_ids"]

    decision_event = await publisher.publish(
        stream=STREAM_RISK_APPROVED if decision.approved else STREAM_RISK_VETOED,
        produced_by="risk/pre-trade-checker",
        schema_version=SCHEMA_VERSION,
        payload=decision_payload,
        correlation_id=intent_event.envelope.event_id,
    )
    return PaperSmokeResult(intent=intent_event, risk_decision=decision_event)
