"""Tests for the Reconciliation Agent core."""

from __future__ import annotations

from typing import Any

import pytest

from shrap.agents.operations.reconciliation_agent.records import (
    BrokerOrderState,
    StoredOrderState,
    compare_orders,
)
from shrap.events import EventPublisher


def _stored(order_id: str, status: str | None = "filled") -> StoredOrderState:
    return StoredOrderState(
        broker="alpaca-paper",
        broker_order_id=order_id,
        status=status,
        symbol="AAPL",
        filled_quantity="1",
    )


def _broker(order_id: str, status: str = "filled") -> BrokerOrderState:
    return BrokerOrderState(
        broker_order_id=order_id,
        status=status,
        symbol="AAPL",
        filled_quantity="1",
    )


def test_compare_orders_matching_states_produce_clean_report() -> None:
    report = compare_orders(
        stored=[_stored("order-1"), _stored("order-2", status="accepted")],
        broker_orders=[_broker("order-1"), _broker("order-2", status="accepted")],
        broker="alpaca-paper",
    )

    assert report.is_clean
    assert report.matched == 2
    assert report.stored_orders == 2
    assert report.broker_orders == 2
    assert report.discrepancies == ()


def test_compare_orders_status_comparison_is_case_and_whitespace_insensitive() -> None:
    report = compare_orders(
        stored=[_stored("order-1", status=" FILLED ")],
        broker_orders=[_broker("order-1", status="filled")],
        broker="alpaca-paper",
    )

    assert report.is_clean
    assert report.matched == 1


def test_compare_orders_flags_broker_order_missing_from_store() -> None:
    report = compare_orders(
        stored=[],
        broker_orders=[_broker("order-1", status="accepted")],
        broker="alpaca-paper",
    )

    assert not report.is_clean
    assert len(report.discrepancies) == 1
    discrepancy = report.discrepancies[0]
    assert discrepancy.kind == "missing-in-store"
    assert discrepancy.broker_order_id == "order-1"
    assert discrepancy.stored_status is None
    assert discrepancy.broker_status == "accepted"


def test_compare_orders_flags_stored_order_missing_at_broker() -> None:
    report = compare_orders(
        stored=[_stored("order-1", status="accepted")],
        broker_orders=[],
        broker="alpaca-paper",
    )

    assert not report.is_clean
    assert len(report.discrepancies) == 1
    discrepancy = report.discrepancies[0]
    assert discrepancy.kind == "missing-at-broker"
    assert discrepancy.broker_order_id == "order-1"
    assert discrepancy.stored_status == "accepted"
    assert discrepancy.broker_status is None


def test_compare_orders_flags_status_mismatch() -> None:
    report = compare_orders(
        stored=[_stored("order-1", status="accepted")],
        broker_orders=[_broker("order-1", status="filled")],
        broker="alpaca-paper",
    )

    assert not report.is_clean
    assert report.matched == 0
    discrepancy = report.discrepancies[0]
    assert discrepancy.kind == "status-mismatch"
    assert discrepancy.stored_status == "accepted"
    assert discrepancy.broker_status == "filled"


def test_compare_orders_stored_none_status_is_a_mismatch() -> None:
    report = compare_orders(
        stored=[_stored("order-1", status=None)],
        broker_orders=[_broker("order-1", status="filled")],
        broker="alpaca-paper",
    )

    assert not report.is_clean
    assert report.discrepancies[0].kind == "status-mismatch"


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        return f"178012860000{len(self.calls)}-0"


class FakeBrokerReader:
    def __init__(
        self,
        account: dict[str, Any],
        orders: list[BrokerOrderState],
    ) -> None:
        self._account = account
        self._orders = orders

    async def get_account(self) -> dict[str, Any]:
        return self._account

    async def list_orders(self) -> list[BrokerOrderState]:
        return self._orders


class FakeRepository:
    def __init__(self, states: list[StoredOrderState]) -> None:
        self._states = states
        self.requested_brokers: list[str] = []

    async def latest_order_states(self, broker: str) -> list[StoredOrderState]:
        self.requested_brokers.append(broker)
        return self._states


class FakeSnapshotSink:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    async def record(self, event_id: str, broker: str, account: dict[str, Any]) -> None:
        self.records.append((event_id, broker, account))


