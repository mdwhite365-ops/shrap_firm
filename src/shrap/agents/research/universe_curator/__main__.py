"""Entrypoint for `shrap-universe-curator`."""

from __future__ import annotations

import asyncio

import structlog

from shrap.agents.research.universe_curator.config import Settings
from shrap.common.logging import configure_logging
from shrap.research.universe_curator.service import run

log = structlog.get_logger(__name__)


def main() -> None:
    """Run the Universe Curator watch-expiry sweep from environment settings."""

    settings = Settings()
    configure_logging(settings.service_name, settings.log_level)
    log.info("universe_curator.config_loaded", **settings.redacted())
    asyncio.run(
        run(
            redis_url=settings.redis_url,
            postgres_dsn=settings.postgres_dsn_value(),
            sweep_interval_seconds=settings.sweep_interval_seconds,
            service_name=settings.service_name,
            log_level=settings.log_level,
        )
    )


if __name__ == "__main__":
    main()
