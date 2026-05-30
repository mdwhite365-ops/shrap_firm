"""Health Monitor agent (operations department).

No-LLM deterministic poller per ADR-0009. Queries Prometheus, publishes
ops.health-tick / ops.health-degraded / ops.health-recovered envelopes
(ADR-0006) on Redis Streams (ADR-0001), escalates via Discord/ntfy.sh
(ADR-0005). No auto-remediation.
"""

from __future__ import annotations

__version__ = "0.1.0"

from shrap.agents.operations.health_monitor.checks import CheckResult
from shrap.agents.operations.health_monitor.config import Settings
from shrap.agents.operations.health_monitor.state import HealthState

__all__ = ["CheckResult", "HealthState", "Settings", "__version__"]
