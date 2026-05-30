"""Unit tests for the Health Monitor agent."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import fakeredis.aioredis
import httpx
import pytest

from shrap.agents.operations.health_monitor import alerts as alerts_mod
from shrap.agents.operations.health_monitor.agent import STREAM_TICK, tick_once
from shrap.agents.operations.health_monitor.checks import CheckResult, check_redis
from shrap.agents.operations.health_monitor.config import Settings
from shrap.agents.operations.health_monitor.state import HealthState
from shrap.common.envelope import Envelope
from shrap.common.redis_client import RedisStreamClient


def _ok(name: str = "redis") -> CheckResult:
    return CheckResult(name=name, status="ok", latency_ms=1.0, evidence={})


def _bad(name: str = "redis") -> CheckResult:
    return CheckResult(name=name, status="down", latency_ms=1.0, evidence={})


def test_state_degradation_threshold() -> None:
    s = HealthState(degradation_threshold=2, recovery_threshold=3)
    assert s.update(_bad()) is None  # 1 bad
    assert s.update(_bad()) == "degraded-confirmed"  # 2 bad


def test_state_recovery_threshold() -> None:
    s = HealthState(degradation_threshold=2, recovery_threshold=3)
    s.update(_bad())
    s.update(_bad())  # now degraded-confirmed
    assert s.is_degraded("redis")
    assert s.update(_ok()) is None
    assert s.update(_ok()) is None
    assert s.update(_ok()) == "recovered-confirmed"
    assert not s.is_degraded("redis")


def test_state_flap_resistance() -> None:
    s = HealthState(degradation_threshold=2, recovery_threshold=3)
    for _ in range(10):
        assert s.update(_bad()) is None
        assert s.update(_ok()) is None
    assert not s.is_degraded("redis")


@pytest.mark.asyncio
async def test_check_redis_parses_prom_response() -> None:
    class FakeProm:
        def __init__(self, val: float | None) -> None:
            self._val = val

        async def query_instant(self, q: str) -> float | None:
            return self._val

    r_ok = await check_redis(FakeProm(1.0))  # type: ignore[arg-type]
    assert r_ok.status == "ok"
    r_down = await check_redis(FakeProm(0.0))  # type: ignore[arg-type]
    assert r_down.status == "down"
    r_deg = await check_redis(FakeProm(None))  # type: ignore[arg-type]
    assert r_deg.status == "degraded"


@pytest.mark.asyncio
async def test_alert_dispatch_failure_does_not_raise() -> None:
    from pydantic import SecretStr

    settings = Settings(
        discord_webhook_url=SecretStr("https://example.invalid/webhook"),
        ntfy_url=None,
        dry_run=False,
    )

    raising_client = AsyncMock(spec=httpx.AsyncClient)
    raising_client.post = AsyncMock(side_effect=httpx.ConnectError("nope"))

    # Stand in for redis with a no-op xadd (we don't want to spin up fakeredis here).
    class _NopRedis:
        async def xadd(self, stream: str, env: Envelope) -> str:
            return "0-0"

        async def close(self) -> None:
            return None

    check = CheckResult(name="redis", status="down", latency_ms=1.0, evidence={})
    # Must not raise.
    await alerts_mod.dispatch(
        "degraded-confirmed",
        check,
        settings,
        http_client=raising_client,
        redis=_NopRedis(),  # type: ignore[arg-type]
        system_wide=False,
    )


@pytest.mark.asyncio
async def test_envelope_published_for_tick() -> None:
    """Drive a single tick with mocked Prometheus + fakeredis; assert ops.health-tick lands."""
    settings = Settings(dry_run=False, discord_webhook_url=None, ntfy_url=None)

    # Wire fakeredis into a RedisStreamClient without touching the network.
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    redis = RedisStreamClient.__new__(RedisStreamClient)
    redis._url = "fake"  # type: ignore[attr-defined]
    redis._redis = fake  # type: ignore[attr-defined]
    redis._known_groups = set()  # type: ignore[attr-defined]

    class FakeProm:
        async def query_instant(self, q: str) -> float | None:
            return 1.0

        async def query_targets_up(self) -> dict[str, bool]:
            return {}

    state = HealthState(degradation_threshold=2, recovery_threshold=3)

    async with httpx.AsyncClient() as http_client:
        results = await tick_once(
            FakeProm(),  # type: ignore[arg-type]
            redis,
            state,
            http_client,
            settings,
        )

    assert len(results) == 6  # six checks

    entries = await fake.xread({STREAM_TICK: "0"}, count=10)
    assert entries, "expected ops.health-tick stream entry"
    _stream, items = entries[0]
    assert len(items) == 1
    _id, raw_fields = items[0]
    # fakeredis may return stream field names/values as bytes even with
    # decode_responses=True; production RedisStreamClient uses decoded strings.
    fields = {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in raw_fields.items()
    }
    env = Envelope.from_redis_fields(fields)
    assert env.schema_version == "1.0.0"
    assert env.payload is not None
    assert "checks" in env.payload
    assert "summary" in env.payload

    await fake.aclose()


def test_settings_redacted_masks_discord_secret() -> None:
    from pydantic import SecretStr

    s = Settings(discord_webhook_url=SecretStr("https://secret.example/x"))
    r: dict[str, Any] = s.redacted()
    assert r["discord_webhook_url"] == "***"
