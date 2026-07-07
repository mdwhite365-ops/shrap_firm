from __future__ import annotations

import tomllib
from pathlib import Path


def test_settings_builds_policy_from_prefixed_environment(monkeypatch) -> None:
    from shrap.agents.risk_compliance.pre_trade_checker.config import Settings
    from shrap.risk_compliance.pre_trade import RiskPolicy

    monkeypatch.setenv("PRE_TRADE_CHECKER_ALLOWED_UNIVERSE", "aapl, nvda, qqq")
    monkeypatch.setenv("PRE_TRADE_CHECKER_MAX_QUANTITY_PER_ORDER", "7")
    monkeypatch.setenv("PRE_TRADE_CHECKER_KILL_SWITCH_ACTIVE", "true")
    monkeypatch.setenv("PRE_TRADE_CHECKER_START_ID", "0-0")

    settings = Settings()
    policy = settings.policy()

    assert isinstance(policy, RiskPolicy)
    assert policy.allowed_universe == {"AAPL", "NVDA", "QQQ"}
    assert policy.max_quantity_per_order == 7
    assert policy.kill_switch_active is True
    assert settings.start_id == "0-0"


def test_settings_redacted_output_is_log_safe() -> None:
    from shrap.agents.risk_compliance.pre_trade_checker.config import Settings

    settings = Settings(redis_url="redis://redis:6379/0", allowed_universe="AAPL,SPY")

    assert settings.redacted() == {
        "service_name": "pre-trade-checker",
        "instance_id": settings.instance_id,
        "redis_url": "redis://redis:6379/0",
        "allowed_universe": ["AAPL", "SPY"],
        "max_quantity_per_order": 1,
        "kill_switch_active": False,
        "max_orders_per_day": 10,
        "symbol_cooldown_seconds": 300,
        "start_id": "0-0",
        "count": 100,
        "block_ms": 5000,
        "retry_delay_seconds": 1.0,
        "log_level": "INFO",
    }


def test_console_script_is_registered() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert pyproject["project"]["scripts"]["shrap-pre-trade-checker"] == (
        "shrap.agents.risk_compliance.pre_trade_checker.__main__:main"
    )


def test_compose_defines_pre_trade_checker_service() -> None:
    compose = Path("infra/docker-compose.yml").read_text()

    assert "pre-trade-checker:" in compose
    assert "container_name: shrap_pre_trade_checker" in compose
    assert "dockerfile: infra/pre-trade-checker.Dockerfile" in compose
    assert "PRE_TRADE_CHECKER_REDIS_URL" in compose
    assert "PRE_TRADE_CHECKER_ALLOWED_UNIVERSE" in compose
