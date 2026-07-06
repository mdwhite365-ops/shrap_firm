from __future__ import annotations

import tomllib
from pathlib import Path


def test_settings_loads_execution_agent_environment(monkeypatch) -> None:
    from shrap.agents.trading_floor.execution_agent.config import Settings

    monkeypatch.setenv("EXECUTION_AGENT_REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("EXECUTION_AGENT_ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("EXECUTION_AGENT_ALPACA_SECRET_KEY", "paper-secret")
    monkeypatch.setenv("EXECUTION_AGENT_ALPACA_ENDPOINT", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("EXECUTION_AGENT_START_ID", "0-0")

    settings = Settings()

    assert settings.redis_url == "redis://redis:6379/0"
    assert settings.alpaca_settings().api_key == "paper-key"
    assert settings.alpaca_settings().endpoint.host == "paper-api.alpaca.markets"
    assert settings.start_id == "0-0"


def test_settings_rejects_live_alpaca_endpoint(monkeypatch) -> None:
    import pytest
    from pydantic import ValidationError

    from shrap.agents.trading_floor.execution_agent.config import Settings

    monkeypatch.setenv("EXECUTION_AGENT_ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("EXECUTION_AGENT_ALPACA_SECRET_KEY", "paper-secret")
    monkeypatch.setenv("EXECUTION_AGENT_ALPACA_ENDPOINT", "https://api.alpaca.markets")

    with pytest.raises(ValidationError, match="paper-only"):
        Settings().alpaca_settings()


def test_settings_redacted_output_is_log_safe() -> None:
    from shrap.agents.trading_floor.execution_agent.config import Settings

    settings = Settings(
        redis_url="redis://redis:6379/0",
        alpaca_api_key="paper-key",
        alpaca_secret_key="paper-secret",
    )

    assert settings.redacted() == {
        "service_name": "execution-agent",
        "instance_id": settings.instance_id,
        "redis_url": "redis://redis:6379/0",
        "alpaca": {
            "api_key": "***",
            "secret_key": "***",
            "endpoint": "https://paper-api.alpaca.markets/",
            "mode": "paper",
        },
        "start_id": "0-0",
        "count": 100,
        "block_ms": 5000,
        "retry_delay_seconds": 1.0,
        "status_poll_interval_seconds": 5.0,
        "log_level": "INFO",
    }


def test_console_script_is_registered() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert pyproject["project"]["scripts"]["shrap-execution-agent"] == (
        "shrap.agents.trading_floor.execution_agent.__main__:main"
    )


def test_compose_defines_execution_agent_service() -> None:
    compose = Path("infra/docker-compose.yml").read_text()

    assert "execution-agent:" in compose
    assert "container_name: shrap_execution_agent" in compose
    assert "dockerfile: infra/execution-agent.Dockerfile" in compose
    assert "EXECUTION_AGENT_REDIS_URL" in compose
    assert "EXECUTION_AGENT_ALPACA_ENDPOINT" in compose
