"""Tests for poison-event handling in the Execution Agent's risk loop.

Root cause found live 2026-07-06: a container restart replays
risk.intent.approved from start_id 0-0, re-submitting orders whose
client_order_id already exists at Alpaca (422). The loop then broke without
advancing past the failed event, retrying it forever and never reaching new
approved intents — every subsequent spine smoke failed at order-submitted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import fakeredis.aioredis
import httpx
import pytest

from shrap.events import Envelope, EventPublisher, ReceivedEvent
from shrap.events.groups import GroupEventSubscriber
from shrap.trading_floor.execution_agent import (
    AlpacaPaperBroker,
    build_paper_order,
    is_duplicate_order_error,
    is_unknown_order_error,
    poll_once,
    poll_order_status_once,
)


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


def subscriber_for(redis: FakeRedis) -> GroupEventSubscriber:
    return GroupEventSubscriber(
        redis,  # type: ignore[arg-type]
        group="execution-agent",
        start_id="0",
    )


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

    async def is_fractionable(self, symbol: str) -> bool:
        # Fractional by default in tests; the non-fractionable path
        # has its own dedicated cases.
        return True

    async def get_order(self, order_id: str) -> dict[str, Any]:
        raise AssertionError("not used")


def _approved_payload(intent_id: str) -> dict[str, Any]:
    return {
        "approved": True,
        "intent_event_id": intent_id,
        "approved_intent_payload": {
            "ticker": "AAPL",
            "account_id": "PA3TESTACCT",
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

    processed = await poll_once(
        redis=redis,  # type: ignore[arg-type]
        broker=broker,
        subscriber=subscriber_for(redis),
        count=10,
        block_ms=1,
        account_id="PA3TESTACCT",
    )

    assert processed == 1  # only the new intent counts as processed
    assert len(broker.submissions) == 2
    submitted_streams = [stream for stream, _ in redis.calls if stream.startswith("execution.")]
    assert submitted_streams == ["execution.order.submitted"]
    # The poisoned event was skipped: both events acked, nothing redelivered.
    assert await subscriber_for(redis).read(["risk.intent.approved"], block_ms=1) == []


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

    processed = await poll_once(
        redis=redis,  # type: ignore[arg-type]
        broker=broker,
        subscriber=subscriber_for(redis),
        count=10,
        block_ms=1,
        account_id="PA3TESTACCT",
    )

    assert processed == 1
    assert len(broker.submissions) == 1  # malformed event never reached the broker
    assert await subscriber_for(redis).read(["risk.intent.approved"], block_ms=1) == []


@pytest.mark.asyncio
async def test_systemic_broker_error_still_retries_same_event() -> None:
    redis = FakeRedis()
    await _publish_approved(redis, "intent-1")

    broker = ScriptedBroker([RuntimeError("broker unreachable")])

    processed = await poll_once(
        redis=redis,  # type: ignore[arg-type]
        broker=broker,
        subscriber=subscriber_for(redis),
        count=10,
        block_ms=1,
        account_id="PA3TESTACCT",
    )

    assert processed == 0
    # NOT acked: the event must be redelivered next cycle, not dropped.
    redelivered = await subscriber_for(redis).read(["risk.intent.approved"], block_ms=1)
    assert len(redelivered) == 1


# --- The status loop: the same defect class, found live 2026-08-04 ------------
#
# The risk loop above was hardened in July. The status loop had both halves of
# the same bug and nobody looked: it claimed events it could not own, and it
# treated the resulting 404 as retryable. Result was a month-long stall on the
# firm's first order (stream id 1783203414014-0, 2026-07-04), with six real
# fills queued behind it and recorded nowhere.


def _not_found_error(order_id: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://paper-api.alpaca.markets/v2/orders/{order_id}")
    response = httpx.Response(404, json={"message": "order not found"}, request=request)
    return httpx.HTTPStatusError("404", request=request, response=response)


def test_unknown_order_error_detection() -> None:
    assert is_unknown_order_error(_not_found_error("order-x"))

    # Every other HTTP failure is transient and must keep its retry. Widening
    # the predicate would drop live orders during an outage.
    for code in (401, 429, 500, 503):
        request = httpx.Request("GET", "https://paper-api.alpaca.markets/v2/orders/order-x")
        response = httpx.Response(code, request=request)
        assert not is_unknown_order_error(
            httpx.HTTPStatusError(str(code), request=request, response=response)
        )
    assert not is_unknown_order_error(RuntimeError("network down"))


class StatusBroker:
    """get_order outcomes keyed by broker order id; records what was asked."""

    def __init__(self, outcomes: dict[str, dict[str, Any] | Exception]) -> None:
        self._outcomes = outcomes
        self.asked: list[str] = []

    async def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("not used")

    async def is_fractionable(self, symbol: str) -> bool:
        # Fractional by default in tests; the non-fractionable path
        # has its own dedicated cases.
        return True

    async def get_order(self, order_id: str) -> dict[str, Any]:
        self.asked.append(order_id)
        outcome = self._outcomes[order_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def _publish_submitted(
    redis: FakeRedis,
    broker_order_id: str,
    account_id: str | None,
) -> None:
    """Publish one execution.order.submitted event.

    ``account_id=None`` reproduces a pre-#fcf8d90 event, which carried no
    account key at all rather than an empty one.
    """

    payload: dict[str, Any] = {
        "broker": "alpaca-paper",
        "broker_order_id": broker_order_id,
        "status": "accepted",
        "submitted_order": {"symbol": "AAPL", "qty": "1"},
        "broker_response": {"id": broker_order_id, "status": "accepted"},
    }
    if account_id is not None:
        payload["account_id"] = account_id
    await EventPublisher(redis).publish(
        stream="execution.order.submitted",
        produced_by="trading-floor/execution-agent",
        schema_version="1.0.0",
        payload=payload,
    )


@pytest.mark.asyncio
async def test_an_unstamped_event_is_skipped_rather_than_claimed() -> None:
    """No account on the event means no agent owns it — not "everyone does"."""

    redis = FakeRedis()
    await _publish_submitted(redis, "order-legacy", account_id=None)
    await _publish_submitted(redis, "order-mine", account_id="PA3TESTACCT")

    broker = StatusBroker({"order-mine": {"id": "order-mine", "status": "filled"}})

    processed = await poll_order_status_once(
        redis=redis,  # type: ignore[arg-type]
        broker=broker,
        subscriber=subscriber_for(redis),
        count=10,
        block_ms=1,
        account_id="PA3TESTACCT",
    )

    assert processed == 1
    # The legacy event never reached the broker, so it never 404ed.
    assert broker.asked == ["order-mine"]
    assert [s for s, _ in redis.calls if s.startswith("execution.order.f")] == [
        "execution.order.filled"
    ]
    assert await subscriber_for(redis).read(["execution.order.submitted"], block_ms=1) == []


@pytest.mark.asyncio
async def test_a_404_is_permanent_and_the_batch_advances_past_it() -> None:
    """An order the broker has never heard of will not appear on retry."""

    redis = FakeRedis()
    await _publish_submitted(redis, "order-gone", account_id="PA3TESTACCT")
    await _publish_submitted(redis, "order-live", account_id="PA3TESTACCT")

    broker = StatusBroker(
        {
            "order-gone": _not_found_error("order-gone"),
            "order-live": {"id": "order-live", "status": "filled"},
        }
    )

    processed = await poll_order_status_once(
        redis=redis,  # type: ignore[arg-type]
        broker=broker,
        subscriber=subscriber_for(redis),
        count=10,
        block_ms=1,
        account_id="PA3TESTACCT",
    )

    assert processed == 1
    # Both asked, in order: the 404 did not break the batch. This is the
    # assertion that would have failed for the last month.
    assert broker.asked == ["order-gone", "order-live"]
    assert await subscriber_for(redis).read(["execution.order.submitted"], block_ms=1) == []


@pytest.mark.asyncio
async def test_a_systemic_error_in_the_status_loop_still_retries() -> None:
    """The no-ack branch must survive: a broker outage is not a poison pill."""

    redis = FakeRedis()
    await _publish_submitted(redis, "order-1", account_id="PA3TESTACCT")

    broker = StatusBroker({"order-1": RuntimeError("broker unreachable")})

    processed = await poll_order_status_once(
        redis=redis,  # type: ignore[arg-type]
        broker=broker,
        subscriber=subscriber_for(redis),
        count=10,
        block_ms=1,
        account_id="PA3TESTACCT",
    )

    assert processed == 0
    redelivered = await subscriber_for(redis).read(["execution.order.submitted"], block_ms=1)
    assert len(redelivered) == 1


# --- Fractional quantities (2026-08-09) ---------------------------------------
#
# The firm floored every order twice: once in the Runner turning a notional slot
# into shares, once in the Risk Officer scaling that share count. 26 of 89 live
# decisions died SIZED_TO_ZERO, every one a request under six shares, and the
# survivors were systematically the cheap names. Quantities are fractional now,
# and the only floor left is the one a non-fractionable asset forces.


def _intent_event(quantity: float, ticker: str = "AAPL") -> ReceivedEvent:
    return ReceivedEvent(
        stream="risk.intent.approved",
        redis_stream_id="1783203414014-0",
        envelope=Envelope(
            event_id="01APPROVED",
            schema_version="1.0.0",
            produced_at=datetime.now(UTC),
            produced_by="risk/pre-trade-checker",
            payload={
                "approved": True,
                "approved_intent_payload": {
                    "ticker": ticker,
                    "account_id": "PA3TESTACCT",
                    "side": "buy",
                    "quantity": quantity,
                    "mode": "paper",
                },
            },
        ),
    )


def test_a_fractionable_asset_keeps_its_fraction() -> None:
    order = build_paper_order(_intent_event(0.75), fractionable=True)
    assert order["qty"] == "0.75"


def test_a_whole_quantity_still_reads_as_a_whole_number() -> None:
    """9.0 must not reach the broker or the order table as "9.000000000"."""

    order = build_paper_order(_intent_event(9.0), fractionable=True)
    assert order["qty"] == "9"


def test_a_tiny_quantity_is_decimal_not_scientific_notation() -> None:
    """str(1e-07) is '1e-07', which Alpaca does not accept as a quantity."""

    order = build_paper_order(_intent_event(0.0000001), fractionable=True)
    assert order["qty"] == "0.0000001"


def test_a_non_fractionable_asset_is_floored_not_rejected() -> None:
    order = build_paper_order(_intent_event(3.9), fractionable=False)
    assert order["qty"] == "3"


def test_a_non_fractionable_asset_under_one_share_refuses_permanently() -> None:
    """ValueError, deliberately: it routes to the ack-and-skip branch.

    The venue will never accept 0.75 of this asset, so retrying is the poison
    pill this module exists to prevent.
    """

    with pytest.raises(ValueError, match="not fractionable"):
        build_paper_order(_intent_event(0.75), fractionable=False)


def test_quantity_is_floored_to_broker_precision_never_rounded_up() -> None:
    """Rounding up ships more than the Risk Officer approved."""

    order = build_paper_order(_intent_event(1.9999999999), fractionable=True)
    assert order["qty"] == "1.999999999"


# --- Fractionable cache provenance -------------------------------------------
#
# A cached False from a transient lookup failure and a legitimately
# non-fractionable asset produce identical orders and identical fills. Without
# provenance on the log line the two are indistinguishable after the fact, and
# the wrong one is worth chasing.


class RecordingLog:
    """Captures structlog-style kwargs for assertion."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def exception(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def error(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))


