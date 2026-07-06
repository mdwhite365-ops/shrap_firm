"""Execution Agent service settings."""

from __future__ import annotations

import socket

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from shrap.trading_floor.alpaca import AlpacaPaperSettings

_DEFAULT_REDIS_URL = "redis" + "://" + "redis" + ":6379/0"
_DEFAULT_ALPACA_ENDPOINT = "https://paper-api.alpaca.markets"


class Settings(BaseSettings):
    """Configuration loaded from EXECUTION_AGENT_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="EXECUTION_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "execution-agent"
    instance_id: str = Field(default_factory=socket.gethostname)
    redis_url: str = _DEFAULT_REDIS_URL
    alpaca_api_key: str = ""
    alpaca_secret_key: SecretStr = SecretStr("")
    alpaca_endpoint: HttpUrl = HttpUrl(_DEFAULT_ALPACA_ENDPOINT)
    start_id: str = "0-0"
    count: int = 100
    block_ms: int = 5000
    retry_delay_seconds: float = 1.0
    status_poll_interval_seconds: float = 5.0
    log_level: str = "INFO"

    def alpaca_settings(self) -> AlpacaPaperSettings:
        """Build paper-only Alpaca settings."""

        return AlpacaPaperSettings(
            api_key=self.alpaca_api_key,
            secret_key=self.alpaca_secret_key,
            endpoint=self.alpaca_endpoint,
        )

    def redacted(self) -> dict[str, object]:
        """Return a log-safe settings snapshot."""

        return {
            "service_name": self.service_name,
            "instance_id": self.instance_id,
            "redis_url": self.redis_url,
            "alpaca": self.alpaca_settings().redacted(),
            "start_id": self.start_id,
            "count": self.count,
            "block_ms": self.block_ms,
            "retry_delay_seconds": self.retry_delay_seconds,
            "status_poll_interval_seconds": self.status_poll_interval_seconds,
            "log_level": self.log_level,
        }
