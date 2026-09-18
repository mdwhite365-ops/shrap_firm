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


# A deploy legitimately restarts containers, so the threshold is "more than one
# restart in the window" rather than "any". `--force-recreate` produces exactly
# one. A crashloop produces hundreds: shraptasmaner averaged roughly one every
# three minutes for six months.
RESTART_WINDOW = "15m"
RESTART_THRESHOLD = 1

# The exporter's Prometheus job, asked separately for liveness so that "nothing
# is broken" and "nobody is answering" are two different answers rather than one
# ambiguous empty result.
EXPORTER_JOB = "docker-state-exporter"
UNHEALTHY_SELECTOR = 'container_state_health_status{status="unhealthy"} == 1'


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


async def check_container_health(prom: PrometheusClient) -> CheckResult:
    """Containers Docker itself calls unhealthy.

    Nothing read this signal until 2026-09-18, and on that day two containers
    had been reporting `unhealthy` for SEVEN WEEKS — `shrap_qdrant`, whose probe
    ran a curl its image does not contain, and `shrap_langfuse`, whose probe
    polled a loopback address it never listens on. Both services were serving
    correctly throughout.

    That is why this is `degraded` rather than `down`. An unhealthy flag means
    "Docker's probe is failing", which is evidence about the probe as much as
    about the service, and the firm has now seen the probe be the broken half
    twice. It is worth waking someone; it is not worth declaring an outage.
    """

    t0 = time.perf_counter()
    # `or vector(0)` because count() over an EMPTY result set returns no data,
    # not zero — so a substrate with nothing unhealthy is indistinguishable from
    # an exporter that is not answering. Found by deploying this check and
    # watching it declare `degraded` while the same query returned no rows.
    # Liveness is asked separately, below, where it can actually be told apart.
    unhealthy = await prom.query_instant(f"count({UNHEALTHY_SELECTOR}) or vector(0)")
    exporter_up = await prom.query_instant(f'max(up{{job="{EXPORTER_JOB}"}})')
    names = await prom.query_series_labels(UNHEALTHY_SELECTOR, "name")
    ms = (time.perf_counter() - t0) * 1000.0
    evidence: dict[str, Any] = {
        "unhealthy_count": unhealthy,
        "unhealthy": names,
        "exporter_up": exporter_up,
    }
    if not exporter_up:
        return CheckResult(
            name="container_health", status="degraded", latency_ms=ms, evidence=evidence
        )
    status: Status = "ok" if unhealthy == 0 else "degraded"
    return CheckResult(name="container_health", status=status, latency_ms=ms, evidence=evidence)


async def check_container_restarts(prom: PrometheusClient) -> CheckResult:
    """Containers stuck in a restart loop.

    THE CASE cAdvISOR CANNOT SEE. `shraptasmaner` restarted 88,034 times before
    anyone noticed, and cAdvisor had never scraped it once: a container that
    fails instantly spends no measurable time running, so every scrape finds it
    exited. `container_last_seen` therefore reported 40 healthy-looking
    containers while a 41st burned a core in a loop. Only the Docker API sees a
    container that is almost never up.

    Measured as restarts WITHIN THE WINDOW rather than the lifetime counter,
    because the lifetime counter never resets — a container that crashlooped
    once in March would otherwise alarm forever.
    """

    t0 = time.perf_counter()
    selector = f"increase(container_restartcount[{RESTART_WINDOW}]) > {RESTART_THRESHOLD}"
    # See check_container_health: an empty count() is no data, not zero.
    looping = await prom.query_instant(f"count({selector}) or vector(0)")
    exporter_up = await prom.query_instant(f'max(up{{job="{EXPORTER_JOB}"}})')
    names = await prom.query_series_labels(selector, "name")
    ms = (time.perf_counter() - t0) * 1000.0
    evidence: dict[str, Any] = {
        "looping_count": looping,
        "looping": names,
        "window": RESTART_WINDOW,
        "threshold": RESTART_THRESHOLD,
        "exporter_up": exporter_up,
    }
    if not exporter_up:
        return CheckResult(
            name="container_restarts", status="degraded", latency_ms=ms, evidence=evidence
        )
    status: Status = "ok" if looping == 0 else "degraded"
    return CheckResult(name="container_restarts", status=status, latency_ms=ms, evidence=evidence)


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
    check_container_health,
    check_container_restarts,
    check_node,
    check_tailscale,
)
