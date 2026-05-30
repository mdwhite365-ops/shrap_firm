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

    def json(self) -> dict[str, Any]: ...


class AsyncHttpClient(Protocol):
    async def get(self, url: str, headers: dict[str, str]) -> HttpResponse: ...


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
        return response.json()
