"""In-memory transition tracker for health checks.

Process-local. On restart, counters reset — that's acceptable per spec; the
agent publishes ops.health-startup and the next tick re-establishes baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from shrap.agents.operations.health_monitor.checks import CheckResult

Transition = Literal["degraded-confirmed", "recovered-confirmed"] | None


@dataclass
class _PerCheck:
    consecutive_good: int = 0
    consecutive_bad: int = 0
    declared_degraded: bool = False
    last_status: str | None = None


@dataclass
class HealthState:
    degradation_threshold: int
    recovery_threshold: int
    _checks: dict[str, _PerCheck] = field(default_factory=dict)

    def update(self, result: CheckResult) -> Transition:
        st = self._checks.setdefault(result.name, _PerCheck())
        st.last_status = result.status

        if result.status == "ok":
            st.consecutive_good += 1
            st.consecutive_bad = 0
        else:
            st.consecutive_bad += 1
            st.consecutive_good = 0

        if not st.declared_degraded and st.consecutive_bad >= self.degradation_threshold:
            st.declared_degraded = True
            return "degraded-confirmed"
        if st.declared_degraded and st.consecutive_good >= self.recovery_threshold:
            st.declared_degraded = False
            return "recovered-confirmed"
        return None

    def is_degraded(self, name: str) -> bool:
        st = self._checks.get(name)
        return bool(st and st.declared_degraded)

    def degraded_count(self) -> int:
        return sum(1 for s in self._checks.values() if s.declared_degraded)

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {
            name: {
                "consecutive_good": s.consecutive_good,
                "consecutive_bad": s.consecutive_bad,
                "declared_degraded": s.declared_degraded,
                "last_status": s.last_status,
            }
            for name, s in self._checks.items()
        }
