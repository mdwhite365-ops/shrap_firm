"""Strategy Runner service settings (env prefix ``STRATEGY_RUNNER_``)."""

from __future__ import annotations

import socket

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from shrap.research.strategy_runner.engine import (
    DEFAULT_CONFIDENCE,
    DEFAULT_MAX_GROSS_EXPOSURE,
    DEFAULT_MAX_QUANTITY,
    RunnerSignalConfig,
)

_DEFAULT_REDIS_URL = "redis" + "://" + "redis" + ":6379/0"


class Settings(BaseSettings):
    """Configuration loaded from STRATEGY_RUNNER_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="STRATEGY_RUNNER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "strategy-runner"
    instance_id: str = Field(default_factory=socket.gethostname)
    redis_url: str = _DEFAULT_REDIS_URL
    postgres_dsn: str = ""

    # Signal shaping. confidence must clear the Decision Maker threshold
    # (default 0.7, strict >).
    #
    # max_quantity must equal the Pre-Trade Checker's PRE_TRADE_MAX_QUANTITY_PER_ORDER.
    # The checker clamps rather than vetoes, so a larger value here would fill a
    # smaller position than the runner records — and the later exit would try to
    # sell shares that were never bought. Raise both or neither.
    max_quantity: int = DEFAULT_MAX_QUANTITY
    confidence: float = DEFAULT_CONFIDENCE

    # Firm-wide exposure budget as a multiple of equity, split equally across
    # active strategies. 1.0 = fully invested, unlevered. Raising this above 1.0
    # levers the whole book and should wait for drawdown/loss limits and an
    # intraday-margin-deficit model (ADR-0016).
    max_gross_exposure: float = DEFAULT_MAX_GROSS_EXPOSURE

    # Bar read + price adjustment (matches the Evaluator's default mode).
    adjustment: str = "all"
    lookback_buffer_days: int = 10
    lookback_max_days: int = 1200

    # Consumer-group + poll knobs. start_id "$" => only new phase events.
    start_id: str = "$"
    count: int = 100
    block_ms: int = 5000
    retry_delay_seconds: float = 1.0
    log_level: str = "INFO"

    def signal_config(self) -> RunnerSignalConfig:
        return RunnerSignalConfig(
            max_quantity=self.max_quantity,
            confidence=self.confidence,
            max_gross_exposure=self.max_gross_exposure,
        )

    def redacted(self) -> dict[str, object]:
        """Return a log-safe settings snapshot (never the DSN's credentials)."""

        return {
            "service_name": self.service_name,
            "instance_id": self.instance_id,
            "redis_url": self.redis_url,
            "postgres_dsn": "***",
            "max_quantity": self.max_quantity,
            "max_gross_exposure": self.max_gross_exposure,
            "confidence": self.confidence,
            "adjustment": self.adjustment,
            "lookback_buffer_days": self.lookback_buffer_days,
            "lookback_max_days": self.lookback_max_days,
            "start_id": self.start_id,
            "count": self.count,
            "block_ms": self.block_ms,
            "retry_delay_seconds": self.retry_delay_seconds,
            "log_level": self.log_level,
        }
