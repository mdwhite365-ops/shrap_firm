"""Alpaca paper-trading configuration.

This module deliberately refuses live Alpaca endpoints. The sprint is paper-only.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

from pydantic import BaseModel, Field, HttpUrl, SecretStr, model_validator

PAPER_HOST = "paper-api.alpaca.markets"


class HttpResponse(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> Any: ...


class AsyncHttpClient(Protocol):
    async def get(self, url: str, headers: dict[str, str]) -> HttpResponse: ...

    async def post(
        self,
        url: str,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> HttpResponse: ...


class AlpacaPaperSettings(BaseModel):
    """Paper-only Alpaca credentials loaded from env by default."""

    api_key: str = Field(default_factory=lambda: os.environ.get("ALPACA_API_KEY", ""))
    secret_key: SecretStr = Field(
        default_factory=lambda: SecretStr(os.environ.get("ALPACA_SECRET_KEY", ""))
    )
    endpoint: HttpUrl = Field(
        default_factory=lambda: HttpUrl(
            os.environ.get("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets")
        )
    )

    @model_validator(mode="after")
    def _paper_only(self) -> AlpacaPaperSettings:
        if not self.api_key:
            raise ValueError("ALPACA_API_KEY is required")
        if not self.secret_key.get_secret_value():
            raise ValueError("ALPACA_SECRET_KEY is required")
        if self.endpoint.host != PAPER_HOST:
            raise ValueError(f"Alpaca endpoint must be paper-only: {PAPER_HOST}")
        return self

    def redacted(self) -> dict[str, Any]:
        """Safe-to-log shape, never secret values."""
        return {
            "api_key": "***" if self.api_key else None,
            "secret_key": "***" if self.secret_key.get_secret_value() else None,
            "endpoint": str(self.endpoint),
            "mode": "paper",
        }


class AlpacaPaperClient:
    """Small read-only Alpaca paper client.

    Order submission is intentionally absent in this slice. The first broker-facing
    milestone is read-only account verification against the paper endpoint.
    """

    def __init__(self, settings: AlpacaPaperSettings) -> None:
        self._settings = settings

    def auth_headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._settings.api_key,
            "APCA-API-SECRET-KEY": self._settings.secret_key.get_secret_value(),
        }

    def _api_base(self) -> str:
        endpoint = str(self._settings.endpoint).rstrip("/")
        if endpoint.endswith("/v2"):
            return endpoint
        return f"{endpoint}/v2"

    async def get_account(self, http_client: AsyncHttpClient) -> dict[str, Any]:
        response = await http_client.get(
            f"{self._api_base()}/account",
            headers=self.auth_headers(),
        )
        response.raise_for_status()
        return _json_object(response.json(), "Alpaca account response")

    async def list_orders(
        self,
        http_client: AsyncHttpClient,
        status: str = "all",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """List Alpaca paper orders, most recent first.

        Defaults to ``status=all`` so reconciliation sees open, filled, and
        canceled orders in one snapshot. Alpaca caps ``limit`` at 500.
        """

        response = await http_client.get(
            f"{self._api_base()}/orders?status={status}&limit={limit}&direction=desc",
            headers=self.auth_headers(),
        )
        response.raise_for_status()
        orders = response.json()
        if not isinstance(orders, list):
            raise ValueError("Alpaca orders response must be a JSON array")
        for order in orders:
            if not isinstance(order, dict):
                raise ValueError("Alpaca orders response must contain JSON objects")
        return orders

    async def submit_order(
        self,
        http_client: AsyncHttpClient,
        order: dict[str, Any],
    ) -> dict[str, Any]:
        """Submit one order to Alpaca paper.

        The settings validator guarantees the host is the paper endpoint. The
        caller is still responsible for passing only risk-approved paper orders.
        """

        response = await http_client.post(
            f"{self._api_base()}/orders",
            headers=self.auth_headers(),
            json=order,
        )
        response.raise_for_status()
        return _json_object(response.json(), "Alpaca order response")

    async def get_order(
        self,
        http_client: AsyncHttpClient,
        order_id: str,
    ) -> dict[str, Any]:
        """Fetch one Alpaca paper order by broker order ID."""

        response = await http_client.get(
            f"{self._api_base()}/orders/{order_id}",
            headers=self.auth_headers(),
        )
        response.raise_for_status()
        return _json_object(response.json(), "Alpaca order response")


def _json_object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object")
    return value
