"""Entrypoint for `shrap-tech-watcher`."""

from __future__ import annotations

import asyncio

import structlog

from shrap.agents.research.tech_watcher.config import Settings
from shrap.common.logging import configure_logging
from shrap.research.tech_watcher.service import run

log = structlog.get_logger(__name__)


def main() -> None:
    """Run the Tech Watcher ingest service from environment settings."""

    settings = Settings()
    configure_logging(settings.service_name, settings.log_level)
    log.info("tech_watcher.config_loaded", **settings.redacted())
    asyncio.run(
        run(
            redis_url=settings.redis_url,
            postgres_dsn=settings.postgres_dsn_value(),
            sec_user_agent=settings.sec_user_agent,
            edgar_forms=settings.edgar_forms_tuple(),
            arxiv_categories=settings.arxiv_categories_tuple(),
            gov_sources_enabled=settings.gov_sources_enabled,
            usaspending_agencies=settings.usaspending_agencies_tuple(),
            usaspending_min_amount=settings.usaspending_min_amount,
            usaspending_lookback_days=settings.usaspending_lookback_days,
            doe_feed_url=settings.doe_feed_url,
            max_results=settings.max_results,
            interval_seconds=settings.interval_seconds,
            http_timeout=settings.http_timeout,
            llm_enabled=settings.llm_enabled,
            filter_max_items=settings.filter_max_items,
            synthesis_interval_seconds=settings.synthesis_interval_seconds,
            max_proposals=settings.max_proposals,
            service_name=settings.service_name,
            log_level=settings.log_level,
        )
    )


if __name__ == "__main__":
    main()
