"""Deployability tests for the Reconciliation Agent service."""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from shrap.agents.operations.reconciliation_agent.records import (
    BrokerOrderState,
    StoredOrderState,
)
from shrap.events import EventPublisher


def test_settings_loads_reconciliation_agent_environment(monkeypatch: Any) -> None:
    from shrap.agents.operations.reconciliation_agent.config import Settings

    monkeypatch.setenv("RECONCILIATION_AGENT_REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("RECONCILIATION_AGENT_POSTGRES_DSN", "postgresql://db.internal/shrap")
    monkeypatch.setenv("RECONCILIATION_AGENT_ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("RECONCILIATION_AGENT_ALPACA_SECRET_KEY", "paper-secret")
    monkeypatch.setenv("RECONCILIATION_AGENT_INTERVAL_SECONDS", "120")
    monkeypatch.setenv("RECONCILIATION_AGENT_ORDER_LIMIT", "250")

    settings = Settings()

    assert settings.redis_url == "redis://redis:6379/0"
    assert settings.postgres_dsn_value() == "postgresql://db.internal/shrap"
    assert settings.interval_seconds == 120.0
    assert settings.order_limit == 250
    assert settings.broker == "alpaca-paper"

    alpaca = settings.alpaca_settings()
    assert alpaca.api_key == "paper-key"
    assert str(alpaca.endpoint).startswith("https://paper-api.alpaca.markets")


def test_settings_rejects_live_alpaca_endpoint(monkeypatch: Any) -> None:
    from shrap.agents.operations.reconciliation_agent.config import Settings

    monkeypatch.setenv("RECONCILIATION_AGENT_ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("RECONCILIATION_AGENT_ALPACA_SECRET_KEY", "paper-secret")
    monkeypatch.setenv("RECONCILIATION_AGENT_ALPACA_ENDPOINT", "https://api.alpaca.markets")

    settings = Settings()

    with pytest.raises(ValueError, match="paper-only"):
        settings.alpaca_settings()


def test_settings_redacted_output_is_log_safe() -> None:
    from shrap.agents.operations.reconciliation_agent.config import Settings

    settings = Settings(
        redis_url="redis://redis:6379/0",
        postgres_dsn=SecretStr("postgresql://db.internal/shrap"),
        alpaca_api_key="paper-key",
        alpaca_secret_key=SecretStr("paper-secret"),
    )

    redacted = settings.redacted()

    assert redacted["postgres_dsn"] == "***"
    assert redacted["alpaca"] == {
        "api_key": "***",
        "secret_key": "***",
        "endpoint": "https://paper-api.alpaca.markets/",
        "mode": "paper",
    }
    assert "paper-secret" not in str(redacted)
    assert "db.internal" not in str(redacted)


def test_console_script_and_optional_extra_are_registered() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert pyproject["project"]["scripts"]["shrap-reconciliation-agent"] == (
        "shrap.agents.operations.reconciliation_agent.__main__:main"
    )
    extra = pyproject["project"]["optional-dependencies"]["reconciliation-agent"]
    assert "asyncpg>=0.29" in extra
    assert "pydantic-settings>=2.4" in extra


def test_compose_defines_reconciliation_agent_service() -> None:
    compose = Path("infra/docker-compose.yml").read_text()

    assert "reconciliation-agent:" in compose
    assert "container_name: shrap_reconciliation_agent" in compose
    assert "dockerfile: infra/reconciliation-agent.Dockerfile" in compose
    assert "RECONCILIATION_AGENT_REDIS_URL" in compose
    assert "RECONCILIATION_AGENT_POSTGRES_DSN" in compose
    assert "RECONCILIATION_AGENT_ALPACA_API_KEY" in compose
    assert "RECONCILIATION_AGENT_INTERVAL_SECONDS" in compose


def test_dockerfile_runs_reconciliation_agent_console_script() -> None:
    dockerfile = Path("infra/reconciliation-agent.Dockerfile").read_text()

    assert "asyncpg>=0.29" in dockerfile
    assert 'CMD ["shrap-reconciliation-agent"]' in dockerfile


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        return f"178012860000{len(self.calls)}-0"


class FakeBrokerReader:
    async def get_account(self) -> dict[str, Any]:
        return {"status": "ACTIVE"}

    async def list_orders(self, since: str | None = None) -> list[BrokerOrderState]:
        return []


class FakeRepository:
    async def latest_order_states(
        self, broker: str, since: object | None = None
    ) -> list[StoredOrderState]:
        return []


@pytest.mark.asyncio
async def test_run_loop_reconciles_then_exits_cleanly_on_stop() -> None:
    from shrap.agents.operations.reconciliation_agent.runner import run_loop

    redis = FakeRedis()
    stop = asyncio.Event()

    async def stop_soon() -> None:
        await asyncio.sleep(0)
        stop.set()

    await asyncio.gather(
        run_loop(
            broker_reader=FakeBrokerReader(),
            repository=FakeRepository(),
            publisher=EventPublisher(redis),
            stop=stop,
            interval_seconds=60.0,
            retry_delay_seconds=0.0,
        ),
        stop_soon(),
    )

    assert stop.is_set()
    assert len(redis.calls) == 1
    assert redis.calls[0][0] == "operations.reconciliation-completed"


@pytest.mark.asyncio
async def test_run_loop_survives_a_failing_pass() -> None:
    from shrap.agents.operations.reconciliation_agent.runner import run_loop

    attempts = 0

    class FlakyBrokerReader:
        async def get_account(self) -> dict[str, Any]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("broker unreachable")
            return {"status": "ACTIVE"}

        async def list_orders(self, since: str | None = None) -> list[BrokerOrderState]:
            return []

    redis = FakeRedis()
    stop = asyncio.Event()

    original_calls = redis.calls

    class StopAfterSuccessRedis(FakeRedis):
        async def xadd(self, stream: str, fields: dict[str, str]) -> str:
            original_calls.append((stream, fields))
            stop.set()
            return f"178012860000{len(original_calls)}-0"

    await run_loop(
        broker_reader=FlakyBrokerReader(),
        repository=FakeRepository(),
        publisher=EventPublisher(StopAfterSuccessRedis()),
        stop=stop,
        interval_seconds=60.0,
        retry_delay_seconds=0.0,
    )

    assert attempts == 2
    assert len(original_calls) == 1
    assert original_calls[0][0] == "operations.reconciliation-completed"
