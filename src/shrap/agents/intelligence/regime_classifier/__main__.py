"""Entrypoint for `shrap-regime-classifier`."""

from __future__ import annotations

import asyncio

import structlog

from shrap.agents.intelligence.regime_classifier.config import Settings
from shrap.common.logging import configure_logging
from shrap.intelligence.regime.agent import run

log = structlog.get_logger(__name__)


def main() -> None:
    """Run the Regime Classifier from environment settings."""

    settings = Settings()
    configure_logging(settings.service_name, settings.log_level)
    log.info("regime_classifier.config_loaded", **settings.redacted())
    asyncio.run(
        run(
            redis_url=settings.redis_url,
            postgres_dsn=settings.postgres_dsn_value(),
            market_data_settings=settings.market_data_settings(),
            config=settings.run_config(),
            service_name=settings.service_name,
            log_level=settings.log_level,
            interval_seconds=settings.interval_seconds,
            retry_delay_seconds=settings.retry_delay_seconds,
        )
    )


if __name__ == "__main__":
    main()
