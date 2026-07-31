"""The discrepancy stream is edge-triggered; the completed stream is not.

Regression cover for the 11,096-event stream found on 2026-07-31. The
divergence was real and understood — Mike cancelled the account's original test
orders by hand, so the broker held filled orders the store had never seen — and
the agent re-announced it every 300 seconds, from three agents, for weeks.

What these tests pin is the *distinction*, not the suppression: a repeat is
silent on the discrepancy stream, and the current count is still on every
completed event. Suppression that also hid current state would be worse than
the noise it replaced.
"""

from __future__ import annotations

from typing import Any

import pytest

from shrap.agents.operations.reconciliation_agent.agent import (
    STREAM_RECONCILIATION_COMPLETED,
    STREAM_RECONCILIATION_DISCREPANCY,
    reconcile_once,
)
from shrap.agents.operations.reconciliation_agent.discrepancy_state import (
    DiscrepancyTracker,
    discrepancy_key,
)
from shrap.agents.operations.reconciliation_agent.records import (
    BrokerOrderState,
    Discrepancy,
    StoredOrderState,
)
from shrap.events import Envelope, EventPublisher, normalize_redis_fields


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        return f"178012860000{len(self.calls)}-0"

    def streams(self) -> list[str]:
        return [stream for stream, _ in self.calls]

    def payloads(self, stream: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name, fields in self.calls:
            if name != stream:
                continue
            payload = Envelope.from_redis_fields(normalize_redis_fields(fields)).payload
            if payload is not None:
                out.append(payload)
        return out


class FakeBrokerReader:
    def __init__(self, orders: list[BrokerOrderState]) -> None:
        self._orders = orders

    async def get_account(self) -> dict[str, Any]:
        return {"status": "ACTIVE", "account_number": "PA3RECON"}

    async def list_orders(self, since: str | None = None) -> list[BrokerOrderState]:
        return self._orders


class FakeRepository:
    def __init__(self, states: list[StoredOrderState]) -> None:
        self._states = states

    async def latest_order_states(
        self, broker: str, account_id: str, since: object | None = None
    ) -> list[StoredOrderState]:
        return self._states


def _broker(order_id: str, *, status: str = "filled") -> BrokerOrderState:
    return BrokerOrderState(
        broker_order_id=order_id,
        status=status,
        symbol="SPY",
        filled_quantity="1",
    )


def _missing(order_id: str, *, broker_status: str = "filled") -> Discrepancy:
    return Discrepancy(
        kind="missing-in-store",
        broker_order_id=order_id,
        stored_status=None,
        broker_status=broker_status,
        symbol="SPY",
    )


# --- the tracker ----------------------------------------------------------------


def test_the_first_sighting_is_news_and_the_second_is_not() -> None:
    tracker = DiscrepancyTracker()
    divergence = [_missing("order-1")]

    first = tracker.observe(divergence)
    second = tracker.observe(divergence)

    assert first.appeared == (divergence[0],)
    assert first.suppressed == 0
    assert second.appeared == ()
    assert second.suppressed == 1


def test_a_changed_status_on_a_known_order_is_news_again() -> None:
    """new -> filled on an order we still do not have is new information."""

    tracker = DiscrepancyTracker()
    tracker.observe([_missing("order-1", broker_status="new")])

    delta = tracker.observe([_missing("order-1", broker_status="filled")])

    assert len(delta.appeared) == 1
    assert delta.appeared[0].broker_status == "filled"


def test_a_divergence_that_clears_is_counted_and_forgotten() -> None:
    tracker = DiscrepancyTracker()
    tracker.observe([_missing("order-1")])

    cleared = tracker.observe([])
    assert cleared.resolved == 1

    # Forgotten, so if it comes back it is news again — a divergence that
    # returns after clearing is a different event from one that never left.
    returned = tracker.observe([_missing("order-1")])
    assert len(returned.appeared) == 1


def test_the_symbol_is_not_part_of_the_identity() -> None:
    """Symbol is derived from the order; including it could only ever misfire."""

    a = _missing("order-1")
    b = Discrepancy(
        kind="missing-in-store",
        broker_order_id="order-1",
        stored_status=None,
        broker_status="filled",
        symbol="DIFFERENT",
    )

    assert discrepancy_key(a) == discrepancy_key(b)


def test_a_pass_reports_appearances_and_repeats_separately() -> None:
    tracker = DiscrepancyTracker()
    tracker.observe([_missing("order-1")])

    delta = tracker.observe([_missing("order-1"), _missing("order-2")])

    assert len(delta.appeared) == 1
    assert delta.suppressed == 1
    assert delta.open_count == 2


# --- end to end -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unchanged_divergence_is_published_once_not_every_pass() -> None:
    redis = FakeRedis()
    broker_reader = FakeBrokerReader([_broker("order-1")])
    repository = FakeRepository([])
    tracker = DiscrepancyTracker()

    for _ in range(3):
        report = await reconcile_once(
            broker_reader=broker_reader,
            repository=repository,
            publisher=EventPublisher(redis),
            tracker=tracker,
        )
        assert not report.is_clean  # the comparison still sees it every pass

    assert redis.streams().count(STREAM_RECONCILIATION_DISCREPANCY) == 1
    assert redis.streams().count(STREAM_RECONCILIATION_COMPLETED) == 3


@pytest.mark.asyncio
async def test_the_completed_event_still_carries_the_current_count_every_pass() -> None:
    """Suppression must not hide current state, or it is worse than the noise."""

    redis = FakeRedis()
    tracker = DiscrepancyTracker()
    broker_reader = FakeBrokerReader([_broker("order-1")])
    repository = FakeRepository([])

    for _ in range(3):
        await reconcile_once(
            broker_reader=broker_reader,
            repository=repository,
            publisher=EventPublisher(redis),
            tracker=tracker,
        )

    completed = redis.payloads(STREAM_RECONCILIATION_COMPLETED)
    assert len(completed) == 3
    assert [c["discrepancies"] for c in completed] == [1, 1, 1]
    assert [c["clean"] for c in completed] == [False, False, False]


@pytest.mark.asyncio
async def test_without_a_tracker_every_divergence_is_still_published() -> None:
    """A one-shot run has no previous pass to be relative to, so it reports all."""

    redis = FakeRedis()
    broker_reader = FakeBrokerReader([_broker("order-1")])
    repository = FakeRepository([])

    for _ in range(3):
        await reconcile_once(
            broker_reader=broker_reader,
            repository=repository,
            publisher=EventPublisher(redis),
        )

    assert redis.streams().count(STREAM_RECONCILIATION_DISCREPANCY) == 3
