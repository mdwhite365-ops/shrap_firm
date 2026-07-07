"""Strategy Fixture service loop."""

from __future__ import annotations

import asyncio
import signal
from typing import cast

import structlog
from redis.asyncio import Redis

from shrap.common.logging import configure_logging
from shrap.research.strategy_fixture import FixtureConfig, FixtureRedis, fire_once

log = structlog.get_logger(__name__)


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass


async def _interruptible_sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run(
    redis_url: str,
    config: FixtureConfig,
    enabled: bool,
    service_name: str = "strategy-fixture",
    log_level: str = "INFO",
    interval_seconds: float = 600.0,
) -> None:
    """Run the fixture until SIGINT/SIGTERM. When disabled, idle loudly."""

    configure_logging(service_name, log_level)
    log.info(
        "strategy_fixture.starting",
        redis_url=redis_url,
        enabled=enabled,
        ticker=config.ticker,
        allowed_regime_labels=list(config.allowed_regime_labels),
        max_signals_per_day=config.max_signals_per_day,
        interval_seconds=interval_seconds,
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    redis: Redis = Redis.from_url(redis_url, decode_responses=True, socket_timeout=30)
    try:
        while not stop.is_set():
            if enabled:
                try:
                    await fire_once(cast(FixtureRedis, redis), config)
                except Exception:
                    log.exception("strategy_fixture.pass_failed")
            else:
                log.info("strategy_fixture.disabled_idle")
            await _interruptible_sleep(stop, interval_seconds)
    finally:
        await redis.aclose()
        log.info("strategy_fixture.stopped")
