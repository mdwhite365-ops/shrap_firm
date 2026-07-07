"""Tests for the deterministic strategy fixture."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from shrap.events import Envelope, EventPublisher, normalize_redis_fields
from shrap.research.strategy_fixture import (
    STREAM_REGIME_SIZING_MODIFIER,
    STREAM_STRATEGY_SIGNAL,
    FixtureConfig,
    fire_once,
)


class FakeFixtureRedis:
    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.flags: dict[str, str] = {}

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        entries = self.streams.setdefault(stream, [])
        entry_id = f"17801286000{len(entries) + 1:02d}-0"
        entries.append((entry_id, fields))
        return entry_id

    async def xrevrange(
        self, name: str, max: str = "+", min: str = "-", count: int | None = None
    ) -> list[tuple[str, dict[str, str]]]:
        entries = self.streams.get(name, [])
        return entries[-(count or 1) :][::-1]

    async def set(
        self, name: str, value: str, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        if nx and name in self.flags:
            return None
        self.flags[name] = value
        return True


async def _publish_regime(redis: FakeFixtureRedis, label: str) -> None:
    await EventPublisher(redis).publish(
        stream=STREAM_REGIME_SIZING_MODIFIER,
        produced_by="intelligence/regime-classifier",
        schema_version="1.0.0",
        payload={"label": label, "confidence": 0.67, "band": [0.75, 1.25], "analogs": []},
    )


@pytest.mark.asyncio
async def test_fires_once_when_regime_allows_then_hits_daily_limit() -> None:
    redis = FakeFixtureRedis()
    await _publish_regime(redis, "crisis-recovery")
    config = FixtureConfig()

    first = await fire_once(redis, config)
    assert first is not None
    signal = Envelope.from_redis_fields(
        normalize_redis_fields(redis.streams[STREAM_STRATEGY_SIGNAL][0][1])
    )
    assert signal.payload is not None
    assert signal.payload["ticker"] == "SPY"
    assert signal.payload["quantity"] == 1
    assert signal.payload["confidence"] == 0.99
    assert signal.payload["regime_label"] == "crisis-recovery"
    assert signal.payload["strategy_id"] == "fixture-regime-gated-v0"
    assert "why this might be wrong" in signal.payload["justification_text"].lower()

    second = await fire_once(redis, config)
    assert second is None
    assert len(redis.streams[STREAM_STRATEGY_SIGNAL]) == 1


@pytest.mark.asyncio
async def test_regime_gate_blocks_disallowed_and_unknown_labels() -> None:
    redis = FakeFixtureRedis()
    await _publish_regime(redis, "wartime")

    assert await fire_once(redis, FixtureConfig()) is None
    assert STREAM_STRATEGY_SIGNAL not in redis.streams
    assert redis.flags == {}  # no daily slot consumed on a closed gate


@pytest.mark.asyncio
async def test_no_regime_events_means_no_signal() -> None:
    redis = FakeFixtureRedis()
    assert await fire_once(redis, FixtureConfig()) is None
    assert STREAM_STRATEGY_SIGNAL not in redis.streams


def test_settings_default_disabled_and_wired(monkeypatch: Any) -> None:
    from shrap.agents.research.strategy_fixture.config import Settings

    assert Settings().enabled is False

    monkeypatch.setenv("STRATEGY_FIXTURE_ALLOWED_REGIME_LABELS", "stagflation, wartime")
    config = Settings().fixture_config()
    assert config.allowed_regime_labels == ("stagflation", "wartime")

    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    assert pyproject["project"]["scripts"]["shrap-strategy-fixture"] == (
        "shrap.agents.research.strategy_fixture.__main__:main"
    )
    compose = Path("infra/docker-compose.yml").read_text()
    assert "strategy-fixture:" in compose
    assert 'STRATEGY_FIXTURE_ENABLED: "${STRATEGY_FIXTURE_ENABLED:-false}"' in compose
    assert 'CMD ["shrap-strategy-fixture"]' in Path("infra/strategy-fixture.Dockerfile").read_text()
