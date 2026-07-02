"""Data shapes and comparison logic for paper-order reconciliation.

Reconciliation compares two views of the same orders: the broker's snapshot
(Alpaca paper) and the latest persisted state per order in
``trading.paper_order_events``. The comparison is a pure function over both
views so it can be tested without a broker or a database.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

DiscrepancyKind = Literal["missing-in-store", "missing-at-broker", "status-mismatch"]


@dataclass(frozen=True, slots=True)
class StoredOrderState:
    """Latest persisted state for one broker order from trading.paper_order_events."""

    broker: str
    broker_order_id: str
    status: str | None
    symbol: str | None
    filled_quantity: str | None


@dataclass(frozen=True, slots=True)
class BrokerOrderState:
    """One order as reported by the broker snapshot."""

    broker_order_id: str
    status: str
    symbol: str | None
    filled_quantity: str | None


@dataclass(frozen=True, slots=True)
class Discrepancy:
    """One divergence between broker state and the persisted order trail."""

    kind: DiscrepancyKind
    broker_order_id: str
    stored_status: str | None
    broker_status: str | None
    symbol: str | None


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Outcome of one reconciliation pass."""

    broker: str
    stored_orders: int
    broker_orders: int
    matched: int
    discrepancies: tuple[Discrepancy, ...]

    @property
    def is_clean(self) -> bool:
        return not self.discrepancies


def _normalized_status(status: str | None) -> str | None:
    if status is None:
        return None
    return status.strip().lower()


def compare_orders(
    stored: Sequence[StoredOrderState],
    broker_orders: Sequence[BrokerOrderState],
    broker: str,
) -> ReconciliationReport:
    """Compare the persisted order trail against the broker snapshot.

    An order present at the broker but absent from the store is
    ``missing-in-store`` (persistence gap). An order present in the store but
    absent at the broker is ``missing-at-broker`` (an order we believe exists
    that the broker does not report). Orders present in both match only when
    their normalized statuses agree.
    """

    stored_by_id = {state.broker_order_id: state for state in stored}
    broker_by_id = {state.broker_order_id: state for state in broker_orders}

    discrepancies: list[Discrepancy] = []
    matched = 0

    for order_id, broker_state in broker_by_id.items():
        stored_state = stored_by_id.get(order_id)
        if stored_state is None:
            discrepancies.append(
                Discrepancy(
                    kind="missing-in-store",
                    broker_order_id=order_id,
                    stored_status=None,
                    broker_status=broker_state.status,
                    symbol=broker_state.symbol,
                )
            )
            continue
        if _normalized_status(stored_state.status) == _normalized_status(broker_state.status):
            matched += 1
        else:
            discrepancies.append(
                Discrepancy(
                    kind="status-mismatch",
                    broker_order_id=order_id,
                    stored_status=stored_state.status,
                    broker_status=broker_state.status,
                    symbol=broker_state.symbol or stored_state.symbol,
                )
            )

    for order_id, stored_state in stored_by_id.items():
        if order_id not in broker_by_id:
            discrepancies.append(
                Discrepancy(
                    kind="missing-at-broker",
                    broker_order_id=order_id,
                    stored_status=stored_state.status,
                    broker_status=None,
                    symbol=stored_state.symbol,
                )
            )

    return ReconciliationReport(
        broker=broker,
        stored_orders=len(stored_by_id),
        broker_orders=len(broker_by_id),
        matched=matched,
        discrepancies=tuple(discrepancies),
    )
