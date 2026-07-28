"""Entrypoint for `shrap-strategy-evaluator-trigger`."""

from __future__ import annotations

import asyncio

import structlog

from shrap.agents.research.strategy_evaluator.config import Settings
from shrap.common.logging import configure_logging
from shrap.research.strategy_evaluator.trigger_service import run

log = structlog.get_logger(__name__)


def main() -> None:
    """Run the Strategy Evaluator trigger from environment settings."""

    settings = Settings()
    configure_logging(settings.service_name, settings.log_level)
    log.info("strategy_evaluator_trigger.config_loaded", **settings.redacted())
    asyncio.run(
        run(
            redis_url=settings.redis_url,
            postgres_dsn=settings.postgres_dsn_value(),
            service_name=settings.service_name,
            log_level=settings.log_level,
            card_root=settings.card_root,
            sweep_interval_seconds=settings.sweep_interval_seconds,
            reeval_interval_hours=settings.reeval_interval_hours,
        )
    )


if __name__ == "__main__":
    main()
