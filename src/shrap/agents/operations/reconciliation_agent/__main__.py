"""Entrypoint for `shrap-reconciliation-agent`."""

from __future__ import annotations

import asyncio

import structlog

from shrap.agents.operations.reconciliation_agent.config import Settings
from shrap.agents.operations.reconciliation_agent.runner import run
from shrap.common.logging import configure_logging

log = structlog.get_logger(__name__)


def main() -> None:
    """Run the Reconciliation Agent from environment settings."""

    settings = Settings()
    configure_logging(settings.service_name, settings.log_level)
    log.info("reconciliation_agent.config_loaded", **settings.redacted())
    asyncio.run(
        run(
            redis_url=settings.redis_url,
            postgres_dsn=settings.postgres_dsn_value(),
            alpaca_settings=settings.alpaca_settings(),
            service_name=settings.service_name,
            log_level=settings.log_level,
            broker=settings.broker,
            order_status=settings.order_status,
            order_limit=settings.order_limit,
            interval_seconds=settings.interval_seconds,
            retry_delay_seconds=settings.retry_delay_seconds,
        )
    )


if __name__ == "__main__":
    main()
