"""Output-freshness checks for the Health Monitor.

The spec has asked for this since the first draft — `docs/agents/operations/
health-monitor.md` §Trigger lists "data freshness" in the 30-second pulse and
§Processing step 4 says the rollup must "include data-freshness summary (last
tick age) for each load-bearing data source". Only the Prometheus half was ever
built, which is why three producers could report healthy passes while writing
nothing (see :mod:`shrap.operations.staleness`).

This module is the adapter, not the logic: the judgement lives in
``shrap.operations.staleness``, and here it is turned into the ``CheckResult``
shape the rest of the agent already knows how to debounce, publish and alert
on. Freshness checks therefore inherit the transition state machine for free —
a table hovering either side of its threshold cannot alert twice.

Two deliberate differences from the Prometheus checks:

- **Own cadence.** Freshness runs on the spec's five-minute pass rather than the
  30-second one. The questions are slow (a table stale by six hours does not
  need 30-second resolution) and the queries are the only ones the monitor makes
  that touch Postgres.
- **Own stream.** Confirmed transitions also publish ``operations.health-anomaly``,
  the stream `docs/02-architecture.md` §Observability and ADR-0006 name for this
  agent and which had no producer here until now. It carries the target detail
  and the threshold's rationale; ``ops.health-degraded`` carries only the
  generic check shape.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from shrap.agents.operations.health_monitor.checks import CheckResult
from shrap.operations.staleness import (
    DEFAULT_TARGETS,
    FreshnessTarget,
    FreshnessVerdict,
    StalenessStore,
    sweep,
)

log = structlog.get_logger(__name__)

STREAM_HEALTH_ANOMALY = "operations.health-anomaly"

# Freshness check names are namespaced so the transition dispatcher can tell
# them from substrate checks without a lookup table.
CHECK_PREFIX = "freshness:"

ANOMALY_KIND_STALE = "output-stale"
ANOMALY_KIND_RESUMED = "output-resumed"


def check_name(target: FreshnessTarget) -> str:
    return f"{CHECK_PREFIX}{target.name}"


def is_freshness_check(name: str) -> bool:
    return name.startswith(CHECK_PREFIX)


def to_check_result(verdict: FreshnessVerdict, latency_ms: float) -> CheckResult:
    return CheckResult(
        name=check_name(verdict.target),
        status=verdict.status,
        latency_ms=latency_ms,
        evidence=verdict.evidence(),
    )


def anomaly_payload(transition: str, verdict: FreshnessVerdict, agent: str) -> dict[str, Any]:
    """Payload for ``operations.health-anomaly``.

    Shaped like the Tech Watcher's anomalies (``agent`` + ``kind`` + detail) so
    a consumer can read every producer of the stream the same way.
    """

    kind = ANOMALY_KIND_RESUMED if transition == "recovered-confirmed" else ANOMALY_KIND_STALE
    return {
        "agent": agent,
        "kind": kind,
        "target": verdict.target.name,
        "status": verdict.status,
        **verdict.evidence(),
    }


@dataclass
class FreshnessSweeper:
    """Runs the freshness sweep on its own cadence and remembers the verdicts.

    ``interval_seconds`` of 0 makes every tick due, which is how the tests drive
    it deterministically.
    """

    store: StalenessStore
    targets: Sequence[FreshnessTarget] = DEFAULT_TARGETS
    interval_seconds: float = 300.0
    timeout_seconds: float = 15.0
    _last_run_monotonic: float | None = field(default=None, repr=False)
    _last_verdicts: dict[str, FreshnessVerdict] = field(default_factory=dict, repr=False)

    def due(self, monotonic_now: float) -> bool:
        if self._last_run_monotonic is None:
            return True
        return (monotonic_now - self._last_run_monotonic) >= self.interval_seconds

    def verdict_for(self, name: str) -> FreshnessVerdict | None:
        """The verdict behind a check name, for transition-time anomaly detail."""

        return self._last_verdicts.get(name)

    async def run(self, now: datetime, monotonic_now: float) -> list[CheckResult]:
        """Sweep every target; return one CheckResult each.

        Never raises. A sweep that times out or blows up produces no results at
        all rather than a false green: an absent check leaves the state machine
        holding its previous verdict, which is the honest reading of "we could
        not ask".
        """

        started = time.perf_counter()
        try:
            verdicts = await asyncio.wait_for(
                sweep(self.store, self.targets, now), timeout=self.timeout_seconds
            )
        except Exception:
            log.exception("freshness.sweep_failed")
            self._last_run_monotonic = monotonic_now
            return []

        self._last_run_monotonic = monotonic_now
        latency_ms = (time.perf_counter() - started) * 1000.0
        results = [to_check_result(v, latency_ms) for v in verdicts]
        self._last_verdicts = {r.name: v for r, v in zip(results, verdicts, strict=True)}
        log.info(
            "freshness.sweep",
            targets=len(results),
            stale=[v.target.name for v in verdicts if v.status != "ok"],
        )
        return results


__all__ = [
    "ANOMALY_KIND_RESUMED",
    "ANOMALY_KIND_STALE",
    "CHECK_PREFIX",
    "STREAM_HEALTH_ANOMALY",
    "FreshnessSweeper",
    "anomaly_payload",
    "check_name",
    "is_freshness_check",
    "to_check_result",
]
