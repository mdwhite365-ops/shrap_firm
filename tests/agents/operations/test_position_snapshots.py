"""Position truth: the firm's only record of what it actually holds.

Nothing stored positions before this card. The Reconciliation Agent read the
account and the orders, and the Risk Officer holds no broker credentials
(ADR-0003) — so the portfolio limits had no book to measure against.

The distinction that carries the most weight here is **flat versus unmeasured**.
A flat account writes no position rows, and so does an agent that never ran. The
Risk Officer must trade on the first and halt on the second, which is why every
pass writes a marker row.
"""

from __future__ import annotations

from typing import Any

import pytest

from shrap.agents.operations.reconciliation_agent.agent import reconcile_once
from shrap.agents.operations.reconciliation_agent.broker import AlpacaPaperSnapshotReader
from shrap.agents.operations.reconciliation_agent.db import (
    FLAT_MARKER_TICKER,
    PostgresPositionSnapshotStore,
    UnidentifiedAccountError,
)
from shrap.agents.operations.reconciliation_agent.records import BrokerOrderState, BrokerPosition

ACCOUNT = {"account_number": "PA3XXXXX", "status": "ACTIVE", "equity": "10000"}


class FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.in_transaction = False
        self.committed_at: int | None = None

    async def execute(self, sql: str, *args: object) -> object:
        self.calls.append((sql, args))
        return None

    async def fetch(self, sql: str, *args: object) -> list[Any]:
        return []

    def transaction(self) -> Any:
        conn = self

        class _Txn:
            async def __aenter__(self) -> None:
                conn.in_transaction = True

            async def __aexit__(self, *exc: object) -> None:
                conn.in_transaction = False
                conn.committed_at = len(conn.calls)

        return _Txn()


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        conn = self._conn

        class _Ctx:
            async def __aenter__(self) -> FakeConn:
                return conn

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()


def _inserted_tickers(conn: FakeConn) -> list[str]:
    return [
        str(args[3])
        for sql, args in conn.calls
        if "INSERT INTO ops.position_snapshots" in sql and len(args) >= 4
    ]


# --- the store ----------------------------------------------------------------


async def test_positions_are_written_with_the_account_that_holds_them() -> None:
    conn = FakeConn()
    store = PostgresPositionSnapshotStore(FakePool(conn))

    await store.record(
        "run-1",
        "alpaca-paper",
        "PA3XXXXX",
        [BrokerPosition(symbol="AAPL", quantity=10.0, market_value=2_000.0)],
    )

    assert "AAPL" in _inserted_tickers(conn)


async def test_a_flat_account_still_writes_the_marker() -> None:
    """Otherwise a flat account and a dead Reconciliation Agent are the same
    absence of rows, and the Risk Officer must halt on one and trade on the
    other."""

    conn = FakeConn()
    store = PostgresPositionSnapshotStore(FakePool(conn))

    await store.record("run-1", "alpaca-paper", "PA3XXXXX", [])

    assert _inserted_tickers(conn) == [FLAT_MARKER_TICKER]


async def test_the_whole_snapshot_lands_in_one_transaction() -> None:
    """A partial write understates exposure, which is the one direction a risk
    input must never fail in."""

    conn = FakeConn()
    store = PostgresPositionSnapshotStore(FakePool(conn))

    await store.record(
        "run-1",
        "alpaca-paper",
        "PA3XXXXX",
        [
            BrokerPosition(symbol="AAPL", quantity=10.0, market_value=2_000.0),
            BrokerPosition(symbol="MSFT", quantity=5.0, market_value=1_500.0),
        ],
    )

    assert conn.committed_at == len(conn.calls)  # committed after the last insert
    assert not conn.in_transaction


async def test_an_unattributed_snapshot_is_refused() -> None:
    conn = FakeConn()
    store = PostgresPositionSnapshotStore(FakePool(conn))

    with pytest.raises(UnidentifiedAccountError):
        await store.record("run-1", "alpaca-paper", "  ", [])


# --- the broker adapter -------------------------------------------------------


class FakeClient:
    def __init__(self, positions: list[dict[str, Any]]) -> None:
        self._positions = positions

    async def list_positions(self, http_client: Any) -> list[dict[str, Any]]:
        return self._positions


def _reader(positions: list[dict[str, Any]]) -> AlpacaPaperSnapshotReader:
    return AlpacaPaperSnapshotReader(FakeClient(positions), object())  # type: ignore[arg-type]


