"""Entrypoint for `shrap-paper-order-store`."""

from __future__ import annotations

import asyncio

import structlog

from shrap.agents.trading_floor.paper_order_store.config import Settings
from shrap.common.logging import configure_logging
from shrap.trading_floor.order_store_agent import run

log = structlog.get_logger(__name__)


def main() -> None:
    """Run the Paper Order Store consumer from environment settings."""

    settings = Settings()
    configure_logging(settings.service_name, settings.log_level)
    log.info("paper_order_store.config_loaded", **settings.redacted())
    asyncio.run(
        run(
            redis_url=settings.redis_url,
            postgres_dsn=settings.postgres_dsn_value(),
            service_name=settings.service_name,
            log_level=settings.log_level,
            start_id=settings.start_id,
            count=settings.count,
            block_ms=settings.block_ms,
            retry_delay_seconds=settings.retry_delay_seconds,
            group=settings.service_name,
            consumer=settings.instance_id,
        )
    )


if __name__ == "__main__":
    main()
