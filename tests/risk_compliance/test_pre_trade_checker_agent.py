from __future__ import annotations

import asyncio
from typing import Any

import fakeredis.aioredis
import pytest

from shrap.events import EventPublisher, PublishedEvent
from shrap.events.groups import GroupEventSubscriber
from shrap.risk_compliance.pre_trade import RiskPolicy

STREAM_INTENT = "trading.decision.intent"


class FakeRedis:
    """fakeredis transport with recorded ``xadd`` calls for assertions."""

    def __init__(self) -> None:
        self._real = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        return await self._real.xadd(stream, fields)

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",
        mkstream: bool = False,
    ) -> Any:
        return await self._real.xgroup_create(name, groupname, id=id, mkstream=mkstream)

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[Any, Any],
        count: int | None = None,
        block: int | None = None,
    ) -> Any:
        return await self._real.xreadgroup(
            groupname, consumername, streams, count=count, block=block
        )

    async def xack(self, name: str, groupname: str, *ids: str) -> Any:
        return await self._real.xack(name, groupname, *ids)


class FailRiskPublishRedis(FakeRedis):
    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        if stream.startswith("risk.intent."):
            raise RuntimeError("risk publish failed")
        return await super().xadd(stream, fields)


class ToggleFailRiskPublishRedis(FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.fail_risk_publish = False

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        if self.fail_risk_publish and stream.startswith("risk.intent."):
            raise RuntimeError("risk publish failed")
        return await super().xadd(stream, fields)


class StopAfterRiskDecisionsRedis(FakeRedis):
    def __init__(self, stop: asyncio.Event, target: int) -> None:
        super().__init__()
        self._stop = stop
        self._target = target

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        redis_id = await super().xadd(stream, fields)
        risk_decisions = sum(
            1 for written_stream, _ in self.calls if written_stream.startswith("risk.intent.")
        )
        if risk_decisions >= self._target:
            self._stop.set()
        return redis_id


def subscriber_for(redis: FakeRedis) -> GroupEventSubscriber:
    return GroupEventSubscriber(
        redis,  # type: ignore[arg-type]
        group="pre-trade-checker",
        start_id="0",
    )


async def publish_intent(redis: FakeRedis, payload: dict[str, Any]) -> PublishedEvent:
    return await EventPublisher(redis).publish(
        stream=STREAM_INTENT,
        produced_by="trading-floor/decision-maker-card-2-stub",
        schema_version="1.0.0",
        payload=payload,
        correlation_id="01KTESTSIGNAL0000000000000",
    )


def paper_intent(**overrides: object) -> dict[str, Any]:
    intent: dict[str, Any] = {
        "ticker": "AAPL",
        "side": "buy",
        "quantity": 3,
        "size_hint": 3,
        "urgency": "normal",
        "justification_text": "test intent",
        "expiry": "2026-06-08T21:00:00Z",
        "mode": "paper",
        "strategy_ids": [],
        "source": "decision-maker-card-2-stub",
    }
    intent.update(overrides)
    return intent


@pytest.mark.asyncio
async def test_poll_once_approves_intent_with_correlation_and_scaled_quantity() -> None:
    from shrap.risk_compliance.pre_trade_checker_agent import (
        STREAM_RISK_APPROVED,
        poll_once,
    )

    redis = FakeRedis()
    intent = await publish_intent(redis, paper_intent(quantity=3, size_hint=3))

    processed = await poll_once(
        redis,  # type: ignore[arg-type]
        RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=2),
        subscriber_for(redis),
        count=10,
        block_ms=1,
    )

    assert processed == 1
    assert redis.calls[-1][0] == STREAM_RISK_APPROVED
    risk = redis.calls[-1][1]
    assert risk["h_correlation_id"] == intent.envelope.event_id
    assert '"approved":true' in risk["payload"]
    assert '"approved_quantity":2' in risk["payload"]
    assert '"requested_quantity":3' in risk["payload"]
    assert '"intent_payload"' in risk["payload"]

    # The intent was acknowledged: a restarted consumer sees nothing pending.
    assert await subscriber_for(redis).read([STREAM_INTENT], block_ms=1) == []


