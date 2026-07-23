"""Filing Processor service settings."""

from __future__ import annotations

import socket

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from shrap.intelligence.filing_processor.client import parse_roster
from shrap.intelligence.filing_processor.service import DEFAULT_FEED, FilingRunConfig

_DEFAULT_REDIS_URL = "redis" + "://" + "redis" + ":6379/0"

# SEC asks automated clients to identify themselves with contact info; same
# convention as the Tech Watcher's EdgarSource, scoped to this agent.
_DEFAULT_SEC_USER_AGENT = "shrap-firm/0.1 filing-processor (mdwhite365@gmail.com)"

# Placeholder Tier 3 roster (ADR-0012), TICKER:CIK keyed by CIK because EDGAR
# resolution is CIK-based. These four single-name equities carry the firm's
# launch names with their public EDGAR CIKs; overridden by
# FILING_PROCESSOR_ROSTER in the deployed env, and superseded by the Universe
# Curator's Tier 3 state when that exists (mirrors the News Analyzer's symbols
# placeholder — real calibration comes from live batches, not spec time).
_DEFAULT_ROSTER = "AAPL:320193,NVDA:1045810,TSLA:1318605,LMT:936468"


def _default_postgres_dsn() -> str:
    return "postgresql://shrap:shrap@postgres:5432/shrap"


class Settings(BaseSettings):
    """Configuration loaded from FILING_PROCESSOR_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="FILING_PROCESSOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "filing-processor"
    instance_id: str = Field(default_factory=socket.gethostname)
    redis_url: str = _DEFAULT_REDIS_URL
    postgres_dsn: SecretStr = Field(default_factory=lambda: SecretStr(_default_postgres_dsn()))
    sec_user_agent: str = _DEFAULT_SEC_USER_AGENT
    roster: str = _DEFAULT_ROSTER
    feed: str = DEFAULT_FEED
    poll_max_items: int = 200
    fetch_max_items: int = 50
    score_max_items: int = 100
    escalation_threshold: int = 2
    publish_threshold: int = 1
    fetch_throttle_seconds: float = 0.2
    active_interval_seconds: float = 600.0
    idle_interval_seconds: float = 3600.0
    http_timeout: float = 30.0
    log_level: str = "INFO"

    def postgres_dsn_value(self) -> str:
        """Return the DB DSN for connection setup without exposing it in repr/logs."""

        return self.postgres_dsn.get_secret_value()

    def run_config(self) -> FilingRunConfig:
        roster = parse_roster(self.roster)
        if len(roster) == 0:
            raise ValueError("FILING_PROCESSOR_ROSTER must list at least one TICKER:CIK pair")
        return FilingRunConfig(
            roster=roster,
            feed=self.feed,
            poll_max_items=self.poll_max_items,
            fetch_max_items=self.fetch_max_items,
            score_max_items=self.score_max_items,
            escalation_threshold=self.escalation_threshold,
            publish_threshold=self.publish_threshold,
            fetch_throttle_seconds=self.fetch_throttle_seconds,
            http_timeout=self.http_timeout,
            active_interval_seconds=self.active_interval_seconds,
            idle_interval_seconds=self.idle_interval_seconds,
        )

    def produced_by(self) -> str:
        return f"{self.service_name}@{self.instance_id}"

    def redacted(self) -> dict[str, object]:
        """Return a log-safe settings snapshot."""

        return {
            "service_name": self.service_name,
            "instance_id": self.instance_id,
            "redis_url": self.redis_url,
            "postgres_dsn": "***",
            "sec_user_agent": self.sec_user_agent,
            "roster": self.roster,
            "feed": self.feed,
            "poll_max_items": self.poll_max_items,
            "fetch_max_items": self.fetch_max_items,
            "score_max_items": self.score_max_items,
            "escalation_threshold": self.escalation_threshold,
            "publish_threshold": self.publish_threshold,
            "fetch_throttle_seconds": self.fetch_throttle_seconds,
            "active_interval_seconds": self.active_interval_seconds,
            "idle_interval_seconds": self.idle_interval_seconds,
            "http_timeout": self.http_timeout,
            "log_level": self.log_level,
        }
