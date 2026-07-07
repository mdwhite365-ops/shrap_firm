"""Entrypoint for `shrap-strategy-fixture`."""

from __future__ import annotations

import asyncio

import structlog

from shrap.agents.research.strategy_fixture.config import Settings
from shrap.agents.research.strategy_fixture.runner import run
from shrap.common.logging import configure_logging

log = structlog.get_logger(__name__)


def main() -> None:
    """Run the Strategy Fixture from environment settings."""

    settings = Settings()
    configure_logging(settings.service_name, settings.log_level)
    log.info("strategy_fixture.config_loaded", **settings.redacted())
    asyncio.run(
        run(
            redis_url=settings.redis_url,
            config=settings.fixture_config(),
            enabled=settings.enabled,
            service_name=settings.service_name,
            log_level=settings.log_level,
            interval_seconds=settings.interval_seconds,
        )
    )


if __name__ == "__main__":
    main()
