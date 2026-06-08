"""Month 1 Card 2 Decision Maker wire-only stub.

This intentionally is not the full Decision Maker. It proves the
``trading.strategy.signal`` -> ``trading.decision.intent`` wire and locks the
Card 2 intent payload shape for downstream Risk/Execution cards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from shrap.events import EventPublisher, EventSubscriber, PublishedEvent, ReceivedEvent


class RedisStreamClient(Protocol):
    async def xadd(self, stream: str, fields: dict[str, str]) -> str: ...

    async def xread(
        self,
        streams: dict[Any, Any],
        count: int | None = None,
        block: int | None = None,
    ) -> Any: ...


STREAM_STRATEGY_SIGNAL = "trading.strategy.signal"
STREAM_DECISION_INTENT = "trading.decision.intent"
PRODUCED_BY = "trading-floor/decision-maker-card-2-stub"
SCHEMA_VERSION = "1.0.0"
DEFAULT_CONFIDENCE_THRESHOLD = 0.7
DEFAULT_EXPIRY = "unknown"
DEFAULT_URGENCY = "normal"


@dataclass(frozen=True, slots=True)
class DecisionMakerStubResult:
    """Events involved in one emitted Card 2 stub intent."""

    source_signal: ReceivedEvent
    intent: PublishedEvent


def _confidence(signal: dict[str, Any]) -> float:
    return float(signal.get("confidence", 0.0))


def should_emit_stub_intent(
    signal: dict[str, Any],
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> bool:
    """Return True when the placeholder pass-through rule accepts a signal."""

    return _confidence(signal) > threshold


def build_stub_intent(
    signal: dict[str, Any],
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> dict[str, Any]:
    """Build the stable Card 2 ``trading.decision.intent`` payload.

    Placeholder fields are present so Cards 3-5 can rely on field existence,
    but their values are not meaningful until their upstream producers exist.
    """

    ticker = str(signal.get("ticker", "")).strip().upper()
    if not ticker:
        raise ValueError("ticker is required")

    side = str(signal.get("side", "")).strip().lower()
    if side not in {"buy", "sell"}:
        raise ValueError("side must be one of ['buy', 'sell']")

    size_hint = int(signal.get("size_hint", signal.get("quantity", 0)))
    if size_hint <= 0:
        raise ValueError("size_hint must be positive")

    confidence = _confidence(signal)
    urgency = str(signal.get("urgency", DEFAULT_URGENCY)).strip() or DEFAULT_URGENCY
    expiry = str(signal.get("expiry", DEFAULT_EXPIRY)).strip() or DEFAULT_EXPIRY
    mode = str(signal.get("mode", "paper")).strip() or "paper"

    return {
        "ticker": ticker,
        "side": side,
        "size_hint": size_hint,
        "quantity": size_hint,
        "urgency": urgency,
        "justification_text": (
            "Card 2 Decision Maker stub passed through trading.strategy.signal "
            f"because confidence {confidence:.2f} exceeded threshold {threshold:.2f}. "
            "No real confluence policy, LLM synthesis, or EXTREME-block applied."
        ),
        "expiry": expiry,
        "mode": mode,
        "strategy_ids": [],
        "regime_label": "unknown",
        "structural_bias": "neutral",
        "intel_refs": [],
        "confluence_score": 0.0,
        "source": "decision-maker-card-2-stub",
        "stub_threshold": threshold,
    }


class DecisionMakerStub:
    """Wire-only Card 2 agent that emits at most one intent per call."""

    def __init__(
        self,
        redis: RedisStreamClient,
        threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        produced_by: str = PRODUCED_BY,
    ) -> None:
        self._subscriber = EventSubscriber(redis)
        self._publisher = EventPublisher(redis)
        self._threshold = threshold
        self._produced_by = produced_by

    async def process_once(self, last_id: str = "$") -> DecisionMakerStubResult | None:
        """Read one strategy signal batch and publish the first accepted intent.

        Low-confidence signals are skipped silently in Card 2. Full skipped-event
        accounting belongs to the Month 2 Decision Maker implementation.
        """

        events = await self._subscriber.read(
            streams={STREAM_STRATEGY_SIGNAL: last_id},
            count=1,
            block_ms=0,
        )
        for event in events:
            signal = event.envelope.payload
            if signal is None or not should_emit_stub_intent(signal, self._threshold):
                continue
            intent_payload = build_stub_intent(signal, self._threshold)
            intent_event = await self._publisher.publish(
                stream=STREAM_DECISION_INTENT,
                produced_by=self._produced_by,
                schema_version=SCHEMA_VERSION,
                payload=intent_payload,
                correlation_id=event.envelope.event_id,
            )
            return DecisionMakerStubResult(source_signal=event, intent=intent_event)
        return None
