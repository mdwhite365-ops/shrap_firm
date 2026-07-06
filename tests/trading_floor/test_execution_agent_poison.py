"""Tests for poison-event handling in the Execution Agent's risk loop.

Root cause found live 2026-07-06: a container restart replays
risk.intent.approved from start_id 0-0, re-submitting orders whose
client_order_id already exists at Alpaca (422). The loop then broke without
advancing past the failed event, retrying it forever and never reaching new
approved intents — every subsequent spine smoke failed at order-submitted.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from shrap.events import EventPublisher
from shrap.trading_floor.execution_agent import is_duplicate_order_error, poll_once


def _duplicate_error() -> httpx.HTTPStatusError:
    response = httpx.Response(
        422,
        json={"code": 40010001, "message": "client_order_id must be unique"},
        request=httpx.Request("POST", "https://paper-api.alpaca.markets/v2/orders"),
    )
    return httpx.HTTPStatusError("422", request=response.request, response=response)


def test_duplicate_order_error_detection() -> None:
    assert is_duplicate_order_error(_duplicate_error())

    other_422 = httpx.Response(
        422,
        json={"message": "insufficient buying power"},
        request=httpx.Request("POST", "https://paper-api.alpaca.markets/v2/orders"),
    )
    assert not is_duplicate_order_error(
        httpx.HTTPStatusError("422", request=other_422.request, response=other_422)
    )

    forbidden = httpx.Response(
        403,
        json={"message": "forbidden"},
        request=httpx.Request("POST", "https://paper-api.alpaca.markets/v2/orders"),
    )
    assert not is_duplicate_order_error(
        httpx.HTTPStatusError("403", request=forbidden.request, response=forbidden)
    )
    assert not is_duplicate_order_error(RuntimeError("network down"))


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        return f"178012860000{len(self.calls)}-0"

    async def xread(
        self,
        streams: dict[Any, Any],
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        response: list[tuple[str, list[tuple[str, dict[str, str]]]]] = []
        for stream, last_id in streams.items():
            entries = [
                (f"178012860000{index}-0", fields)
                for index, (written_stream, fields) in enumerate(self.calls, start=1)
                if written_stream == stream and f"178012860000{index}-0" > str(last_id)
            ]
            if entries:
                response.append((str(stream), entries[: count or len(entries)]))
        return response


class ScriptedBroker:
    """submit_order responses/errors served in order."""

    def __init__(self, outcomes: list[dict[str, Any] | Exception]) -> None:
        self._outcomes = outcomes
        self.submissions: list[dict[str, Any]] = []

    async def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        self.submissions.append(order)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def get_order(self, order_id: str) -> dict[str, Any]:
        raise AssertionError("not used")


def _approved_payload(intent_id: str) -> dict[str, Any]:
    return {
        "approved": True,
        "intent_event_id": intent_id,
        "approved_intent_payload": {
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 1,
            "mode": "paper",
        },
    }


async def _publish_approved(redis: FakeRedis, intent_id: str) -> None:
    await EventPublisher(redis).publish(
        stream="risk.intent.approved",
        produced_by="risk/pre-trade-checker",
        schema_version="1.0.0",
        payload=_approved_payload(intent_id),
    )


@pytest.mark.asyncio
async def test_duplicate_replay_is_skipped_and_new_intent_still_submits() -> None:
    redis = FakeRedis()
    await _publish_approved(redis, "intent-old")  # replayed after restart -> 422
    await _publish_approved(redis, "intent-new")  # must still be submitted

    broker = ScriptedBroker(
        [
            _duplicate_error(),
            {"id": "order-new", "status": "accepted"},
        ]
    )
    last_ids: dict[str, str] = {}

    processed = await poll_once(
        redis=redis,  # type: ignore[arg-type]
        broker=broker,
        last_ids=last_ids,
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert processed == 1  # only the new intent counts as processed
    assert len(broker.submissions) == 2
    submitted_streams = [stream for stream, _ in redis.calls if stream.startswith("execution.")]
    assert submitted_streams == ["execution.order.submitted"]
    # The poisoned event was skipped: offsets are past BOTH events.
    assert last_ids["risk.intent.approved"] == "1780128600002-0"


@pytest.mark.asyncio
async def test_malformed_approved_event_is_skipped() -> None:
    redis = FakeRedis()
    await EventPublisher(redis).publish(
        stream="risk.intent.approved",
        produced_by="risk/pre-trade-checker",
        schema_version="1.0.0",
        payload={"approved": True},  # missing approved_intent_payload -> ValueError
    )
    await _publish_approved(redis, "intent-good")

    broker = ScriptedBroker([{"id": "order-good", "status": "accepted"}])
    last_ids: dict[str, str] = {}

    processed = await poll_once(
        redis=redis,  # type: ignore[arg-type]
        broker=broker,
        last_ids=last_ids,
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert processed == 1
    assert len(broker.submissions) == 1  # malformed event never reached the broker
    assert last_ids["risk.intent.approved"] == "1780128600002-0"


@pytest.mark.asyncio
async def test_systemic_broker_error_still_retries_same_event() -> None:
    redis = FakeRedis()
    await _publish_approved(redis, "intent-1")

    broker = ScriptedBroker([RuntimeError("broker unreachable")])
    last_ids: dict[str, str] = {}

    processed = await poll_once(
        redis=redis,  # type: ignore[arg-type]
        broker=broker,
        last_ids=last_ids,
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert processed == 0
    # Offset NOT advanced: the event must retry next cycle, not be dropped.
    assert last_ids["risk.intent.approved"] == "0-0"
