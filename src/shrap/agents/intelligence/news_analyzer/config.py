"""News Analyzer service settings."""

from __future__ import annotations

import socket

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from shrap.intelligence.market_data import AlpacaMarketDataSettings
from shrap.intelligence.news_analyzer.client import NEWS_SOURCE
from shrap.intelligence.news_analyzer.service import NewsRunConfig

_DEFAULT_REDIS_URL = "redis" + "://" + "redis" + ":6379/0"
_DEFAULT_DATA_ENDPOINT = "https://data.alpaca.markets"
# Placeholder for the Tier 3 launch names (ADR-0012): the Regime Classifier's
# default set until the Universe Curator owns Tier 3 state. Overridden by
# NEWS_ANALYZER_SYMBOLS in the deployed env.
_DEFAULT_SYMBOLS = "SPY,QQQ,IWM,HYG,TLT,AAPL,NVDA,TSLA,LMT"


def _default_postgres_dsn() -> str:
    return "postgresql://shrap:shrap@postgres:5432/shrap"


class Settings(BaseSettings):
    """Configuration loaded from NEWS_ANALYZER_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="NEWS_ANALYZER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "news-analyzer"
    instance_id: str = Field(default_factory=socket.gethostname)
    redis_url: str = _DEFAULT_REDIS_URL
    postgres_dsn: SecretStr = Field(default_factory=lambda: SecretStr(_default_postgres_dsn()))
    alpaca_api_key: str = ""
    alpaca_secret_key: SecretStr = SecretStr("")
    alpaca_data_endpoint: HttpUrl = HttpUrl(_DEFAULT_DATA_ENDPOINT)
    symbols: str = _DEFAULT_SYMBOLS
    feed: str = NEWS_SOURCE
    lookback_days: int = 3
    page_limit: int = 50
    score_max_items: int = 300
    escalation_threshold: int = 2
    publish_threshold: int = 1
    active_interval_seconds: float = 600.0
    idle_interval_seconds: float = 3600.0
    http_timeout: float = 60.0
    log_level: str = "INFO"

    def market_data_settings(self) -> AlpacaMarketDataSettings:
        """Build data-host-only Alpaca settings (news uses the data host)."""

        return AlpacaMarketDataSettings(
            api_key=self.alpaca_api_key,
            secret_key=self.alpaca_secret_key,
            endpoint=self.alpaca_data_endpoint,
        )

    def postgres_dsn_value(self) -> str:
        """Return the DB DSN for connection setup without exposing it in repr/logs."""

        return self.postgres_dsn.get_secret_value()

    def symbol_list(self) -> tuple[str, ...]:
        symbols = tuple(
            symbol.strip().upper() for symbol in self.symbols.split(",") if symbol.strip()
        )
        if not symbols:
            raise ValueError("NEWS_ANALYZER_SYMBOLS must list at least one symbol")
        return symbols

    def produced_by(self) -> str:
        return f"{self.service_name}@{self.instance_id}"

    def run_config(self) -> NewsRunConfig:
        return NewsRunConfig(
            symbols=self.symbol_list(),
            feed=self.feed,
            lookback_days=self.lookback_days,
            page_limit=self.page_limit,
            score_max_items=self.score_max_items,
            escalation_threshold=self.escalation_threshold,
            publish_threshold=self.publish_threshold,
            active_interval_seconds=self.active_interval_seconds,
            idle_interval_seconds=self.idle_interval_seconds,
        )

    def redacted(self) -> dict[str, object]:
        """Return a log-safe settings snapshot."""

        return {
            "service_name": self.service_name,
            "instance_id": self.instance_id,
            "redis_url": self.redis_url,
            "postgres_dsn": "***",
            "alpaca": self.market_data_settings().redacted(),
            "symbols": self.symbols,
            "feed": self.feed,
            "lookback_days": self.lookback_days,
            "page_limit": self.page_limit,
            "score_max_items": self.score_max_items,
            "escalation_threshold": self.escalation_threshold,
            "publish_threshold": self.publish_threshold,
            "active_interval_seconds": self.active_interval_seconds,
            "idle_interval_seconds": self.idle_interval_seconds,
            "http_timeout": self.http_timeout,
            "log_level": self.log_level,
        }
