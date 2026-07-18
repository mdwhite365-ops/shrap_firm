"""Tech Watcher ingest service settings."""

from __future__ import annotations

import socket

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_REDIS_URL = "redis" + "://" + "redis" + ":6379/0"

# SEC asks automated clients to identify themselves with contact info.
_DEFAULT_SEC_USER_AGENT = "shrap-firm/0.1 tech-watcher (mdwhite365@gmail.com)"


def _default_postgres_dsn() -> str:
    return "postgresql://shrap:shrap@postgres:5432/shrap"


class Settings(BaseSettings):
    """Configuration loaded from TECH_WATCHER_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="TECH_WATCHER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "tech-watcher"
    instance_id: str = Field(default_factory=socket.gethostname)
    redis_url: str = _DEFAULT_REDIS_URL
    postgres_dsn: SecretStr = Field(default_factory=lambda: SecretStr(_default_postgres_dsn()))
    sec_user_agent: str = _DEFAULT_SEC_USER_AGENT
    edgar_forms: str = "10-K,10-Q,8-K"
    arxiv_categories: str = "cs.AI,cs.LG,cond-mat,q-bio.NC"
    max_results: int = 100
    interval_seconds: float = 3600.0
    http_timeout: float = 30.0
    log_level: str = "INFO"

    def postgres_dsn_value(self) -> str:
        """Return the DB DSN for connection setup without exposing it in repr/logs."""

        return self.postgres_dsn.get_secret_value()

    def edgar_forms_tuple(self) -> tuple[str, ...]:
        return tuple(f.strip() for f in self.edgar_forms.split(",") if f.strip())

    def arxiv_categories_tuple(self) -> tuple[str, ...]:
        return tuple(c.strip() for c in self.arxiv_categories.split(",") if c.strip())

    def redacted(self) -> dict[str, object]:
        """Return a log-safe settings snapshot."""

        return {
            "service_name": self.service_name,
            "instance_id": self.instance_id,
            "redis_url": self.redis_url,
            "postgres_dsn": "***",
            "sec_user_agent": self.sec_user_agent,
            "edgar_forms": self.edgar_forms,
            "arxiv_categories": self.arxiv_categories,
            "max_results": self.max_results,
            "interval_seconds": self.interval_seconds,
            "http_timeout": self.http_timeout,
            "log_level": self.log_level,
        }
