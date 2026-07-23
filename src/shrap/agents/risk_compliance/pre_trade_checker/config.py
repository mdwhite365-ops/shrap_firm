"""Pre-Trade Checker service settings."""

from __future__ import annotations

import socket
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shrap.risk_compliance.pre_trade import RiskPolicy
from shrap.risk_compliance.rate_limit import RateLimitConfig

_DEFAULT_REDIS_URL = "redis" + "://" + "redis" + ":6379/0"
_DEFAULT_ALLOWED_UNIVERSE = "AAPL,NVDA,QQQ,SPY,TSLA,LMT"


def _default_postgres_dsn() -> str:
    return "postgresql://shrap:shrap@postgres:5432/shrap"


class Settings(BaseSettings):
    """Configuration loaded from PRE_TRADE_CHECKER_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="PRE_TRADE_CHECKER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "pre-trade-checker"
    instance_id: str = Field(default_factory=socket.gethostname)
    redis_url: str = _DEFAULT_REDIS_URL
    allowed_universe: str | list[str] = _DEFAULT_ALLOWED_UNIVERSE
    max_quantity_per_order: int = 1
    kill_switch_active: bool = False
    max_orders_per_day: int = 10
    symbol_cooldown_seconds: int = 300
    # Tier 3 membership enforcement (ADR-0012). Default off: nothing populates
    # research.universe_tiers until the Universe Curator's first card lands and
    # DQ-004 locks the launch list — enforcing against an empty table would
    # reject every order, including the live smoke path. Flipping this on is an
    # explicit human decision; once on, unavailable tier state fails closed.
    tier3_enforcement: bool = False
    postgres_dsn: SecretStr = Field(default_factory=lambda: SecretStr(_default_postgres_dsn()))
    tier3_cache_ttl_seconds: float = 30.0
    start_id: str = "0-0"
    count: int = 100
    block_ms: int = 5000
    retry_delay_seconds: float = 1.0
    log_level: str = "INFO"

    @field_validator("allowed_universe", mode="before")
    @classmethod
    def _normalize_allowed_universe(cls, value: Any) -> str | list[str]:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return [str(item).strip().upper() for item in value if str(item).strip()]
        raise TypeError("allowed_universe must be a comma-separated string or list")

    def allowed_universe_set(self) -> set[str]:
        """Return the configured ticker universe as normalized symbols."""

        if isinstance(self.allowed_universe, str):
            raw = self.allowed_universe.split(",")
        else:
            raw = self.allowed_universe
        return {ticker.strip().upper() for ticker in raw if ticker.strip()}

    def policy(self) -> RiskPolicy:
        """Build the deterministic Month 1 risk policy."""

        return RiskPolicy(
            allowed_universe=self.allowed_universe_set(),
            max_quantity_per_order=self.max_quantity_per_order,
            kill_switch_active=self.kill_switch_active,
        )

    def rate_limit_config(self) -> RateLimitConfig:
        """Build the Redis-backed order-rate guardrail config."""

        return RateLimitConfig(
            max_orders_per_day=self.max_orders_per_day,
            symbol_cooldown_seconds=self.symbol_cooldown_seconds,
        )

    def postgres_dsn_value(self) -> str:
        """Return the DB DSN for connection setup without exposing it in repr/logs."""

        return self.postgres_dsn.get_secret_value()

    def redacted(self) -> dict[str, object]:
        """Return a log-safe settings snapshot."""

        return {
            "service_name": self.service_name,
            "instance_id": self.instance_id,
            "redis_url": self.redis_url,
            "allowed_universe": sorted(self.allowed_universe_set()),
            "max_quantity_per_order": self.max_quantity_per_order,
            "kill_switch_active": self.kill_switch_active,
            "max_orders_per_day": self.max_orders_per_day,
            "symbol_cooldown_seconds": self.symbol_cooldown_seconds,
            "tier3_enforcement": self.tier3_enforcement,
            "postgres_dsn": "***",
            "tier3_cache_ttl_seconds": self.tier3_cache_ttl_seconds,
            "start_id": self.start_id,
            "count": self.count,
            "block_ms": self.block_ms,
            "retry_delay_seconds": self.retry_delay_seconds,
            "log_level": self.log_level,
        }
