"""Health Monitor main loop."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from shrap.agents.operations.health_monitor import alerts
from shrap.agents.operations.health_monitor.checks import ALL_CHECKS, CheckResult
from shrap.agents.operations.health_monitor.state import HealthState
from shrap.common.envelope import Envelope
from shrap.common.logging import configure_logging
from shrap.common.prom_client import PrometheusClient
from shrap.common.redis_client import RedisStreamClient

if TYPE_CHECKING:
    from shrap.agents.operations.health_monitor.config import Settings

log = structlog.get_logger(__name__)

SCHEMA_VERSION = "1.0.0"

STREAM_TICK = "ops.health-tick"
STREAM_DEGRADED = "ops.health-degraded"
STREAM_RECOVERED = "ops.health-recovered"
STREAM_STARTUP = "ops.health-startup"
STREAM_SHUTDOWN = "ops.health-shutdown"


CheckFn = Callable[[PrometheusClient], Awaitable[CheckResult]]


async def _run_check(check_fn: CheckFn, prom: PrometheusClient) -> CheckResult:
    """Invoke a check function; turn unexpected exceptions into degraded results."""
    try:
        return await asyncio.wait_for(check_fn(prom), timeout=5.0)
    except Exception as e:
        log.exception("check.exception", check=getattr(check_fn, "__name__", "?"))
        return CheckResult(
            name=getattr(check_fn, "__name__", "unknown").removeprefix("check_"),
            status="degraded",
            latency_ms=0.0,
            evidence={"error": str(e)},
        )


async def _publish(
    redis: RedisStreamClient, stream: str, settings: Settings, payload: dict[str, Any]
) -> None:
    if settings.dry_run:
        log.info("publish.dry_run", stream=stream, payload_keys=list(payload.keys()))
        return
    env = Envelope.new(
        produced_by=settings.produced_by(),
        schema_version=SCHEMA_VERSION,
        payload=payload,
    )
    await redis.xadd(stream, env)


async def tick_once(
    prom: PrometheusClient,
    redis: RedisStreamClient,
    state: HealthState,
    http_client: httpx.AsyncClient,
    settings: Settings,
) -> list[CheckResult]:
    """Run one tick: all checks, publish tick envelope, dispatch any transitions.

    Factored out so tests can drive a single tick deterministically.
    """
    results = await asyncio.gather(*(_run_check(fn, prom) for fn in ALL_CHECKS))

    tick_payload = {
        "checks": [r.to_dict() for r in results],
        "summary": {
            "ok": sum(1 for r in results if r.status == "ok"),
            "degraded": sum(1 for r in results if r.status == "degraded"),
            "down": sum(1 for r in results if r.status == "down"),
        },
    }
    await _publish(redis, STREAM_TICK, settings, tick_payload)

    # Compute transitions, then determine system-wide-ness for severity routing.
    transitions: list[tuple[str, CheckResult]] = []
    for r in results:
        t = state.update(r)
        if t is not None:
            transitions.append((t, r))

    system_wide = state.degraded_count() >= 2

    for transition, check in transitions:
        stream = STREAM_DEGRADED if transition == "degraded-confirmed" else STREAM_RECOVERED
        await _publish(
            redis,
            stream,
            settings,
            {
                "transition": transition,
                "check": check.to_dict(),
                "system_wide": system_wide,
            },
        )
        await alerts.dispatch(
            transition,
            check,
            settings,
            http_client=http_client,
            redis=redis,
            system_wide=(transition == "degraded-confirmed" and system_wide),
        )

    log.info(
        "health.tick",
        summary=tick_payload["summary"],
        transitions=[(t, c.name) for t, c in transitions],
        state=state.snapshot(),
    )
    return results


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows / restricted environments — fall through.
            pass


async def run(settings: Settings) -> None:
    configure_logging(settings.service_name, settings.log_level)
    log.info("health_monitor.starting", **settings.redacted())

    redis = RedisStreamClient(settings.redis_url)
    prom = PrometheusClient(settings.prom_url)
    state = HealthState(
        degradation_threshold=settings.degradation_threshold_consecutive_ticks,
        recovery_threshold=settings.recovery_threshold_consecutive_ticks,
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)

    async with httpx.AsyncClient(timeout=10.0) as http_client:
        try:
            await _publish(
                redis,
                STREAM_STARTUP,
                settings,
                {"settings": settings.redacted()},
            )
        except Exception:
            log.exception("health_monitor.startup_publish_failed")

        try:
            while not stop.is_set():
                try:
                    await tick_once(prom, redis, state, http_client, settings)
                except Exception:
                    log.exception("health_monitor.tick_failed")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.tick_interval_seconds)
                except TimeoutError:
                    pass
        finally:
            try:
                await _publish(
                    redis,
                    STREAM_SHUTDOWN,
                    settings,
                    {"reason": "signal"},
                )
            except Exception:
                log.exception("health_monitor.shutdown_publish_failed")
            await redis.close()
            log.info("health_monitor.stopped")
