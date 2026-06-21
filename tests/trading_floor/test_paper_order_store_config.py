from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import SecretStr


def test_settings_loads_paper_order_store_environment(monkeypatch) -> None:
    from shrap.agents.trading_floor.paper_order_store.config import Settings

    monkeypatch.setenv("PAPER_ORDER_STORE_REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("PAPER_ORDER_STORE_POSTGRES_DSN", "postgresql://db.internal/shrap")
    monkeypatch.setenv("PAPER_ORDER_STORE_START_ID", "0-0")
    monkeypatch.setenv("PAPER_ORDER_STORE_COUNT", "25")
    monkeypatch.setenv("PAPER_ORDER_STORE_BLOCK_MS", "2500")

    settings = Settings()

    assert settings.redis_url == "redis://redis:6379/0"
    assert settings.postgres_dsn_value() == "postgresql://db.internal/shrap"
    assert settings.start_id == "0-0"
    assert settings.count == 25
    assert settings.block_ms == 2500


def test_settings_redacted_output_is_log_safe() -> None:
    from shrap.agents.trading_floor.paper_order_store.config import Settings

    settings = Settings(
        redis_url="redis://redis:6379/0",
        postgres_dsn=SecretStr("postgresql://db.internal/shrap"),
    )

    assert settings.redacted() == {
        "service_name": "paper-order-store",
        "instance_id": settings.instance_id,
        "redis_url": "redis://redis:6379/0",
        "postgres_dsn": "***",
        "start_id": "0-0",
        "count": 100,
        "block_ms": 5000,
        "retry_delay_seconds": 1.0,
        "log_level": "INFO",
    }


def test_console_script_and_optional_extra_are_registered() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert pyproject["project"]["scripts"]["shrap-paper-order-store"] == (
        "shrap.agents.trading_floor.paper_order_store.__main__:main"
    )
    assert "asyncpg>=0.29" in pyproject["project"]["optional-dependencies"]["paper-order-store"]
    assert (
        "pydantic-settings>=2.4"
        in pyproject["project"]["optional-dependencies"]["paper-order-store"]
    )


def test_compose_defines_paper_order_store_service() -> None:
    compose = Path("infra/docker-compose.yml").read_text()

    assert "paper-order-store:" in compose
    assert "container_name: shrap_paper_order_store" in compose
    assert "dockerfile: infra/paper-order-store.Dockerfile" in compose
    assert "PAPER_ORDER_STORE_REDIS_URL" in compose
    assert "PAPER_ORDER_STORE_POSTGRES_DSN" in compose
    assert "trading.paper_order_events" in compose


def test_dockerfile_runs_paper_order_store_console_script() -> None:
    dockerfile = Path("infra/paper-order-store.Dockerfile").read_text()

    assert "asyncpg>=0.29" in dockerfile
    assert 'CMD ["shrap-paper-order-store"]' in dockerfile
