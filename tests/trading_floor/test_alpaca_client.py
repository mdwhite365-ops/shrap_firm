"""Tests for read-only Alpaca paper client."""

from __future__ import annotations

from typing import Any

import pytest


def test_alpaca_paper_client_builds_auth_headers() -> None:
    from shrap.trading_floor.alpaca import AlpacaPaperClient, AlpacaPaperSettings

    settings = AlpacaPaperSettings(
        api_key="paper-key",
        secret_key="paper-secret",
        endpoint="https://paper-api.alpaca.markets",
    )

    client = AlpacaPaperClient(settings)

    assert client.auth_headers() == {
        "APCA-API-KEY-ID": "paper-key",
        "APCA-API-SECRET-KEY": "paper-secret",
    }


@pytest.mark.asyncio
async def test_get_account_uses_paper_endpoint_and_redacts_nothing_in_return() -> None:
    from shrap.trading_floor.alpaca import AlpacaPaperClient, AlpacaPaperSettings

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "id": "acct-123",
                "status": "ACTIVE",
                "trading_blocked": False,
                "account_blocked": False,
                "paper": True,
            }

    class FakeHttpClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, str]]] = []

        async def get(self, url: str, headers: dict[str, str]) -> FakeResponse:
            self.calls.append((url, headers))
            return FakeResponse()

    settings = AlpacaPaperSettings(
        api_key="paper-key",
        secret_key="paper-secret",
        endpoint="https://paper-api.alpaca.markets",
    )
    http_client = FakeHttpClient()
    client = AlpacaPaperClient(settings)

    account = await client.get_account(http_client)  # type: ignore[arg-type]

    assert http_client.calls == [
        (
            "https://paper-api.alpaca.markets/v2/account",
            {
                "APCA-API-KEY-ID": "paper-key",
                "APCA-API-SECRET-KEY": "paper-secret",
            },
        )
    ]
    assert account["status"] == "ACTIVE"
    assert account["trading_blocked"] is False


@pytest.mark.asyncio
async def test_get_account_does_not_duplicate_v2_when_endpoint_includes_api_prefix() -> None:
    from shrap.trading_floor.alpaca import AlpacaPaperClient, AlpacaPaperSettings

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"status": "ACTIVE"}

    class FakeHttpClient:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def get(self, url: str, headers: dict[str, str]) -> FakeResponse:
            self.urls.append(url)
            return FakeResponse()

    settings = AlpacaPaperSettings(
        api_key="paper-key",
        secret_key="paper-secret",
        endpoint="https://paper-api.alpaca.markets/v2",
    )
    http_client = FakeHttpClient()

    await AlpacaPaperClient(settings).get_account(http_client)  # type: ignore[arg-type]

    assert http_client.urls == ["https://paper-api.alpaca.markets/v2/account"]
