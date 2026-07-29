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
        "max_quantity_per_order": 100,
        "kill_switch_active": False,
        "max_orders_per_day": 80,
        "symbol_cooldown_seconds": 300,
        "tier3_enforcement": False,
        "postgres_dsn": "***",
        "tier3_cache_ttl_seconds": 30.0,
        "start_id": "0-0",
        "count": 100,
        "block_ms": 5000,
        "retry_delay_seconds": 1.0,
        "log_level": "INFO",
    }


# --- the $10k aggressive book (Mike's ruling, docs/status/session-handoff.md) --


def test_the_default_universe_is_the_launch_list_not_a_copy_of_it() -> None:
    """A copied ticker list is free to drift from the universe strategies are
    evaluated against. This one is imported, so it cannot."""

    from shrap.agents.risk_compliance.pre_trade_checker.config import Settings
    from shrap.research.universe_curator.launch_list import LAUNCH_LIST

    assert Settings().allowed_universe_set() == {name.ticker for name in LAUNCH_LIST}
    assert len(LAUNCH_LIST) == 50


def test_compose_does_not_pin_the_allowlist_to_an_empty_string() -> None:
    """`VAR: "${VAR:-}"` sets the variable to an EMPTY STRING when unset, which
    is an empty allowlist — every order vetoed — and overrides .env besides.

    The variable must stay absent from `environment:` so the imported default
    applies and .env can still override it.
    """

    compose = Path("infra/docker-compose.yml").read_text()
    assert 'PRE_TRADE_CHECKER_ALLOWED_UNIVERSE: "' not in compose


def test_the_two_per_order_caps_cannot_diverge_in_compose() -> None:
    """Both services read the *same* env var, so a raise applies to both or
    neither. A comment asking two numbers to stay equal is not a mechanism."""

    compose = Path("infra/docker-compose.yml").read_text()
    assert (
        'STRATEGY_RUNNER_MAX_QUANTITY: "${PRE_TRADE_CHECKER_MAX_QUANTITY_PER_ORDER:-100}"'
        in compose
    )


def test_the_daily_cap_covers_entering_the_whole_universe() -> None:
    """A 50-name universe entered from flat is 50 approvals. A cap below that
    would silently truncate a strategy's first session partway through."""

    from shrap.agents.risk_compliance.pre_trade_checker.config import Settings
    from shrap.research.universe_curator.launch_list import LAUNCH_LIST

    assert Settings().max_orders_per_day > len(LAUNCH_LIST)


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
