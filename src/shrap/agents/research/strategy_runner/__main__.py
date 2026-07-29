"""Entrypoint for `shrap-strategy-runner`."""

from __future__ import annotations

import asyncio

import structlog

from shrap.agents.research.strategy_runner.config import Settings
from shrap.agents.research.strategy_runner.runner import run
from shrap.common.logging import configure_logging

log = structlog.get_logger(__name__)


def main() -> None:
    """Run the Strategy Runner from environment settings."""

    settings = Settings()
    configure_logging(settings.service_name, settings.log_level)
    log.info("strategy_runner.config_loaded", **settings.redacted())
    asyncio.run(
        run(
            redis_url=settings.redis_url,
            postgres_dsn=settings.postgres_dsn,
            config=settings.signal_config(),
            service_name=settings.service_name,
            log_level=settings.log_level,
            adjustment=settings.adjustment,
            lookback_buffer_days=settings.lookback_buffer_days,
            lookback_max_days=settings.lookback_max_days,
            account_id=settings.account_id,
            start_id=settings.start_id,
            count=settings.count,
            block_ms=settings.block_ms,
            retry_delay_seconds=settings.retry_delay_seconds,
        )
    )


if __name__ == "__main__":
    main()
