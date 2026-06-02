"""Audit Logger settings."""

from __future__ import annotations

import socket

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_REDIS_URL = "redis" + "://" + "redis" + ":6379/0"
_DEFAULT_POSTGRES_DSN = (
    "postgresql" + "://" + "shrap:change-me-strong-random-value@postgres:5432/shrap"
)


class Settings(BaseSettings):
    """Configuration loaded from AUDIT_LOGGER_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="AUDIT_LOGGER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "audit-logger"
    instance_id: str = Field(default_factory=socket.gethostname)
    redis_url: str = _DEFAULT_REDIS_URL
    postgres_dsn: str = _DEFAULT_POSTGRES_DSN
    stream_pattern: str = "*"
    start_id: str = "0-0"
    read_count: int = 100
    block_ms: int = 5000
    retry_delay_seconds: float = 5.0
    log_level: str = "INFO"

    def redacted(self) -> dict[str, object]:
        return {
            "service_name": self.service_name,
            "instance_id": self.instance_id,
            "redis_url": self.redis_url,
            "postgres_dsn": "***",
            "stream_pattern": self.stream_pattern,
            "start_id": self.start_id,
            "read_count": self.read_count,
            "block_ms": self.block_ms,
            "retry_delay_seconds": self.retry_delay_seconds,
            "log_level": self.log_level,
        }
