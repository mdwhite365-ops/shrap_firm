"""Reconciliation Agent service loop.

Runs one reconciliation pass per interval — no scheduler beyond a simple
interruptible sleep. A failed pass logs, waits the retry delay, and tries
again; it never crashes the service.
"""

from __future__ import annotations

import asyncio
import signal
from typing import cast

import httpx
import structlog
from redis.asyncio import Redis

from shrap.agents.operations.reconciliation_agent.agent import (
    DEFAULT_BROKER,
    DEFAULT_PRODUCED_BY,
    AccountSnapshotSink,
    OrderStateRepository,
    Publisher,
    reconcile_once,
)
from shrap.agents.operations.reconciliation_agent.broker import (
    AlpacaPaperSnapshotReader,
    BrokerSnapshotReader,
)
from shrap.agents.operations.reconciliation_agent.db import (
    PostgresAccountSnapshotStore,
    PostgresOrderEventRepository,
)
from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.events import EventPublisher, RedisPublisher
from shrap.trading_floor.alpaca import AlpacaPaperClient, AlpacaPaperSettings, AsyncHttpClient

log = structlog.get_logger(__name__)

HTTP_TIMEOUT_SECONDS = 30.0


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


async def run_loop(
    broker_reader: BrokerSnapshotReader,
    repository: OrderStateRepository,
    publisher: Publisher,
    stop: asyncio.Event,
    produced_by: str = DEFAULT_PRODUCED_BY,
    broker: str = DEFAULT_BROKER,
    interval_seconds: float = 300.0,
    retry_delay_seconds: float = 30.0,
    snapshot_sink: AccountSnapshotSink | None = None,
) -> None:
    """Run reconciliation passes until ``stop`` is set."""

    while not stop.is_set():
        try:
            await reconcile_once(
                broker_reader=broker_reader,
                repository=repository,
                publisher=publisher,
                produced_by=produced_by,
                broker=broker,
                snapshot_sink=snapshot_sink,
            )
            delay = interval_seconds
        except Exception:
            log.exception("reconciliation.pass_failed")
            delay = retry_delay_seconds
        await _interruptible_sleep(stop, delay)


async def run(
    redis_url: str,
    postgres_dsn: str,
    alpaca_settings: AlpacaPaperSettings,
    service_name: str = "reconciliation-agent",
    log_level: str = "INFO",
    broker: str = DEFAULT_BROKER,
    order_status: str = "all",
    order_limit: int = 500,
    interval_seconds: float = 300.0,
    retry_delay_seconds: float = 30.0,
) -> None:
    """Run the Reconciliation Agent service until SIGINT/SIGTERM."""

    configure_logging(service_name, log_level)
    log.info(
        "reconciliation_agent.starting",
        redis_url=redis_url,
        postgres_dsn="***",
        alpaca=alpaca_settings.redacted(),
        broker=broker,
        interval_seconds=interval_seconds,
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    redis: Redis = Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_timeout=HTTP_TIMEOUT_SECONDS,
    )
    pool = await create_asyncpg_pool(postgres_dsn)
    repository = PostgresOrderEventRepository(pool)
    snapshot_store = PostgresAccountSnapshotStore(pool)
    await snapshot_store.ensure_schema()
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as http_client:
        broker_reader = AlpacaPaperSnapshotReader(
            AlpacaPaperClient(alpaca_settings),
            cast(AsyncHttpClient, http_client),
            order_status=order_status,
            order_limit=order_limit,
        )
        try:
            await run_loop(
                broker_reader=broker_reader,
                repository=repository,
                publisher=EventPublisher(cast(RedisPublisher, redis)),
                stop=stop,
                broker=broker,
                interval_seconds=interval_seconds,
                retry_delay_seconds=retry_delay_seconds,
                snapshot_sink=snapshot_store,
            )
        finally:
            await redis.aclose()
            await pool.close()
            log.info("reconciliation_agent.stopped")


__all__ = [
    "run",
    "run_loop",
]
