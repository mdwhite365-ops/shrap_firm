"""Hypothesis Generator trigger settings.

Note what is *absent*, on the same principle as the Evaluator trigger's config:
nothing here can widen what the proposer is allowed to say. The identity key
that caps proposals at one lineage root per implemented effect, the requirement
for a cited prior, the parameter bounds — all live in code, because a deployment
knob that relaxed any of them would make "propose until something passes" an
env-var change on the production box.

What *is* here is throughput and a kill switch.
"""

from __future__ import annotations

import socket

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from shrap.llm.registry import TIER_LOCAL_HEAVY
from shrap.research.hypothesis_generator.trigger_service import (
    DEFAULT_MAX_ITEMS,
    DEFAULT_SWEEP_INTERVAL_SECONDS,
)

_DEFAULT_REDIS_URL = "redis" + "://" + "redis" + ":6379/0"


def _default_postgres_dsn() -> str:
    return "postgresql://shrap:shrap@postgres:5432/shrap"


class Settings(BaseSettings):
    """Configuration loaded from HYPOTHESIS_GENERATOR_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="HYPOTHESIS_GENERATOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "hypothesis-generator"
    instance_id: str = Field(default_factory=socket.gethostname)
    redis_url: str = _DEFAULT_REDIS_URL
    postgres_dsn: SecretStr = Field(default_factory=lambda: SecretStr(_default_postgres_dsn()))
    sweep_interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS
    max_items: int = DEFAULT_MAX_ITEMS
    tier: str = TIER_LOCAL_HEAVY
    dry_run: bool = False
    """The kill switch. The sweep still runs and still reports; it writes
    nothing. Safe to leave off because the proposer's output is bounded by the
    scorer library rather than by how long it runs — see the trigger service."""

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
            "sweep_interval_seconds": self.sweep_interval_seconds,
            "max_items": self.max_items,
            "tier": self.tier,
            "dry_run": self.dry_run,
            "log_level": self.log_level,
        }