@pytest.mark.asyncio
async def test_reconcile_once_publishes_account_summary_and_records_snapshot() -> None:
    from shrap.agents.operations.reconciliation_agent.agent import reconcile_once
    from shrap.events import Envelope, normalize_redis_fields

    redis = FakeRedis()
    broker_reader = FakeBrokerReader(
        account={
            "status": "ACTIVE",
            "currency": "USD",
            "cash": "99883.42",
            "equity": "100011.58",
            "buying_power": "199766.84",
            "portfolio_value": "100011.58",
        },
        orders=[_broker("order-1")],
    )
    repository = FakeRepository([_stored("order-1")])
    sink = FakeSnapshotSink()

    await reconcile_once(
        broker_reader=broker_reader,
        repository=repository,
        publisher=EventPublisher(redis),
        snapshot_sink=sink,
    )

    envelope = Envelope.from_redis_fields(normalize_redis_fields(redis.calls[-1][1]))
    assert envelope.payload is not None
    assert envelope.payload["account"] == {
        "status": "ACTIVE",
        "currency": "USD",
        "cash": "99883.42",
        "equity": "100011.58",
        "buying_power": "199766.84",
        "portfolio_value": "100011.58",
    }
    assert len(sink.records) == 1
    event_id, broker_name, account = sink.records[0]
    assert event_id == envelope.correlation_id
    assert broker_name == "alpaca-paper"
    assert account["equity"] == "100011.58"


@pytest.mark.asyncio
async def test_account_snapshot_store_parses_numbers_and_inserts() -> None:
    from shrap.agents.operations.reconciliation_agent.db import (
        INSERT_ACCOUNT_SNAPSHOT_SQL,
        PostgresAccountSnapshotStore,
    )

    executed: list[tuple[str, tuple[object, ...]]] = []

    class FakeConn:
        async def execute(self, sql: str, *args: object) -> None:
            executed.append((sql, args))

        async def fetch(self, sql: str, *args: object) -> list[Any]:
            return []

    class FakeAcquire:
        async def __aenter__(self) -> FakeConn:
            return FakeConn()

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    class FakePool:
        def acquire(self) -> FakeAcquire:
            return FakeAcquire()

    store = PostgresAccountSnapshotStore(FakePool())
    await store.record(
        "run-1",
        "alpaca-paper",
        {
            "status": "ACTIVE",
            "currency": "USD",
            "cash": "99883.42",
            "equity": "not-a-number",
            "buying_power": None,
        },
    )

    assert executed[0][0] == INSERT_ACCOUNT_SNAPSHOT_SQL
    args = executed[0][1]
    assert args[0] == "run-1"
    assert args[1] == "alpaca-paper"
    assert args[4] == 99883.42  # cash parsed
    assert args[5] is None  # unparseable equity stays NULL
    assert args[6] is None  # missing buying_power stays NULL


@pytest.mark.asyncio
async def test_reconcile_once_clean_pass_publishes_completed_only() -> None:
    from shrap.agents.operations.reconciliation_agent.agent import (
        STREAM_RECONCILIATION_COMPLETED,
        reconcile_once,
    )
    from shrap.events import Envelope, normalize_redis_fields

    redis = FakeRedis()
    broker_reader = FakeBrokerReader(
        account={"status": "ACTIVE"},
        orders=[_broker("order-1")],
    )
    repository = FakeRepository([_stored("order-1")])

    report = await reconcile_once(
        broker_reader=broker_reader,
        repository=repository,
        publisher=EventPublisher(redis),
    )

    assert report.is_clean
    assert repository.requested_brokers == ["alpaca-paper"]
    assert [stream for stream, _ in redis.calls] == [STREAM_RECONCILIATION_COMPLETED]
    envelope = Envelope.from_redis_fields(normalize_redis_fields(redis.calls[0][1]))
    assert envelope.payload == {
        "broker": "alpaca-paper",
        "account_status": "ACTIVE",
        "account": {
            "status": "ACTIVE",
            "currency": None,
            "cash": None,
            "equity": None,
            "buying_power": None,
            "portfolio_value": None,
        },
        "stored_orders": 1,
        "broker_orders": 1,
        "matched": 1,
        "discrepancies": 0,
        "clean": True,
    }
    assert envelope.correlation_id is not None


@pytest.mark.asyncio
async def test_reconcile_once_publishes_discrepancy_events_before_completed() -> None:
    from shrap.agents.operations.reconciliation_agent.agent import (
        STREAM_RECONCILIATION_COMPLETED,
        STREAM_RECONCILIATION_DISCREPANCY,
        reconcile_once,
    )
    from shrap.events import Envelope, normalize_redis_fields

    redis = FakeRedis()
    broker_reader = FakeBrokerReader(
        account={"status": "ACTIVE"},
        orders=[_broker("order-1", status="filled"), _broker("order-2", status="accepted")],
    )
    repository = FakeRepository([_stored("order-1", status="accepted")])

    report = await reconcile_once(
        broker_reader=broker_reader,
        repository=repository,
        publisher=EventPublisher(redis),
        correlation_id="run-123",
    )

    assert not report.is_clean
    assert len(report.discrepancies) == 2

    streams = [stream for stream, _ in redis.calls]
    assert streams == [
        STREAM_RECONCILIATION_DISCREPANCY,
        STREAM_RECONCILIATION_DISCREPANCY,
        STREAM_RECONCILIATION_COMPLETED,
    ]

    envelopes = [
        Envelope.from_redis_fields(normalize_redis_fields(fields)) for _, fields in redis.calls
    ]
    assert {envelope.correlation_id for envelope in envelopes} == {"run-123"}

    discrepancy_payloads = [envelope.payload for envelope in envelopes[:2]]
    kinds = {payload["kind"] for payload in discrepancy_payloads if payload is not None}
    assert kinds == {"status-mismatch", "missing-in-store"}

    completed = envelopes[-1].payload
    assert completed is not None
    assert completed["discrepancies"] == 2
    assert completed["clean"] is False


