"""Backfill CLI settings (``MARKET_DATA_*`` env vars).

Operational knobs (DB DSN, feed, adjustment, throttle) load from the
``MARKET_DATA_`` prefix. Broker/data credentials deliberately do **not**: they
come from :class:`shrap.intelligence.market_data.AlpacaMarketDataSettings`,
which reads the bare ``ALPACA_API_KEY`` / ``ALPACA_SECRET_KEY`` /
``ALPACA_DATA_ENDPOINT`` names the rest of the firm's data-host clients already
use. Reusing those names keeps one credential source of truth and never
re-exports secrets under a second prefix.
"""

from __future__ import annotations

import socket

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from shrap.intelligence.market_data import AlpacaMarketDataSettings
from shrap.market_data.client import ADJUSTMENT_ALL, IEX_FEED


def _default_postgres_dsn() -> str:
    return "postgresql://shrap:shrap@postgres:5432/shrap"


class Settings(BaseSettings):
    """Configuration loaded from ``MARKET_DATA_*`` env vars."""

    model_config = SettingsConfigDict(
        env_prefix="MARKET_DATA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "market-data-backfill"
    instance_id: str = Field(default_factory=socket.gethostname)
    postgres_dsn: SecretStr = Field(default_factory=lambda: SecretStr(_default_postgres_dsn()))
    feed: str = IEX_FEED
    adjustment: str = ADJUSTMENT_ALL
    request_limit: int = 10000
    inter_ticker_delay_seconds: float = 0.3
    http_timeout: float = 30.0
    log_level: str = "INFO"

    def postgres_dsn_value(self) -> str:
        """Return the DB DSN for connection setup without exposing it in repr/logs."""

        return self.postgres_dsn.get_secret_value()

    # NOTE: TriggerSettings below reuses this class's fields and adds the sweep
    # knobs. It is a subclass rather than a copy so that a change to the feed,
    # adjustment or throttle applies to the scheduled path and the hand-run one
    # identically — two settings classes that drifted would mean the automated
    # sweep quietly fetching under different terms than a manual backfill.

    def market_data_settings(self) -> AlpacaMarketDataSettings:
        """Data-host-only Alpaca credentials from the shared ``ALPACA_*`` env names."""

        return AlpacaMarketDataSettings()

    def redacted(self) -> dict[str, object]:
        """Return a log-safe settings snapshot (never secret values)."""

        return {
            "service_name": self.service_name,
            "instance_id": self.instance_id,
            "postgres_dsn": "***",
            "alpaca": self.market_data_settings().redacted(),
            "feed": self.feed,
            "adjustment": self.adjustment,
            "request_limit": self.request_limit,
            "inter_ticker_delay_seconds": self.inter_ticker_delay_seconds,
            "http_timeout": self.http_timeout,
            "log_level": self.log_level,
        }


class TriggerSettings(Settings):
    """Configuration for the scheduled sweep (``MARKET_DATA_TRIGGER_*`` env vars).

    Inherits every field above so the automated path fetches on exactly the same
    terms as a hand-run backfill, and adds only the scheduling knobs.
    """

    model_config = SettingsConfigDict(
        env_prefix="MARKET_DATA_TRIGGER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "market-data-trigger"

    # Daily bars change once per session, so the interval buys latency after the
    # close rather than resolution. Six hours guarantees a post-close pull within
    # six hours on every trading day at four passes daily — 50 requests a pass
    # against a box that runs every agent at 0.00% CPU.
    sweep_interval_seconds: float = 21600.0

    # adjustment=all means splits and dividends restate history, so the newest
    # bars are not final when first written. Five days re-requests the window
    # where a correction is still plausible; the upsert is idempotent, so the
    # cost is a few refreshed rows per ticker and the alternative is being
    # quietly wrong about a split.
    restate_days: int = 5

    # Used only for a ticker the store has never seen — a name newly added to
    # the universe. Matches the backfill CLI's default lookback so an onboarded
    # name gets the same history a hand-run backfill would have given it.
    bootstrap_days: int = 5 * 365


__all__ = ["Settings", "TriggerSettings"]
