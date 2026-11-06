"""Latched kill switches, from the classification rule to an alert on the wire.

The case these exist for: ``daily_loss`` tripped in late October and stayed set
for over a week. Every component was healthy by its own measure; nothing asked
whether the firm was allowed to trade.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from shrap.agents.operations.health_monitor import alerts as alerts_mod
from shrap.agents.operations.health_monitor.agent import tick_once
from shrap.agents.operations.health_monitor.config import Settings
from shrap.agents.operations.health_monitor.freshness import STREAM_HEALTH_ANOMALY
from shrap.agents.operations.health_monitor.state import HealthState
from shrap.agents.operations.health_monitor.switch_latch import (
    SwitchLatchSweeper,
    check_name,
)
from shrap.operations.switch_latch import SwitchRow, classify, first_boundary_after
from tests.agents.operations.test_health_monitor_freshness import (
    _AllUpProm,
    _fake_redis_client,
    _payloads,
    _record_dispatch,
)

NOW = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)  # a Wednesday, mid-session


def _row(
    name: str, at: datetime, active: bool = True, actor: str = "risk-officer/monitor"
) -> SwitchRow:
    return SwitchRow(name=name, active=active, actor=actor, reason="r", at=at)


class _Store:
    def __init__(self, rows: Sequence[SwitchRow]) -> None:
        self.rows = list(rows)

    async def latest(self) -> Sequence[SwitchRow]:
        return self.rows


# --- the rule -------------------------------------------------------------------


def test_the_october_latch_is_down() -> None:
    verdict = classify(_row("daily_loss", datetime(2026, 10, 27, 14, 0, tzinfo=UTC)), NOW)

    assert verdict.status == "down"
    assert verdict.latched_hours is not None and verdict.latched_hours > 24 * 7


def test_a_daily_loss_switch_set_this_session_is_ok() -> None:
    assert classify(_row("daily_loss", NOW - timedelta(hours=2)), NOW).status == "ok"


def test_the_minutes_after_midnight_are_not_a_fault() -> None:
    """Between 00:00 UTC and the Risk Officer's next sweep the switch is
    legitimately still set. The grace keeps that from paging anyone."""

    set_at = datetime(2026, 11, 3, 19, 30, tzinfo=UTC)
    just_after = first_boundary_after(set_at) + timedelta(minutes=10)

    assert classify(_row("daily_loss", set_at), just_after).status == "ok"
    assert classify(_row("daily_loss", set_at), just_after + timedelta(hours=1)).status == "down"


def test_a_cleared_switch_is_ok() -> None:
    row = _row("daily_loss", NOW - timedelta(days=9), active=False)
    assert classify(row, NOW).status == "ok"


@pytest.mark.parametrize("name", ["manual", "strategy:01KYNH9VKXVQXJ48T4MF306PHE"])
def test_human_and_drawdown_switches_are_degraded_not_down(name: str) -> None:
    """Both wait for a ruling by design. The alert is a reminder, not a fault."""

    assert classify(_row(name, NOW - timedelta(days=2), actor="human"), NOW).status == "degraded"


def test_a_naive_timestamp_reads_as_utc() -> None:
    assert classify(_row("daily_loss", datetime(2026, 11, 1, 12, 0)), NOW).status == "down"


# --- the sweeper ----------------------------------------------------------------


async def test_one_result_per_switch_ever_recorded() -> None:
    sweeper = SwitchLatchSweeper(
        store=_Store(
            [
                _row("daily_loss", NOW - timedelta(days=8)),
                _row("manual", NOW - timedelta(days=30), active=False),
            ]
        ),
        interval_seconds=0.0,
    )

    results = await sweeper.run(NOW, 0.0)

    assert {r.name: r.status for r in results} == {
        check_name("daily_loss"): "down",
        check_name("manual"): "ok",
    }


async def test_an_unreadable_log_returns_nothing_rather_than_green() -> None:
    class _Exploding:
        async def latest(self) -> Sequence[SwitchRow]:
            raise RuntimeError("pool exhausted")

    sweeper = SwitchLatchSweeper(store=_Exploding(), interval_seconds=0.0)

    assert await sweeper.run(NOW, 0.0) == []


# --- end to end -----------------------------------------------------------------


async def _tick(redis: Any, state: HealthState, sweeper: SwitchLatchSweeper) -> None:
    settings = Settings(dry_run=False, discord_webhook_url=None, ntfy_url=None)
    async with httpx.AsyncClient() as http:
        await tick_once(
            _AllUpProm(),  # type: ignore[arg-type]
            redis,
            state,
            http,
            settings,
            None,
            NOW,
            latch=sweeper,
        )


async def test_a_latched_daily_loss_switch_alerts_and_publishes_an_anomaly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[dict[str, Any]] = []
    monkeypatch.setattr(alerts_mod, "dispatch", _record_dispatch(dispatched))
    redis, fake = _fake_redis_client()
    store = _Store([_row("daily_loss", datetime(2026, 10, 27, 14, 0, tzinfo=UTC))])
    sweeper = SwitchLatchSweeper(store=store, interval_seconds=0.0)
    state = HealthState(degradation_threshold=2, recovery_threshold=3)

    for _ in range(2):
        await _tick(redis, state, sweeper)

    assert dispatched == [
        {
            "transition": "degraded-confirmed",
            "check": "switch-latch:daily_loss",
            "system_wide": False,
        }
    ]
    anomalies = await _payloads(fake, STREAM_HEALTH_ANOMALY)
    assert len(anomalies) == 1
    assert anomalies[0]["kind"] == "switch-latched"
    assert anomalies[0]["switch"] == "daily_loss"
    assert anomalies[0]["status"] == "down"

    # The switch clears; three good ticks confirm the recovery.
    store.rows = [_row("daily_loss", NOW, active=False)]
    for _ in range(3):
        await _tick(redis, state, sweeper)

    assert dispatched[-1]["transition"] == "recovered-confirmed"
    assert (await _payloads(fake, STREAM_HEALTH_ANOMALY))[-1]["kind"] == "switch-released"
    await fake.aclose()