async def test_market_value_comes_from_the_broker_not_from_our_own_arithmetic() -> None:
    """The Risk Officer sizes limits off this number. The risk gate must not be
    the component that disagrees with the broker about how big a position is."""

    reader = _reader([{"symbol": "AAPL", "qty": "10", "market_value": "1997.30", "side": "long"}])

    positions = await reader.list_positions()

    assert positions[0].market_value == 1997.30  # not 10 x some price we looked up


async def test_a_short_keeps_its_sign() -> None:
    reader = _reader([{"symbol": "TSLA", "qty": "-5", "market_value": "-1200.00", "side": "short"}])

    positions = await reader.list_positions()

    assert positions[0].quantity == -5.0
    assert positions[0].market_value == -1200.0


async def test_a_position_missing_its_market_value_raises_rather_than_being_skipped() -> None:
    """Silently dropping it would understate exposure."""

    reader = _reader([{"symbol": "AAPL", "qty": "10"}])

    with pytest.raises(ValueError, match="market_value"):
        await reader.list_positions()


async def test_a_position_missing_its_symbol_raises() -> None:
    reader = _reader([{"qty": "10", "market_value": "100"}])

    with pytest.raises(ValueError, match="symbol"):
        await reader.list_positions()


async def test_a_flat_broker_account_is_an_empty_list_not_an_error() -> None:
    assert await _reader([]).list_positions() == []


# --- the pass -----------------------------------------------------------------


class FakeBroker:
    def __init__(self, positions: list[BrokerPosition] | None = None, fail: bool = False) -> None:
        self._positions = positions or []
        self._fail = fail

    async def get_account(self) -> dict[str, Any]:
        return ACCOUNT

    async def list_orders(self, since: str | None = None) -> list[BrokerOrderState]:
        return []

    async def list_positions(self) -> list[BrokerPosition]:
        if self._fail:
            raise RuntimeError("broker down")
        return self._positions


class FakeRepo:
    async def latest_order_states(
        self, broker: str, account_id: str, since: object | None = None
    ) -> list[Any]:
        return []


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def publish(self, stream: str, **kwargs: Any) -> object:
        self.events.append(stream)
        return None


class RecordingSink:
    def __init__(self, fail: bool = False) -> None:
        self.records: list[tuple[str, str, list[BrokerPosition]]] = []
        self._fail = fail

    async def record(self, event_id: str, broker: str, account_id: str, positions: Any) -> None:
        if self._fail:
            raise RuntimeError("postgres down")
        self.records.append((event_id, account_id, list(positions)))


async def test_a_pass_records_the_positions_it_read() -> None:
    sink = RecordingSink()

    await reconcile_once(
        broker_reader=FakeBroker([BrokerPosition("AAPL", 10.0, 2_000.0)]),  # type: ignore[arg-type]
        repository=FakeRepo(),  # type: ignore[arg-type]
        publisher=FakePublisher(),  # type: ignore[arg-type]
        position_sink=sink,  # type: ignore[arg-type]
    )

    assert len(sink.records) == 1
    _, account_id, positions = sink.records[0]
    assert account_id == "PA3XXXXX"
    assert positions[0].symbol == "AAPL"


async def test_a_position_fetch_failure_does_not_break_order_reconciliation() -> None:
    """Order reconciliation is the pass's job. The safe end state for a missing
    position snapshot is the Risk Officer's own read going stale and its gate
    failing closed — which stops trading rather than permitting it.
    """

    publisher = FakePublisher()

    report = await reconcile_once(
        broker_reader=FakeBroker(fail=True),  # type: ignore[arg-type]
        repository=FakeRepo(),  # type: ignore[arg-type]
        publisher=publisher,  # type: ignore[arg-type]
        position_sink=RecordingSink(),  # type: ignore[arg-type]
    )

    assert report.is_clean
    assert "operations.reconciliation-completed" in publisher.events


async def test_a_pass_without_a_position_sink_behaves_as_before() -> None:
    publisher = FakePublisher()

    await reconcile_once(
        broker_reader=FakeBroker(),  # type: ignore[arg-type]
        repository=FakeRepo(),  # type: ignore[arg-type]
        publisher=publisher,  # type: ignore[arg-type]
    )

    assert "operations.reconciliation-completed" in publisher.events
