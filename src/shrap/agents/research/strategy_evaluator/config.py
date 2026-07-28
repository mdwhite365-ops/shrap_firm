"""Strategy Evaluator trigger service settings.

Note what is *absent*: ``min_trades`` and ``sharpe_floor``. Those are the
evaluation protocol (``docs/research/eval-protocol.md``), not deployment
configuration. Exposing them here would make "lower the gate until something
passes" a one-line env change on the production box, which is precisely the
failure the protocol's no-human-tuning rule exists to prevent.
"""

from __future__ import annotations

import socket

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_REDIS_URL = "redis" + "://" + "redis" + ":6379/0"


def _default_postgres_dsn() -> str:
    return "postgresql://shrap:shrap@postgres:5432/shrap"


class Settings(BaseSettings):
    """Configuration loaded from STRATEGY_EVALUATOR_TRIGGER_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="STRATEGY_EVALUATOR_TRIGGER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "strategy-evaluator-trigger"
    instance_id: str = Field(default_factory=socket.gethostname)
    redis_url: str = _DEFAULT_REDIS_URL
    postgres_dsn: SecretStr = Field(default_factory=lambda: SecretStr(_default_postgres_dsn()))
    card_root: str = "/cards"
    sweep_interval_seconds: float = 900.0
    reeval_interval_hours: float = 24.0
    log_level: str = "INFO"

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
            "card_root": self.card_root,
            "sweep_interval_seconds": self.sweep_interval_seconds,
            "reeval_interval_hours": self.reeval_interval_hours,
            "log_level": self.log_level,
        }