@pytest.mark.asyncio
async def test_reconcile_once_broker_failure_publishes_nothing() -> None:
    from shrap.agents.operations.reconciliation_agent.agent import reconcile_once

    class FailingBrokerReader:
        async def get_account(self) -> dict[str, Any]:
            raise RuntimeError("broker unreachable")

        async def list_orders(self) -> list[BrokerOrderState]:
            raise AssertionError("must not be called")

    redis = FakeRedis()
    repository = FakeRepository([])

    with pytest.raises(RuntimeError):
        await reconcile_once(
            broker_reader=FailingBrokerReader(),
            repository=repository,
            publisher=EventPublisher(redis),
        )

    assert redis.calls == []


@pytest.mark.asyncio
async def test_postgres_repository_maps_rows_to_stored_states() -> None:
    from shrap.agents.operations.reconciliation_agent.db import (
        SELECT_LATEST_ORDER_STATES_SQL,
        PostgresOrderEventRepository,
    )

    rows = [
        {
            "broker": "alpaca-paper",
            "broker_order_id": "order-1",
            "status": "filled",
            "symbol": "AAPL",
            "filled_quantity": "1",
        },
        {
            "broker": "alpaca-paper",
            "broker_order_id": "order-2",
            "status": None,
            "symbol": None,
            "filled_quantity": None,
        },
    ]

    class FakeConn:
        def __init__(self) -> None:
            self.queries: list[tuple[str, tuple[object, ...]]] = []

        async def fetch(self, sql: str, *args: object) -> list[Any]:
            self.queries.append((sql, args))
            return rows

    conn = FakeConn()

    class FakeAcquire:
        async def __aenter__(self) -> FakeConn:
            return conn

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    class FakePool:
        def acquire(self) -> FakeAcquire:
            return FakeAcquire()

    repository = PostgresOrderEventRepository(FakePool())

    states = await repository.latest_order_states("alpaca-paper")

    assert conn.queries == [(SELECT_LATEST_ORDER_STATES_SQL, ("alpaca-paper",))]
    assert states == [
        StoredOrderState(
            broker="alpaca-paper",
            broker_order_id="order-1",
            status="filled",
            symbol="AAPL",
            filled_quantity="1",
        ),
        StoredOrderState(
            broker="alpaca-paper",
            broker_order_id="order-2",
            status=None,
            symbol=None,
            filled_quantity=None,
        ),
    ]


@pytest.mark.asyncio
async def test_alpaca_snapshot_reader_maps_orders_and_rejects_missing_ids() -> None:
    from shrap.agents.operations.reconciliation_agent.broker import AlpacaPaperSnapshotReader
    from shrap.trading_floor.alpaca import AlpacaPaperClient, AlpacaPaperSettings

    class FakeResponse:
        def __init__(self, body: Any) -> None:
            self._body = body

        def raise_for_status(self) -> None:
            return None

        def json(self) -> Any:
            return self._body

    class FakeHttpClient:
        def __init__(self, body: Any) -> None:
            self._body = body
            self.urls: list[str] = []

        async def get(self, url: str, headers: dict[str, str]) -> FakeResponse:
            self.urls.append(url)
            return FakeResponse(self._body)

    settings = AlpacaPaperSettings(
        api_key="paper-key",
        secret_key="paper-secret",
        endpoint="https://paper-api.alpaca.markets",
    )
    http_client = FakeHttpClient(
        [
            {"id": "order-1", "status": "filled", "symbol": "AAPL", "filled_qty": "1"},
            {"id": "order-2", "status": "accepted"},
        ]
    )
    reader = AlpacaPaperSnapshotReader(
        AlpacaPaperClient(settings),
        http_client,  # type: ignore[arg-type]
    )

    orders = await reader.list_orders()

    assert http_client.urls == [
        "https://paper-api.alpaca.markets/v2/orders?status=all&limit=500&direction=desc"
    ]
    assert orders == [
        BrokerOrderState(
            broker_order_id="order-1", status="filled", symbol="AAPL", filled_quantity="1"
        ),
        BrokerOrderState(
            broker_order_id="order-2", status="accepted", symbol=None, filled_quantity=None
        ),
    ]

    bad_http_client = FakeHttpClient([{"status": "accepted"}])
    bad_reader = AlpacaPaperSnapshotReader(
        AlpacaPaperClient(settings),
        bad_http_client,  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="missing an order id"):
        await bad_reader.list_orders()
