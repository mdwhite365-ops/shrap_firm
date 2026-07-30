"""Health Monitor output-freshness wiring.

Separate file from ``test_health_monitor.py`` on purpose (KI-016): two cards
appending to the tail of one test file merged into a SyntaxError once, and
decoupling the test is cheaper than sequencing the merges.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis.aioredis
import httpx
import pytest

from shrap.agents.operations.health_monitor import agent as agent_mod
from shrap.agents.operations.health_monitor import alerts as alerts_mod
from shrap.agents.operations.health_monitor.agent import tick_once
from shrap.agents.operations.health_monitor.config import Settings
from shrap.agents.operations.health_monitor.freshness import (
    STREAM_HEALTH_ANOMALY,
    FreshnessSweeper,
    check_name,
)
from shrap.agents.operations.health_monitor.state import HealthState
from shrap.common.envelope import Envelope
from shrap.common.redis_client import RedisStreamClient
from shrap.operations.staleness import DEFAULT_TARGETS, FreshnessReading, FreshnessTarget

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)

FRESH = FreshnessReading(table_exists=True, has_rows=True, last_row_at=NOW)
EMPTY = FreshnessReading(table_exists=True, has_rows=False, last_row_at=None)


class _StubStore:
    """Returns whatever reading the test currently wants, per target."""

    def __init__(self, default: FreshnessReading) -> None:
        self.default = default
        self.overrides: dict[str, FreshnessReading] = {}

    async def read(self, target: FreshnessTarget) -> FreshnessReading:
        return self.overrides.get(target.name, self.default)


class _AllUpProm:
    """Every substrate metric healthy. 100 satisfies both the up-gauges and the
    container-count / free-memory ratios, so no infra check degrades."""

    async def query_instant(self, q: str) -> float | None:
        return 100.0

    async def query_targets_up(self) -> dict[str, bool]:
        return {}


class _AllDownProm:
    async def query_instant(self, q: str) -> float | None:
        return 0.0

    async def query_targets_up(self) -> dict[str, bool]:
        return {}


def _fake_redis_client() -> tuple[RedisStreamClient, fakeredis.aioredis.FakeRedis]:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    redis = RedisStreamClient.__new__(RedisStreamClient)
    redis._url = "fake"  # type: ignore[attr-defined]
    redis._redis = fake  # type: ignore[attr-defined]
    redis._known_groups = set()  # type: ignore[attr-defined]
    return redis, fake


async def _payloads(fake: fakeredis.aioredis.FakeRedis, stream: str) -> list[dict[str, Any]]:
    entries = await fake.xread({stream: "0"}, count=100)
    if not entries:
        return []
    _stream, items = entries[0]
    out: list[dict[str, Any]] = []
    for _id, raw in items:
        fields = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw.items()
        }
        env = Envelope.from_redis_fields(fields)
        assert env.payload is not None
        out.append(env.payload)
    return out


# ---------------------------------------------------------------------------
# cadence
# ---------------------------------------------------------------------------


def test_sweeper_is_due_before_its_first_run() -> None:
    sweeper = FreshnessSweeper(store=_StubStore(FRESH), interval_seconds=300.0)
    assert sweeper.due(1000.0)


async def test_sweeper_holds_off_until_the_interval_elapses() -> None:
    sweeper = FreshnessSweeper(store=_StubStore(FRESH), interval_seconds=300.0)
    await sweeper.run(NOW, 1000.0)
    assert not sweeper.due(1200.0)
    assert sweeper.due(1300.0)


async def test_sweep_produces_one_result_per_target() -> None:
    sweeper = FreshnessSweeper(store=_StubStore(FRESH), interval_seconds=0.0)
    results = await sweeper.run(NOW, 0.0)
    assert [r.name for r in results] == [check_name(t) for t in DEFAULT_TARGETS]
    assert all(r.status == "ok" for r in results)


async def test_a_store_that_cannot_be_read_is_degraded_never_ok() -> None:
    class _Exploding:
        async def read(self, target: FreshnessTarget) -> FreshnessReading:
            raise RuntimeError("pool exhausted")

    sweeper = FreshnessSweeper(store=_Exploding(), interval_seconds=0.0, timeout_seconds=0.01)
    results = await sweeper.run(NOW, 0.0)
    # sweep() absorbs per-target failures into query-failed verdicts, so results
    # arrive — but degraded, never ok.
    assert results and all(r.status == "degraded" for r in results)


# ---------------------------------------------------------------------------
# tick integration
# ---------------------------------------------------------------------------


async def test_freshness_checks_join_the_tick_rollup() -> None:
    settings = Settings(dry_run=False, discord_webhook_url=None, ntfy_url=None)
    redis, fake = _fake_redis_client()
    sweeper = FreshnessSweeper(store=_StubStore(FRESH), interval_seconds=0.0)
    state = HealthState(degradation_threshold=2, recovery_threshold=3)

    async with httpx.AsyncClient() as http:
        results = await tick_once(
            _AllUpProm(),  # type: ignore[arg-type]
            redis,
            state,
            http,
            settings,
            sweeper,
            NOW,
        )

    assert len(results) == 6 + len(DEFAULT_TARGETS)
    tick = (await _payloads(fake, agent_mod.STREAM_TICK))[0]
    assert tick["summary"]["ok"] == 6 + len(DEFAULT_TARGETS)
    await fake.aclose()


async def test_an_empty_table_publishes_a_health_anomaly(monkeypatch: pytest.MonkeyPatch) -> None:
    """The News Analyzer failure, end to end: empty table -> named anomaly."""

    monkeypatch.setattr(alerts_mod, "dispatch", _record_dispatch([]))
    settings = Settings(dry_run=False, discord_webhook_url=None, ntfy_url=None)
    redis, fake = _fake_redis_client()

    store = _StubStore(FRESH)
    store.overrides["intelligence.news_items"] = EMPTY
    sweeper = FreshnessSweeper(store=store, interval_seconds=0.0)
    state = HealthState(degradation_threshold=2, recovery_threshold=3)

    async with httpx.AsyncClient() as http:
        for _ in range(2):  # two consecutive bad ticks confirm the transition
            await tick_once(
                _AllUpProm(),  # type: ignore[arg-type]
                redis,
                state,
                http,
                settings,
                sweeper,
                NOW,
            )

    anomalies = await _payloads(fake, STREAM_HEALTH_ANOMALY)
    assert len(anomalies) == 1
    payload = anomalies[0]
    assert payload["agent"] == "health-monitor"
    assert payload["kind"] == "output-stale"
    assert payload["target"] == "intelligence.news_items"
    assert payload["status"] == "down"
    assert payload["reason"] == "no-rows"
    assert payload["producer"] == "news-analyzer"
    assert payload["rationale"]
    await fake.aclose()


async def test_recovery_publishes_output_resumed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alerts_mod, "dispatch", _record_dispatch([]))
    settings = Settings(dry_run=False, discord_webhook_url=None, ntfy_url=None)
    redis, fake = _fake_redis_client()

    store = _StubStore(FRESH)
    store.overrides["intelligence.news_items"] = EMPTY
    sweeper = FreshnessSweeper(store=store, interval_seconds=0.0)
    state = HealthState(degradation_threshold=2, recovery_threshold=3)

    async def tick() -> None:
        async with httpx.AsyncClient() as http:
            await tick_once(
                _AllUpProm(),  # type: ignore[arg-type]
                redis,
                state,
                http,
                settings,
                sweeper,
                NOW,
            )

    await tick()
    await tick()
    store.overrides.clear()  # the producer starts writing again
    for _ in range(3):
        await tick()

    kinds = [p["kind"] for p in await _payloads(fake, STREAM_HEALTH_ANOMALY)]
    assert kinds == ["output-stale", "output-resumed"]
    await fake.aclose()


async def test_stale_tables_do_not_trigger_the_urgent_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Five dead producers is serious and routine. ntfy priority 5 is for the
    substrate being gone, and an alarm that always screams gets muted."""

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(alerts_mod, "dispatch", _record_dispatch(calls))
    settings = Settings(dry_run=False, discord_webhook_url=None, ntfy_url=None)
    redis, fake = _fake_redis_client()

    sweeper = FreshnessSweeper(store=_StubStore(EMPTY), interval_seconds=0.0)
    state = HealthState(degradation_threshold=2, recovery_threshold=3)

    async with httpx.AsyncClient() as http:
        for _ in range(2):
            await tick_once(
                _AllUpProm(),  # type: ignore[arg-type]
                redis,
                state,
                http,
                settings,
                sweeper,
                NOW,
            )

    assert len(calls) == len(DEFAULT_TARGETS)
    assert all(c["system_wide"] is False for c in calls)
    await fake.aclose()


