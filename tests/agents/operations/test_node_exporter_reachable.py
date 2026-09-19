"""The scrape path to node-exporter, asserted against the files that define it.

node-exporter was configured to bind ``127.0.0.1`` while Prometheus — on a
different network namespace — scraped a hardcoded ``172.17.0.1``. Neither half
could work: the address was wrong for this compose project's bridge, and a
loopback bind is unreachable from another namespace at *any* address. Nothing
raised. ``up{job="node-exporter"}`` was 0 for every sample Prometheus ever
took, so the firm had no host CPU, memory or disk metrics at all.

This is the recurring shape CLAUDE.md names: **a component reconstructed a fact
that was already recorded, and the reconstruction disagreed.** Docker knows what
the gateway is; the config asserted one anyway, with a comment telling the
reader to fix it by hand if it was wrong.

These tests are cheap and they are the only thing standing between that comment
and its return. They check the two halves *together*, because each is harmless
alone and fatal in combination.
"""

from __future__ import annotations

from pathlib import Path

import yaml

COMPOSE = Path("infra/docker-compose.yml")
PROMETHEUS = Path("infra/prometheus/prometheus.yml")

HOST_ALIAS = "host.docker.internal"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def _prometheus() -> dict:
    return yaml.safe_load(PROMETHEUS.read_text())


def test_node_exporter_does_not_bind_loopback() -> None:
    """A loopback bind under host networking is unreachable from any container.

    Asserted on the flag rather than on behaviour because the failure mode is
    silent: the exporter starts, serves happily to the host, and reports
    nothing wrong while every scrape times out.
    """

    command = _compose()["services"]["node-exporter"]["command"]
    listen = next(arg for arg in command if str(arg).startswith("--web.listen-address"))

    assert "127.0.0.1" not in listen, (
        f"node-exporter binds loopback ({listen}); Prometheus runs on the "
        "shrap_net bridge and cannot reach it there at any address"
    )
    assert listen.endswith(":9100"), listen


def test_prometheus_resolves_the_host_instead_of_asserting_an_address() -> None:
    """The gateway is Docker's fact to supply, not the config's to guess."""

    prometheus = _compose()["services"]["prometheus"]
    extra_hosts = prometheus.get("extra_hosts") or []

    assert f"{HOST_ALIAS}:host-gateway" in extra_hosts, (
        "prometheus must map host.docker.internal to host-gateway, or the "
        "node-exporter target below cannot resolve"
    )


def test_node_exporter_target_is_the_alias_not_a_hardcoded_bridge_ip() -> None:
    """172.17.0.1 is the DEFAULT bridge; this project's gateway is not that."""

    job = next(j for j in _prometheus()["scrape_configs"] if j["job_name"] == "node-exporter")
    targets = job["static_configs"][0]["targets"]

    assert targets == [f"{HOST_ALIAS}:9100"], targets
    assert not any(t.startswith("172.") for t in targets), (
        f"hardcoded bridge address in node-exporter target: {targets}"
    )


def test_the_two_halves_agree() -> None:
    """Either half alone is harmless; together they decide whether it works.

    The bug survived because the bind and the target live in different files
    and neither one is wrong on its own terms.
    """

    command = _compose()["services"]["node-exporter"]["command"]
    listen = next(arg for arg in command if str(arg).startswith("--web.listen-address"))
    _, _, listen_port = listen.rpartition(":")

    job = next(j for j in _prometheus()["scrape_configs"] if j["job_name"] == "node-exporter")
    target_host, _, target_port = job["static_configs"][0]["targets"][0].rpartition(":")

    assert listen_port == target_port, (
        f"exporter binds :{listen_port} but Prometheus scrapes :{target_port}"
    )
    assert target_host == HOST_ALIAS
    # Host networking is what makes the alias necessary: on a bridge the
    # exporter would be reachable by service DNS like everything else.
    assert _compose()["services"]["node-exporter"]["network_mode"] == "host"
