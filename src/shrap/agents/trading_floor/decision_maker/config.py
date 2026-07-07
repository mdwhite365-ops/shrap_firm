"""Decision Maker stub service settings."""

from __future__ import annotations

import socket

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_REDIS_URL = "redis" + "://" + "redis" + ":6379/0"


class Settings(BaseSettings):
    """Configuration loaded from DECISION_MAKER_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="DECISION_MAKER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "decision-maker"
    instance_id: str = Field(default_factory=socket.gethostname)
    redis_url: str = _DEFAULT_REDIS_URL
    start_id: str = "$"
    count: int = 100
    block_ms: int = 5000
    retry_delay_seconds: float = 1.0
    confidence_threshold: float = 0.7
    log_level: str = "INFO"

    def redacted(self) -> dict[str, object]:
        """Return a log-safe settings snapshot."""

        return {
            "service_name": self.service_name,
            "instance_id": self.instance_id,
            "redis_url": self.redis_url,
            "start_id": self.start_id,
            "count": self.count,
            "block_ms": self.block_ms,
            "retry_delay_seconds": self.retry_delay_seconds,
            "confidence_threshold": self.confidence_threshold,
            "log_level": self.log_level,
        }
