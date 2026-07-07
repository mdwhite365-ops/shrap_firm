"""Deterministic strategy fixture — the firm's first autonomous signal source.

This is NOT a trading strategy. Its job is to exercise the live pipeline:
publish at most one regime-gated ``trading.strategy.signal`` per UTC day so
the deployed Decision Maker stub, Pre-Trade Checker (with rate guardrails),
Execution Agent, and reconciliation loop can be tested end to end during
live sessions without a human injecting intents.

Safety properties, in order of enforcement:
1. Disabled by default (``STRATEGY_FIXTURE_ENABLED=false``).
2. Regime gate: fires only when the latest ``intel.regime.sizing-modifier``
   label is in the allowed set.
3. Own daily limit via Redis SET NX (default 1 signal/day, restart-proof).
4. Everything downstream still applies: Decision Maker confidence threshold,
   pre-trade policy, and the firm-wide rate guardrails.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from shrap.events import Envelope, EventPublisher, PublishedEvent, normalize_redis_fields

log = structlog.get_logger(__name__)

STREAM_STRATEGY_SIGNAL = "trading.strategy.signal"
STREAM_REGIME_SIZING_MODIFIER = "intel.regime.sizing-modifier"
PRODUCED_BY = "research/strategy-fixture"
SCHEMA_VERSION = "1.0.0"
STRATEGY_ID = "fixture-regime-gated-v0"

_FIRED_KEY_TTL_SECONDS = 2 * 24 * 3600


class FixtureRedis(Protocol):
    async def xadd(self, stream: str, fields: dict[str, str]) -> str: ...

    async def xrevrange(
        self, name: str, max: str = "+", min: str = "-", count: int | None = None
    ) -> Any: ...

    async def set(
        self,
        name: str,
        value: str,
        nx: bool = False,
        ex: int | None = None,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class FixtureConfig:
    """Knobs for the fixture. All conservative by default."""

    ticker: str = "SPY"
    side: str = "buy"
    quantity: int = 1
    allowed_regime_labels: tuple[str, ...] = ("crisis-recovery", "late-cycle-melt-up")
    max_signals_per_day: int = 1
    confidence: float = 0.99  # above the Decision Maker stub threshold by design


async def latest_regime_label(redis: FixtureRedis) -> str | None:
    """Read the label from the newest sizing-modifier event, if any."""

    entries = await redis.xrevrange(STREAM_REGIME_SIZING_MODIFIER, count=1)
    if not entries:
        return None
    _, fields = entries[0]
    try:
        envelope = Envelope.from_redis_fields(normalize_redis_fields(fields))
    except Exception:
        log.warning("strategy_fixture.malformed_regime_event_skipped")
        return None
    if envelope.payload is None:
        return None
    label = envelope.payload.get("label")
    return str(label) if label is not None else None


async def _claim_daily_slot(redis: FixtureRedis, max_per_day: int) -> bool:
    """Claim one of today's signal slots; False when exhausted."""

    today = datetime.now(UTC).date().isoformat()
    for slot in range(max(1, max_per_day)):
        claimed = await redis.set(
            f"strategy-fixture:fired:{today}:{slot}",
            "1",
            nx=True,
            ex=_FIRED_KEY_TTL_SECONDS,
        )
        if claimed:
            return True
    return False


async def fire_once(
    redis: FixtureRedis,
    config: FixtureConfig,
    produced_by: str = PRODUCED_BY,
) -> PublishedEvent | None:
    """Run one fixture pass; publish at most one signal.

    Returns the published event, or None with the reason logged.
    """

    label = await latest_regime_label(redis)
    if label is None:
        log.info("strategy_fixture.no_regime_label_yet")
        return None
    if label not in config.allowed_regime_labels:
        log.info(
            "strategy_fixture.regime_gate_closed",
            label=label,
            allowed=list(config.allowed_regime_labels),
        )
        return None
    if not await _claim_daily_slot(redis, config.max_signals_per_day):
        log.info("strategy_fixture.daily_limit_reached", max_per_day=config.max_signals_per_day)
        return None

    event = await EventPublisher(redis).publish(
        stream=STREAM_STRATEGY_SIGNAL,
        produced_by=produced_by,
        schema_version=SCHEMA_VERSION,
        payload={
            "strategy_id": STRATEGY_ID,
            "ticker": config.ticker.upper(),
            "side": config.side.lower(),
            "size_hint": config.quantity,
            "quantity": config.quantity,
            "confidence": config.confidence,
            "urgency": "normal",
            "regime_label": label,
            "justification_text": (
                "Deterministic pipeline-exercise fixture, not an alpha signal. "
                f"Fired because regime label '{label}' is in the allowed set. "
                "Why this might be wrong: it carries no market view at all."
            ),
        },
    )
    log.info(
        "strategy_fixture.signal_published",
        ticker=config.ticker.upper(),
        regime_label=label,
        event_id=event.envelope.event_id,
    )
    return event
