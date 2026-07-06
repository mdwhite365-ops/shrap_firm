"""Regime Classifier service settings."""

from __future__ import annotations

import socket

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from shrap.intelligence.market_data import AlpacaMarketDataSettings
from shrap.intelligence.regime.agent import RegimeRunConfig

_DEFAULT_REDIS_URL = "redis" + "://" + "redis" + ":6379/0"
_DEFAULT_DATA_ENDPOINT = "https://data.alpaca.markets"
_DEFAULT_SYMBOLS = "SPY,QQQ,IWM,HYG,TLT,AAPL,NVDA,TSLA,LMT"


def _default_postgres_dsn() -> str:
    return "postgresql://shrap:shrap@postgres:5432/shrap"


class Settings(BaseSettings):
    """Configuration loaded from REGIME_CLASSIFIER_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="REGIME_CLASSIFIER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "regime-classifier"
    instance_id: str = Field(default_factory=socket.gethostname)
    redis_url: str = _DEFAULT_REDIS_URL
    postgres_dsn: SecretStr = Field(default_factory=lambda: SecretStr(_default_postgres_dsn()))
    alpaca_api_key: str = ""
    alpaca_secret_key: SecretStr = SecretStr("")
    alpaca_data_endpoint: HttpUrl = HttpUrl(_DEFAULT_DATA_ENDPOINT)
    symbols: str = _DEFAULT_SYMBOLS
    primary_symbol: str = "SPY"
    credit_symbol: str = "HYG"
    rates_symbol: str = "TLT"
    lookback_days: int = 400
    debounce_m: int = 3
    epsilon: float = 0.05
    interval_seconds: float = 300.0
    retry_delay_seconds: float = 60.0
    log_level: str = "INFO"

    def market_data_settings(self) -> AlpacaMarketDataSettings:
        """Build data-host-only Alpaca settings."""

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
        required = {self.primary_symbol.upper(), self.credit_symbol.upper()}
        required.add(self.rates_symbol.upper())
        missing = required - set(symbols)
        if missing:
            raise ValueError(
                f"symbols must include primary/credit/rates symbols: {sorted(missing)}"
            )
        return symbols

    def run_config(self) -> RegimeRunConfig:
        return RegimeRunConfig(
            symbols=self.symbol_list(),
            primary_symbol=self.primary_symbol.upper(),
            credit_symbol=self.credit_symbol.upper(),
            rates_symbol=self.rates_symbol.upper(),
            lookback_days=self.lookback_days,
            debounce_m=self.debounce_m,
            epsilon=self.epsilon,
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
            "primary_symbol": self.primary_symbol,
            "credit_symbol": self.credit_symbol,
            "rates_symbol": self.rates_symbol,
            "lookback_days": self.lookback_days,
            "debounce_m": self.debounce_m,
            "epsilon": self.epsilon,
            "interval_seconds": self.interval_seconds,
            "retry_delay_seconds": self.retry_delay_seconds,
            "log_level": self.log_level,
        }
