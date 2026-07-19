"""Market Phase Scheduler settings — pydantic-settings, loaded from env / .env."""

from __future__ import annotations

import socket
from datetime import time

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_REDIS_URL = "redis" + "://" + "redis" + ":6379/0"


class Settings(BaseSettings):
    """Market Phase Scheduler configuration.

    All fields overridable via MARKET_PHASE_* env vars (pydantic-settings prefix).
    """

    model_config = SettingsConfigDict(
        env_prefix="MARKET_PHASE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "market-phase"
    instance_id: str = Field(default_factory=socket.gethostname)
    redis_url: str = _DEFAULT_REDIS_URL
    calendar_name: str = "XNYS"
    timezone_name: str = "America/New_York"
    pre_open_time: str = "04:00"
    extended_end_time: str = "20:00"
    lookbehind_days: int = 3
    lookahead_days: int = 10
    max_sleep_seconds: float = 900.0
    publish_retry_initial_seconds: float = 1.0
    publish_retry_max_seconds: float = 60.0
    log_level: str = "INFO"
    dry_run: bool = False

    def pre_open(self) -> time:
        return time.fromisoformat(self.pre_open_time)

    def extended_end(self) -> time:
        return time.fromisoformat(self.extended_end_time)

    def produced_by(self) -> str:
        return f"{self.service_name}@{self.instance_id}"

    def redacted(self) -> dict[str, object]:
        """Return a dict safe for log emission (this service holds no secrets)."""
        return {
            "service_name": self.service_name,
            "instance_id": self.instance_id,
            "redis_url": self.redis_url,
            "calendar_name": self.calendar_name,
            "timezone_name": self.timezone_name,
            "pre_open_time": self.pre_open_time,
            "extended_end_time": self.extended_end_time,
            "lookbehind_days": self.lookbehind_days,
            "lookahead_days": self.lookahead_days,
            "max_sleep_seconds": self.max_sleep_seconds,
            "publish_retry_initial_seconds": self.publish_retry_initial_seconds,
            "publish_retry_max_seconds": self.publish_retry_max_seconds,
            "log_level": self.log_level,
            "dry_run": self.dry_run,
        }