class FakeAssetClient:
    def __init__(self, outcome: dict[str, Any] | Exception) -> None:
        self._outcome = outcome
        self.calls: list[str] = []

    async def get_asset(self, http_client: Any, symbol: str) -> dict[str, Any]:
        self.calls.append(symbol)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _broker_with(outcome: dict[str, Any] | Exception) -> tuple[Any, FakeAssetClient, RecordingLog]:
    broker = AlpacaPaperBroker.__new__(AlpacaPaperBroker)
    client = FakeAssetClient(outcome)
    broker._client = client  # type: ignore[attr-defined]
    broker._http_client = None  # type: ignore[attr-defined]
    broker._fractionable = {}  # type: ignore[attr-defined]
    recorder = RecordingLog()
    return broker, client, recorder


@pytest.mark.asyncio
async def test_a_broker_answer_is_logged_with_its_source_and_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker, client, recorder = _broker_with({"fractionable": True})
    monkeypatch.setattr("shrap.trading_floor.execution_agent.log", recorder)

    assert await broker.is_fractionable("aapl") is True
    # Second call is served from cache: no second broker hit, no second log.
    assert await broker.is_fractionable("AAPL") is True
    assert client.calls == ["AAPL"]

    (event, kw) = recorder.events[0]
    assert event == "execution_agent.fractionable_resolved"
    assert kw["symbol"] == "AAPL"
    assert kw["fractionable"] is True
    assert kw["source"] == "broker"
    assert kw["cached"] is True
    assert len(recorder.events) == 1


@pytest.mark.asyncio
async def test_a_failed_lookup_is_distinguishable_from_a_real_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: both answer False, and only the log tells them apart."""

    broker, _client, recorder = _broker_with(RuntimeError("alpaca timeout"))
    monkeypatch.setattr("shrap.trading_floor.execution_agent.log", recorder)

    assert await broker.is_fractionable("UUP") is False

    (_, kw) = recorder.events[0]
    assert kw["fractionable"] is False
    assert kw["source"] == "lookup_failed"  # not "broker"
    assert kw["cached"] is False


@pytest.mark.asyncio
async def test_a_failed_lookup_is_retried_rather_than_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A network blip must not pin a symbol to whole shares for the process."""

    broker, client, recorder = _broker_with(RuntimeError("alpaca timeout"))
    monkeypatch.setattr("shrap.trading_floor.execution_agent.log", recorder)

    await broker.is_fractionable("UUP")
    await broker.is_fractionable("UUP")

    assert client.calls == ["UUP", "UUP"]  # asked again, not cached