async def test_a_dead_substrate_still_escalates(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half of the same change: infra checks keep their urgent path."""

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(alerts_mod, "dispatch", _record_dispatch(calls))
    settings = Settings(dry_run=False, discord_webhook_url=None, ntfy_url=None)
    redis, fake = _fake_redis_client()

    sweeper = FreshnessSweeper(store=_StubStore(FRESH), interval_seconds=0.0)
    state = HealthState(degradation_threshold=2, recovery_threshold=3)

    async with httpx.AsyncClient() as http:
        for _ in range(2):
            await tick_once(
                _AllDownProm(),  # type: ignore[arg-type]
                redis,
                state,
                http,
                settings,
                sweeper,
                NOW,
            )

    assert calls, "expected substrate transitions"
    assert all(c["system_wide"] is True for c in calls)
    await fake.aclose()


async def test_absent_freshness_checks_hold_their_state() -> None:
    """A tick where the sweep is not due must not read as recovery."""

    settings = Settings(dry_run=False, discord_webhook_url=None, ntfy_url=None)
    redis, fake = _fake_redis_client()
    sweeper = FreshnessSweeper(store=_StubStore(EMPTY), interval_seconds=1e9)
    state = HealthState(degradation_threshold=1, recovery_threshold=1)

    async with httpx.AsyncClient() as http:
        first = await tick_once(
            _AllUpProm(),  # type: ignore[arg-type]
            redis,
            state,
            http,
            settings,
            sweeper,
            NOW,
        )
        second = await tick_once(
            _AllUpProm(),  # type: ignore[arg-type]
            redis,
            state,
            http,
            settings,
            sweeper,
            NOW + timedelta(seconds=30),
        )

    assert len(first) == 6 + len(DEFAULT_TARGETS)
    assert len(second) == 6  # not due; freshness simply absent
    assert state.is_degraded(check_name(DEFAULT_TARGETS[0]))
    await fake.aclose()


def _record_dispatch(sink: list[dict[str, Any]]) -> Any:
    async def _dispatch(
        transition: str,
        check: Any,
        settings: Any,
        *,
        http_client: Any,
        redis: Any = None,
        system_wide: bool = False,
    ) -> None:
        sink.append({"transition": transition, "check": check.name, "system_wide": system_wide})

    return _dispatch