@pytest.mark.asyncio
async def test_poll_once_vetoes_non_paper_mode_with_reason() -> None:
    from shrap.risk_compliance.pre_trade import REAL_MONEY_FORBIDDEN_REASON
    from shrap.risk_compliance.pre_trade_checker_agent import STREAM_RISK_VETOED, poll_once

    redis = FakeRedis()
    await publish_intent(redis, paper_intent(mode="live"))

    processed = await poll_once(
        redis,  # type: ignore[arg-type]
        RiskPolicy(allowed_universe={"AAPL"}),
        subscriber_for(redis),
        count=10,
        block_ms=1,
    )

    assert processed == 1
    assert redis.calls[-1][0] == STREAM_RISK_VETOED
    assert f'"reason":"{REAL_MONEY_FORBIDDEN_REASON}"' in redis.calls[-1][1]["payload"]


@pytest.mark.asyncio
async def test_poll_once_vetoes_universe_ineligible_with_reason() -> None:
    from shrap.risk_compliance.pre_trade_checker_agent import STREAM_RISK_VETOED, poll_once

    redis = FakeRedis()
    await publish_intent(redis, paper_intent(ticker="TSLA"))

    processed = await poll_once(
        redis,  # type: ignore[arg-type]
        RiskPolicy(allowed_universe={"AAPL"}),
        subscriber_for(redis),
        count=10,
        block_ms=1,
    )

    assert processed == 1
    assert redis.calls[-1][0] == STREAM_RISK_VETOED
    assert '"reason":"TICKER_NOT_IN_UNIVERSE"' in redis.calls[-1][1]["payload"]


@pytest.mark.asyncio
async def test_poll_once_vetoes_non_positive_quantity_with_reason() -> None:
    from shrap.risk_compliance.pre_trade_checker_agent import STREAM_RISK_VETOED, poll_once

    redis = FakeRedis()
    await publish_intent(redis, paper_intent(quantity=0, size_hint=0))

    processed = await poll_once(
        redis,  # type: ignore[arg-type]
        RiskPolicy(allowed_universe={"AAPL"}),
        subscriber_for(redis),
        count=10,
        block_ms=1,
    )

    assert processed == 1
    assert redis.calls[-1][0] == STREAM_RISK_VETOED
    assert '"reason":"INVALID_QUANTITY"' in redis.calls[-1][1]["payload"]


@pytest.mark.asyncio
async def test_reprocessing_same_intent_event_emits_same_decision_payload() -> None:
    from shrap.events import Envelope, ReceivedEvent
    from shrap.risk_compliance.pre_trade_checker_agent import process_intent_event

    redis = FakeRedis()
    await publish_intent(redis, paper_intent(quantity=1))
    received = ReceivedEvent(
        stream=STREAM_INTENT,
        redis_stream_id="1-0",
        envelope=Envelope.from_redis_fields(redis.calls[0][1]),
    )
    policy = RiskPolicy(allowed_universe={"AAPL"})

    first = await process_intent_event(redis, received, policy)  # type: ignore[arg-type]
    second = await process_intent_event(redis, received, policy)  # type: ignore[arg-type]

    assert first.envelope.payload == second.envelope.payload


@pytest.mark.asyncio
async def test_agent_processes_queued_intents_when_group_is_first_created() -> None:
    from shrap.risk_compliance.pre_trade_checker_agent import run_loop

    stop = asyncio.Event()
    redis = StopAfterRiskDecisionsRedis(stop=stop, target=3)
    for quantity in [1, 2, 3]:
        await publish_intent(redis, paper_intent(quantity=quantity, size_hint=quantity))

    await asyncio.wait_for(
        run_loop(
            redis,  # type: ignore[arg-type]
            RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=5),
            stop=stop,
            start_id="0",
            count=10,
            block_ms=1,
            retry_delay_seconds=0,
        ),
        timeout=1,
    )

    assert [stream for stream, _ in redis.calls].count("risk.intent.approved") == 3


@pytest.mark.asyncio
async def test_processed_intents_do_not_replay_after_restart() -> None:
    from shrap.risk_compliance.pre_trade_checker_agent import poll_once

    redis = FakeRedis()
    await publish_intent(redis, paper_intent(quantity=1))
    policy = RiskPolicy(allowed_universe={"AAPL"})

    assert await poll_once(redis, policy, subscriber_for(redis), count=10, block_ms=1) == 1  # type: ignore[arg-type]

    # Restart: fresh subscriber object, same group. The intent must not be
    # re-approved — this is the KI-006 fix.
    assert await poll_once(redis, policy, subscriber_for(redis), count=10, block_ms=1) == 0  # type: ignore[arg-type]
    assert [stream for stream, _ in redis.calls].count("risk.intent.approved") == 1


