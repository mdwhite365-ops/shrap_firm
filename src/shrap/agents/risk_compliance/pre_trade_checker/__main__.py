"""Entrypoint for `shrap-pre-trade-checker`."""

from __future__ import annotations

import asyncio

from shrap.agents.risk_compliance.pre_trade_checker.config import Settings
from shrap.risk_compliance.pre_trade_checker_agent import run


def main() -> None:
    """Run the Pre-Trade Checker from environment settings."""

    settings = Settings()
    asyncio.run(
        run(
            redis_url=settings.redis_url,
            policy=settings.policy(),
            service_name=settings.service_name,
            log_level=settings.log_level,
            start_id=settings.start_id,
            count=settings.count,
            block_ms=settings.block_ms,
            retry_delay_seconds=settings.retry_delay_seconds,
            rate_limit_config=settings.rate_limit_config(),
        )
    )


if __name__ == "__main__":
    main()
