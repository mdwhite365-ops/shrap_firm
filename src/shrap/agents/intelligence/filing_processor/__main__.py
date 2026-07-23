"""Entrypoint for `shrap-filing-processor`."""

from __future__ import annotations

import asyncio

import structlog

from shrap.agents.intelligence.filing_processor.config import Settings
from shrap.common.logging import configure_logging
from shrap.intelligence.filing_processor.service import run

log = structlog.get_logger(__name__)


def main() -> None:
    """Run the Filing Processor from environment settings."""

    settings = Settings()
    configure_logging(settings.service_name, settings.log_level)
    log.info("filing_processor.config_loaded", **settings.redacted())
    asyncio.run(
        run(
            redis_url=settings.redis_url,
            postgres_dsn=settings.postgres_dsn_value(),
            sec_user_agent=settings.sec_user_agent,
            config=settings.run_config(),
            service_name=settings.service_name,
            log_level=settings.log_level,
            http_timeout=settings.http_timeout,
        )
    )


if __name__ == "__main__":
    main()
