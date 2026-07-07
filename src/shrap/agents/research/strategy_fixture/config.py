"""Strategy Fixture service settings."""

from __future__ import annotations

import socket

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from shrap.research.strategy_fixture import FixtureConfig

_DEFAULT_REDIS_URL = "redis" + "://" + "redis" + ":6379/0"
_DEFAULT_ALLOWED_LABELS = "crisis-recovery,late-cycle-melt-up"


class Settings(BaseSettings):
    """Configuration loaded from STRATEGY_FIXTURE_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="STRATEGY_FIXTURE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "strategy-fixture"
    instance_id: str = Field(default_factory=socket.gethostname)
    redis_url: str = _DEFAULT_REDIS_URL
    enabled: bool = False
    ticker: str = "SPY"
    side: str = "buy"
    quantity: int = 1
    allowed_regime_labels: str = _DEFAULT_ALLOWED_LABELS
    max_signals_per_day: int = 1
    interval_seconds: float = 600.0
    log_level: str = "INFO"

    def fixture_config(self) -> FixtureConfig:
        labels = tuple(
            label.strip() for label in self.allowed_regime_labels.split(",") if label.strip()
        )
        return FixtureConfig(
            ticker=self.ticker.upper(),
            side=self.side.lower(),
            quantity=self.quantity,
            allowed_regime_labels=labels,
            max_signals_per_day=self.max_signals_per_day,
        )

    def redacted(self) -> dict[str, object]:
        """Return a log-safe settings snapshot."""

        return {
            "service_name": self.service_name,
            "instance_id": self.instance_id,
            "redis_url": self.redis_url,
            "enabled": self.enabled,
            "ticker": self.ticker,
            "side": self.side,
            "quantity": self.quantity,
            "allowed_regime_labels": self.allowed_regime_labels,
            "max_signals_per_day": self.max_signals_per_day,
            "interval_seconds": self.interval_seconds,
            "log_level": self.log_level,
        }
