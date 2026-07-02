"""Broker snapshot adapter for reconciliation.

The agent core depends on the ``BrokerSnapshotReader`` protocol so tests run
against a fake broker. The Alpaca adapter wraps the paper-only client — the
paper-endpoint guarantee lives in ``AlpacaPaperSettings`` and is not
re-implemented here.
"""

from __future__ import annotations

from typing import Any, Protocol

from shrap.agents.operations.reconciliation_agent.records import BrokerOrderState
from shrap.trading_floor.alpaca import AlpacaPaperClient, AsyncHttpClient


class BrokerSnapshotReader(Protocol):
    async def get_account(self) -> dict[str, Any]: ...

    async def list_orders(self) -> list[BrokerOrderState]: ...


class AlpacaPaperSnapshotReader:
    """Read-only Alpaca paper snapshot: account plus all orders."""

    def __init__(
        self,
        client: AlpacaPaperClient,
        http_client: AsyncHttpClient,
        order_status: str = "all",
        order_limit: int = 500,
    ) -> None:
        self._client = client
        self._http_client = http_client
        self._order_status = order_status
        self._order_limit = order_limit

    async def get_account(self) -> dict[str, Any]:
        return await self._client.get_account(self._http_client)

    async def list_orders(self) -> list[BrokerOrderState]:
        raw_orders = await self._client.list_orders(
            self._http_client,
            status=self._order_status,
            limit=self._order_limit,
        )
        orders: list[BrokerOrderState] = []
        for raw in raw_orders:
            order_id = str(raw.get("id", "")).strip()
            if not order_id:
                raise ValueError("Alpaca order snapshot entry is missing an order id")
            orders.append(
                BrokerOrderState(
                    broker_order_id=order_id,
                    status=str(raw.get("status", "")),
                    symbol=_optional_str(raw.get("symbol")),
                    filled_quantity=_optional_str(raw.get("filled_qty")),
                )
            )
        return orders


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