@pytest.mark.asyncio
async def test_publish_failure_leaves_intent_pending_for_retry() -> None:
    from shrap.risk_compliance.pre_trade_checker_agent import poll_once

    redis = FailRiskPublishRedis()
    await publish_intent(redis, paper_intent(quantity=1))

    processed = await poll_once(
        redis,  # type: ignore[arg-type]
        RiskPolicy(allowed_universe={"AAPL"}),
        subscriber_for(redis),
        count=10,
        block_ms=1,
    )

    assert processed == 0
    # Not acked: a restarted consumer is redelivered the same intent.
    redelivered = await subscriber_for(redis).read([STREAM_INTENT], block_ms=1)
    assert len(redelivered) == 1


@pytest.mark.asyncio
async def test_invalid_payload_is_acked_and_skipped_without_blocking_later_intents() -> None:
    from shrap.events import Envelope
    from shrap.risk_compliance.pre_trade_checker_agent import poll_once

    redis = FakeRedis()
    # Valid envelope the checker cannot process: payload_ref instead of the
    # inline payload the risk gate requires.
    poison = Envelope.new(
        produced_by="test",
        schema_version="1.0.0",
        payload={"placeholder": True},
    )
    fields = poison.to_redis_fields()
    fields.pop("payload")
    fields["payload_ref"] = "qdrant://poison"
    await redis.xadd(STREAM_INTENT, fields)
    await publish_intent(redis, paper_intent(quantity=1))

    processed = await poll_once(
        redis,  # type: ignore[arg-type]
        RiskPolicy(allowed_universe={"AAPL"}),
        subscriber_for(redis),
        count=10,
        block_ms=1,
    )

    # The poison intent is skipped; the valid one behind it is still processed.
    assert processed == 1
    assert [stream for stream, _ in redis.calls].count("risk.intent.approved") == 1
    assert await subscriber_for(redis).read([STREAM_INTENT], block_ms=1) == []


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_publish_failure() -> None:
    from shrap.risk_compliance.pre_trade_checker_agent import poll_once

    redis = ToggleFailRiskPublishRedis()
    await publish_intent(redis, paper_intent(quantity=1))
    policy = RiskPolicy(allowed_universe={"AAPL"})
    subscriber = subscriber_for(redis)

    assert await poll_once(redis, policy, subscriber, count=1, block_ms=1) == 1  # type: ignore[arg-type]

    await publish_intent(redis, paper_intent(quantity=2, size_hint=2))
    redis.fail_risk_publish = True
    assert await poll_once(redis, policy, subscriber, count=1, block_ms=1) == 0  # type: ignore[arg-type]

    # Outage over: the pending intent is redelivered and processed exactly once.
    redis.fail_risk_publish = False
    assert await poll_once(redis, policy, subscriber, count=1, block_ms=1) == 1  # type: ignore[arg-type]
    assert [stream for stream, _ in redis.calls].count("risk.intent.approved") == 2


@pytest.mark.asyncio
async def test_process_intent_event_preserves_original_intent_payload() -> None:
    from shrap.events import Envelope, ReceivedEvent
    from shrap.risk_compliance.pre_trade_checker_agent import process_intent_event

    redis = FakeRedis()
    intent = await publish_intent(redis, paper_intent(quantity=3, size_hint=3))
    received = ReceivedEvent(
        stream=STREAM_INTENT,
        redis_stream_id="1-0",
        envelope=Envelope.from_redis_fields(redis.calls[0][1]),
    )

    published = await process_intent_event(
        redis,  # type: ignore[arg-type]
        received,
        RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=2),
    )

    assert published.envelope.correlation_id == intent.envelope.event_id
    assert published.envelope.payload is not None
    assert published.envelope.payload["intent_payload"]["quantity"] == 3
    assert published.envelope.payload["approved_intent_payload"]["quantity"] == 2


@pytest.mark.asyncio
async def test_run_loop_exits_cleanly_when_stop_signal_is_set() -> None:
    from shrap.risk_compliance.pre_trade_checker_agent import run_loop

    redis = FakeRedis()
    stop = asyncio.Event()

    async def stop_soon() -> None:
        await asyncio.sleep(0)
        stop.set()

    await asyncio.gather(
        run_loop(
            redis,  # type: ignore[arg-type]
            RiskPolicy(allowed_universe={"AAPL"}),
            stop=stop,
            start_id="0",
            count=10,
            block_ms=1,
            retry_delay_seconds=0,
        ),
        stop_soon(),
    )

    assert stop.is_set()
