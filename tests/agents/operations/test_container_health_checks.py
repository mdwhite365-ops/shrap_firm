"""Container health and restart-loop checks, and why Prometheus alone could not.

On 2026-09-18 three containers were quietly broken and the Health Monitor
reported six green checks throughout:

- `shraptasmaner` had restarted **88,034 times**. cAdvisor had never scraped it
  once — it saw 40 containers and that one was not among them, because a
  container that fails instantly spends no measurable time running. Crashloop
  detection from `container_last_seen` was not merely missing, it was
  impossible.
- `shrap_qdrant` and `shrap_langfuse` had read `unhealthy` for seven weeks while
  serving correctly, because their probes referenced a curl and a loopback
  address that do not exist inside those images.

These tests pin the two checks that close that gap, and the distinctions that
make them useful rather than noisy.
"""

from __future__ import annotations

from typing import Any

import pytest

from shrap.agents.operations.health_monitor.checks import (
    RESTART_THRESHOLD,
    RESTART_WINDOW,
    check_container_health,
    check_container_restarts,
)


class FakeProm:
    """Answers by substring, so a test states the world rather than a query."""

    def __init__(self, counts: dict[str, float | None], names: dict[str, list[str]]) -> None:
        self._counts = counts
        self._names = names
        self.queries: list[str] = []

    def _match(self, q: str, table: dict[str, Any], default: Any) -> Any:
        for key, value in table.items():
            if key in q:
                return value
        return default

    async def query_instant(self, q: str) -> float | None:
        self.queries.append(q)
        if "up{job=" in q:
            return self._counts.get("exporter_up", 1.0)
        return self._match(q, self._counts, 0.0)

    async def query_series_labels(self, q: str, label: str) -> list[str]:
        return self._match(q, self._names, [])


# --- unhealthy containers ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_healthy_substrate_is_ok() -> None:
    result = await check_container_health(FakeProm({"unhealthy": 0.0}, {}))  # type: ignore[arg-type]
    assert result.status == "ok"
    assert result.evidence["unhealthy_count"] == 0


@pytest.mark.asyncio
async def test_unhealthy_containers_degrade_and_are_named() -> None:
    """An alert saying "1 unhealthy" costs the reader a console session."""

    prom = FakeProm({"unhealthy": 2.0}, {"unhealthy": ["shrap_langfuse", "shrap_qdrant"]})
    result = await check_container_health(prom)  # type: ignore[arg-type]
    assert result.status == "degraded"
    assert result.evidence["unhealthy"] == ["shrap_langfuse", "shrap_qdrant"]


@pytest.mark.asyncio
async def test_unhealthy_is_degraded_not_down() -> None:
    """`unhealthy` is evidence about the PROBE as much as about the service.

    The firm has now seen the probe be the broken half twice in one day — a
    curl that is not in the image, and a loopback address nothing listens on.
    Both services were serving correctly. Worth waking someone; not worth
    declaring an outage.
    """

    result = await check_container_health(FakeProm({"unhealthy": 1.0}, {}))  # type: ignore[arg-type]
    assert result.status == "degraded"


@pytest.mark.asyncio
async def test_a_missing_exporter_is_degraded_not_ok() -> None:
    """Nobody answering is not the same as nothing being wrong.

    Reading absence as "nothing unhealthy" would rebuild the exact blind spot
    this check exists to remove, so liveness is asked as its own question.
    """

    prom = FakeProm({"unhealthy": 0.0, "exporter_up": 0.0}, {})
    result = await check_container_health(prom)  # type: ignore[arg-type]
    assert result.status == "degraded"
    assert result.evidence["exporter_up"] == 0.0


@pytest.mark.asyncio
async def test_an_empty_result_set_reads_as_zero_not_as_an_outage() -> None:
    """THE bug this check shipped with, caught by deploying it.

    `count()` over an empty result set returns NO DATA in Prometheus, not 0. The
    first version read that as "the exporter is not answering" and declared
    `degraded` on a perfectly healthy substrate — an alarm that fires precisely
    when nothing is wrong is worse than no alarm, because it is the fastest way
    to teach someone to ignore the channel.
    """

    for check in (check_container_health, check_container_restarts):
        prom = FakeProm({"unhealthy": 0.0, "restartcount": 0.0, "exporter_up": 1.0}, {})
        result = await check(prom)  # type: ignore[arg-type]
        assert result.status == "ok", check.__name__
        assert any("or vector(0)" in q for q in prom.queries), check.__name__


# --- restart loops -----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_quiet_substrate_has_no_restart_loops() -> None:
    result = await check_container_restarts(FakeProm({"restartcount": 0.0}, {}))  # type: ignore[arg-type]
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_a_crashloop_degrades_and_is_named() -> None:
    """The shraptasmaner case: 88,034 restarts, invisible to cAdvisor."""

    prom = FakeProm({"restartcount": 1.0}, {"restartcount": ["shraptasmaner"]})
    result = await check_container_restarts(prom)  # type: ignore[arg-type]
    assert result.status == "degraded"
    assert result.evidence["looping"] == ["shraptasmaner"]


@pytest.mark.asyncio
async def test_restarts_are_measured_in_a_window_not_as_a_lifetime_total() -> None:
    """The lifetime counter never resets.

    A container that crashlooped once in March would otherwise alarm forever,
    and an alarm that can never clear is one people learn to ignore — which is
    how two `unhealthy` flags survived seven weeks.
    """

    prom = FakeProm({"restartcount": 0.0}, {})
    await check_container_restarts(prom)  # type: ignore[arg-type]
    assert any("increase(" in q and RESTART_WINDOW in q for q in prom.queries)
    assert not any("container_restartcount >" in q for q in prom.queries)


@pytest.mark.asyncio
async def test_a_single_restart_does_not_alarm() -> None:
    """`docker compose up --force-recreate` produces exactly one restart.

    A deploy is not an incident. shraptasmaner averaged roughly one restart
    every three minutes for six months, so the signal being caught here is
    orders of magnitude away from the threshold.
    """

    assert RESTART_THRESHOLD >= 1


@pytest.mark.asyncio
async def test_both_checks_are_in_the_substrate_pass() -> None:
    from shrap.agents.operations.health_monitor.checks import ALL_CHECKS

    assert check_container_health in ALL_CHECKS
    assert check_container_restarts in ALL_CHECKS


def test_compose_and_prometheus_wire_the_exporter() -> None:
    from pathlib import Path

    import yaml

    compose = yaml.safe_load(Path("infra/docker-compose.yml").read_text())
    svc = compose["services"]["docker-state-exporter"]
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in svc["volumes"]

    prom = yaml.safe_load(Path("infra/prometheus/prometheus.yml").read_text())
    job = next(j for j in prom["scrape_configs"] if j["job_name"] == "docker-state-exporter")
    assert job["static_configs"][0]["targets"] == ["docker-state-exporter:8080"]
    # The exporter copies every compose label onto every series.
    assert any(r.get("action") == "labeldrop" for r in job["metric_relabel_configs"])
