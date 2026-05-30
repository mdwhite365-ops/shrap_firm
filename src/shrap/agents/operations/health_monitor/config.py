"""Health Monitor settings — pydantic-settings, loaded from env / .env."""

from __future__ import annotations

import socket

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_REDIS_URL = "redis" + "://" + "redis" + ":6379/0"
_DEFAULT_PROM_URL = "http" + "://" + "prometheus" + ":9090"


class Settings(BaseSettings):
    """Health Monitor configuration.

    All fields overridable via HEALTH_MONITOR_* env vars (pydantic-settings prefix).
    """

    model_config = SettingsConfigDict(
        env_prefix="HEALTH_MONITOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "health-monitor"
    instance_id: str = Field(default_factory=socket.gethostname)
    redis_url: str = _DEFAULT_REDIS_URL
    prom_url: str = _DEFAULT_PROM_URL
    tick_interval_seconds: int = 30
    discord_webhook_url: SecretStr | None = None
    ntfy_url: str | None = None
    log_level: str = "INFO"
    degradation_threshold_consecutive_ticks: int = 2
    recovery_threshold_consecutive_ticks: int = 3
    dry_run: bool = False

    def produced_by(self) -> str:
        return f"{self.service_name}@{self.instance_id}"

    def redacted(self) -> dict[str, object]:
        """Return a dict safe for log/event emission (secrets masked)."""
        return {
            "service_name": self.service_name,
            "instance_id": self.instance_id,
            "redis_url": self.redis_url,
            "prom_url": self.prom_url,
            "tick_interval_seconds": self.tick_interval_seconds,
            "discord_webhook_url": "***" if self.discord_webhook_url else None,
            "ntfy_url": self.ntfy_url,
            "log_level": self.log_level,
            "degradation_threshold_consecutive_ticks": (
                self.degradation_threshold_consecutive_ticks
            ),
            "recovery_threshold_consecutive_ticks": self.recovery_threshold_consecutive_ticks,
            "dry_run": self.dry_run,
        }
