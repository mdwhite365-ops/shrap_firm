"""Broker snapshot adapter for reconciliation.

The agent core depends on the ``BrokerSnapshotReader`` protocol so tests run
against a fake broker. The Alpaca adapter wraps the paper-only client — the
paper-endpoint guarantee lives in ``AlpacaPaperSettings`` and is not
re-implemented here.
"""

from __future__ import annotations

from typing import Any, Protocol

from shrap.agents.operations.reconciliation_agent.records import BrokerOrderState, BrokerPosition
from shrap.trading_floor.alpaca import AlpacaPaperClient, AsyncHttpClient


class BrokerSnapshotReader(Protocol):
    async def get_account(self) -> dict[str, Any]: ...

    async def list_orders(self, since: str | None = None) -> list[BrokerOrderState]: ...

    async def list_positions(self) -> list[BrokerPosition]: ...


class AlpacaPaperSnapshotReader:
    """Read-only Alpaca paper snapshot: account, orders, and open positions."""

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

    async def list_orders(self, since: str | None = None) -> list[BrokerOrderState]:
        raw_orders = await self._client.list_orders(
            self._http_client,
            status=self._order_status,
            limit=self._order_limit,
            after=since,
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

    async def list_positions(self) -> list[BrokerPosition]:
        """Open positions as the venue reports them.

        ``market_value`` is taken from the broker rather than recomputed from
        quantity x a price we looked up: the Risk Officer sizes limits off this
        number, and the risk gate must not be the component that disagrees with
        the broker about how large a position is.

        A position missing a symbol or a market value is an error, not a row to
        skip. Silently dropping it would understate exposure, and understated
        exposure is the one direction a risk input must never fail in.
        """

        raw_positions = await self._client.list_positions(self._http_client)
        positions: list[BrokerPosition] = []
        for raw in raw_positions:
            symbol = str(raw.get("symbol", "")).strip().upper()
            if not symbol:
                raise ValueError("Alpaca position snapshot entry is missing a symbol")
            market_value = raw.get("market_value")
            if market_value is None:
                raise ValueError(f"Alpaca position for {symbol} is missing market_value")
            positions.append(
                BrokerPosition(
                    symbol=symbol,
                    quantity=float(raw.get("qty", 0.0)),
                    market_value=float(market_value),
                    side=_optional_str(raw.get("side")),
                )
            )
        return positions


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
