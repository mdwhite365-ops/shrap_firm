"""Entrypoint for `shrap-execution-agent`."""

from __future__ import annotations

import asyncio

from shrap.agents.trading_floor.execution_agent.config import Settings
from shrap.trading_floor.execution_agent import run


def main() -> None:
    """Run the paper Execution Agent from environment settings."""

    settings = Settings()
    asyncio.run(
        run(
            redis_url=settings.redis_url,
            alpaca_settings=settings.alpaca_settings(),
            service_name=settings.service_name,
            log_level=settings.log_level,
            start_id=settings.start_id,
            count=settings.count,
            block_ms=settings.block_ms,
            retry_delay_seconds=settings.retry_delay_seconds,
            status_poll_interval_seconds=settings.status_poll_interval_seconds,
            group=settings.service_name,
            consumer=settings.instance_id,
        )
    )


if __name__ == "__main__":
    main()
