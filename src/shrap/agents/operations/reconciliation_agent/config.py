"""Reconciliation Agent service settings."""

from __future__ import annotations

import socket

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from shrap.trading_floor.alpaca import AlpacaPaperSettings

_DEFAULT_REDIS_URL = "redis" + "://" + "redis" + ":6379/0"
_DEFAULT_ALPACA_ENDPOINT = "https://paper-api.alpaca.markets"


def _default_postgres_dsn() -> str:
    return "postgresql://shrap:shrap@postgres:5432/shrap"


class Settings(BaseSettings):
    """Configuration loaded from RECONCILIATION_AGENT_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="RECONCILIATION_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "reconciliation-agent"
    instance_id: str = Field(default_factory=socket.gethostname)
    redis_url: str = _DEFAULT_REDIS_URL
    postgres_dsn: SecretStr = Field(default_factory=lambda: SecretStr(_default_postgres_dsn()))
    alpaca_api_key: str = ""
    alpaca_secret_key: SecretStr = SecretStr("")
    alpaca_endpoint: HttpUrl = HttpUrl(_DEFAULT_ALPACA_ENDPOINT)
    broker: str = "alpaca-paper"
    order_status: str = "all"
    order_limit: int = 500
    interval_seconds: float = 300.0
    retry_delay_seconds: float = 30.0
    log_level: str = "INFO"

    def alpaca_settings(self) -> AlpacaPaperSettings:
        """Build paper-only Alpaca settings."""

        return AlpacaPaperSettings(
            api_key=self.alpaca_api_key,
            secret_key=self.alpaca_secret_key,
            endpoint=self.alpaca_endpoint,
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
            "postgres_dsn": "***",
            "alpaca": self.alpaca_settings().redacted(),
            "broker": self.broker,
            "order_status": self.order_status,
            "order_limit": self.order_limit,
            "interval_seconds": self.interval_seconds,
            "retry_delay_seconds": self.retry_delay_seconds,
            "log_level": self.log_level,
        }
