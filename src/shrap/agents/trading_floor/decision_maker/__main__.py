"""Entrypoint for `shrap-decision-maker`."""

from __future__ import annotations

import asyncio

import structlog

from shrap.agents.trading_floor.decision_maker.config import Settings
from shrap.common.logging import configure_logging
from shrap.trading_floor.decision_maker_service import run

log = structlog.get_logger(__name__)


def main() -> None:
    """Run the Decision Maker stub from environment settings."""

    settings = Settings()
    configure_logging(settings.service_name, settings.log_level)
    log.info("decision_maker.config_loaded", **settings.redacted())
    asyncio.run(
        run(
            redis_url=settings.redis_url,
            service_name=settings.service_name,
            log_level=settings.log_level,
            start_id=settings.start_id,
            count=settings.count,
            block_ms=settings.block_ms,
            retry_delay_seconds=settings.retry_delay_seconds,
            threshold=settings.confidence_threshold,
            group=settings.service_name,
            consumer=settings.instance_id,
        )
    )


if __name__ == "__main__":
    main()
