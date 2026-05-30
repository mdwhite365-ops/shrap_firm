"""Health checks. Pure async functions; each takes a PrometheusClient and
returns a CheckResult. No side effects.

Status convention:
    "ok"        - metric present and good
    "degraded"  - metric stale/missing or warn-level numeric breach
    "down"      - explicit "0" / unreachable
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from shrap.common.prom_client import PrometheusClient

Status = str  # "ok" | "degraded" | "down"

# Containers we expect to always be present in the substrate compose stack.
EXPECTED_CONTAINERS: tuple[str, ...] = (
    "shrap_redis",
    "shrap_postgres",
    "shrap_qdrant",
    "shrap_prometheus",
    "shrap_grafana",
    "shrap_cadvisor",
    "shrap_redis_exporter",
)


@dataclass
class CheckResult:
    name: str
    status: Status
    latency_ms: float
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _classify_up(val: float | None) -> Status:
    if val is None:
        return "degraded"
    if val >= 1.0:
        return "ok"
    return "down"


async def _timed_query(prom: PrometheusClient, q: str) -> tuple[float | None, float]:
    t0 = time.perf_counter()
    val = await prom.query_instant(q)
    return val, (time.perf_counter() - t0) * 1000.0


async def check_redis(prom: PrometheusClient) -> CheckResult:
    val, ms = await _timed_query(prom, "max(redis_up)")
    return CheckResult(
        name="redis",
        status=_classify_up(val),
        latency_ms=ms,
        evidence={"redis_up": val},
    )


async def check_postgres(prom: PrometheusClient) -> CheckResult:
    val, ms = await _timed_query(prom, "max(pg_up)")
    return CheckResult(
        name="postgres",
        status=_classify_up(val),
        latency_ms=ms,
        evidence={"pg_up": val},
    )


async def check_qdrant(prom: PrometheusClient) -> CheckResult:
    val, ms = await _timed_query(prom, 'max(up{job="qdrant"})')
    return CheckResult(
        name="qdrant",
        status=_classify_up(val),
        latency_ms=ms,
        evidence={"up_qdrant": val},
    )


async def check_docker(prom: PrometheusClient) -> CheckResult:
    """cadvisor exposes container_last_seen; count distinct container names seen recently."""
    t0 = time.perf_counter()
    seen = await prom.query_instant('count(count by (name) (container_last_seen{name=~".+"}))')
    ms = (time.perf_counter() - t0) * 1000.0
    evidence: dict[str, Any] = {
        "containers_seen": seen,
        "expected_min": len(EXPECTED_CONTAINERS),
    }
    if seen is None:
        return CheckResult(name="docker", status="degraded", latency_ms=ms, evidence=evidence)
    status: Status = "ok" if seen >= len(EXPECTED_CONTAINERS) else "degraded"
    return CheckResult(name="docker", status=status, latency_ms=ms, evidence=evidence)


async def check_node(prom: PrometheusClient) -> CheckResult:
    """Host vitals via node-exporter. Memory/disk under 10% available → degraded."""
    t0 = time.perf_counter()
    up = await prom.query_instant('max(up{job="node-exporter"})')
    load1 = await prom.query_instant("max(node_load1)")
    mem_avail = await prom.query_instant("max(node_memory_MemAvailable_bytes)")
    mem_total = await prom.query_instant("max(node_memory_MemTotal_bytes)")
    fs_avail = await prom.query_instant('max(node_filesystem_avail_bytes{mountpoint="/"})')
    fs_size = await prom.query_instant('max(node_filesystem_size_bytes{mountpoint="/"})')
    ms = (time.perf_counter() - t0) * 1000.0

    evidence: dict[str, Any] = {
        "up": up,
        "load1": load1,
        "mem_avail_bytes": mem_avail,
        "mem_total_bytes": mem_total,
        "fs_avail_bytes": fs_avail,
        "fs_total_bytes": fs_size,
    }

    status: Status = _classify_up(up)
    if status == "ok":
        # Check memory + disk pressure.
        if mem_avail is not None and mem_total and mem_total > 0:
            mem_frac = mem_avail / mem_total
            evidence["mem_avail_frac"] = mem_frac
            if mem_frac < 0.10:
                status = "degraded"
        if fs_avail is not None and fs_size and fs_size > 0:
            fs_frac = fs_avail / fs_size
            evidence["fs_avail_frac"] = fs_frac
            if fs_frac < 0.10:
                status = "degraded"

    return CheckResult(name="node", status=status, latency_ms=ms, evidence=evidence)


async def check_tailscale(prom: PrometheusClient) -> CheckResult:
    """STUB: tailscale metrics aren't wired into Prometheus yet.

    Returns ok with an explicit note so consumers know coverage is incomplete
    rather than getting a fake green signal. Replace once tailscale-exporter
    (or `tailscale status --json` shim) is added to the substrate.
    """
    return CheckResult(
        name="tailscale",
        status="ok",
        latency_ms=0.0,
        evidence={"note": "tailscale metrics not yet wired", "stub": True},
    )


ALL_CHECKS = (
    check_redis,
    check_postgres,
    check_qdrant,
    check_docker,
    check_node,
    check_tailscale,
)
