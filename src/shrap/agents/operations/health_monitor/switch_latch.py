"""Latched-switch checks for the Health Monitor.

The adapter, not the logic: the judgement lives in
:mod:`shrap.operations.switch_latch`, and here it becomes ``CheckResult``s the
agent already debounces, publishes and alerts on, exactly as
:mod:`~shrap.agents.operations.health_monitor.freshness` does for output tables.

It runs on the freshness cadence (five minutes) and shares its Postgres pool.
A latch is a slow fact; thirty-second resolution would add queries and no
earlier warning.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import structlog

from shrap.agents.operations.health_monitor.checks import CheckResult
from shrap.operations.switch_latch import (
    DEFAULT_GRACE,
    LatchVerdict,
    SwitchLatchStore,
    sweep,
)

log = structlog.get_logger(__name__)

CHECK_PREFIX = "switch-latch:"

ANOMALY_KIND_LATCHED = "switch-latched"
ANOMALY_KIND_RELEASED = "switch-released"


def check_name(switch: str) -> str:
    return f"{CHECK_PREFIX}{switch}"


def is_latch_check(name: str) -> bool:
    return name.startswith(CHECK_PREFIX)


def anomaly_payload(transition: str, verdict: LatchVerdict, agent: str) -> dict[str, Any]:
    kind = ANOMALY_KIND_RELEASED if transition == "recovered-confirmed" else ANOMALY_KIND_LATCHED
    return {"agent": agent, "kind": kind, "status": verdict.status, **verdict.evidence()}


@dataclass
class SwitchLatchSweeper:
    store: SwitchLatchStore
    interval_seconds: float = 300.0
    timeout_seconds: float = 15.0
    grace: timedelta = DEFAULT_GRACE
    _last_run_monotonic: float | None = field(default=None, repr=False)
    _last_verdicts: dict[str, LatchVerdict] = field(default_factory=dict, repr=False)

    def due(self, monotonic_now: float) -> bool:
        if self._last_run_monotonic is None:
            return True
        return (monotonic_now - self._last_run_monotonic) >= self.interval_seconds

    def verdict_for(self, name: str) -> LatchVerdict | None:
        return self._last_verdicts.get(name)

    async def run(self, now: datetime, monotonic_now: float) -> list[CheckResult]:
        """One CheckResult per switch ever recorded. Never raises.

        A failed read returns nothing rather than a false green, so the state
        machine holds its last verdict: "we could not ask" is not "all clear".
        """

        started = time.perf_counter()
        try:
            verdicts = await asyncio.wait_for(
                sweep(self.store, now, self.grace), timeout=self.timeout_seconds
            )
        except Exception:
            log.exception("switch_latch.sweep_failed")
            self._last_run_monotonic = monotonic_now
            return []

        self._last_run_monotonic = monotonic_now
        latency_ms = (time.perf_counter() - started) * 1000.0
        results = [
            CheckResult(
                name=check_name(v.switch),
                status=v.status,
                latency_ms=latency_ms,
                evidence=v.evidence(),
            )
            for v in verdicts
        ]
        self._last_verdicts = {r.name: v for r, v in zip(results, verdicts, strict=True)}
        latched = [v.switch for v in verdicts if v.status != "ok"]
        if latched:
            log.warning("switch_latch.latched", switches=latched)
        else:
            log.info("switch_latch.sweep", switches=len(results))
        return results


__all__ = [
    "ANOMALY_KIND_LATCHED",
    "ANOMALY_KIND_RELEASED",
    "CHECK_PREFIX",
    "SwitchLatchSweeper",
    "anomaly_payload",
    "check_name",
    "is_latch_check",
]
