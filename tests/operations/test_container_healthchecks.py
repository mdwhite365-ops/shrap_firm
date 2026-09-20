"""Healthchecks must be runnable by the image they run in.

Two probes in this stack could never have passed, and both reported `unhealthy`
for SEVEN WEEKS against services that were working the whole time:

- ``qdrant`` ran ``curl``, and the qdrant image ships no curl, wget, nc or
  python. Every run exited 1 with "curl: not found".
- ``langfuse`` polled ``localhost:3000``, and Langfuse binds only to the
  container's eth0 address. Both loopback forms are refused from inside while
  the host reaches it fine through the port mapping.

**A probe that cannot run is worse than no probe.** It reports a permanent
failure indistinguishable from a real one, so the signal is not just missing —
it is actively misleading, and seven weeks of it trained everyone to ignore two
`unhealthy` flags. These tests pin the shape of each fix against the specific
mistake that was made, because neither is visible by reading the compose file:
you have to know what is inside the image.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

COMPOSE = Path("infra/docker-compose.yml")

# Retired from the main stack 2026-09-19 (zero traces, ever) but kept revivable,
# same as infra/ibgateway/. The probe knowledge travels with it.
LANGFUSE_LOCAL_COMPOSE = Path("infra/langfuse-local/docker-compose.yml")


@pytest.fixture(scope="module")
def services() -> dict[str, Any]:
    data = yaml.safe_load(COMPOSE.read_text())
    return dict(data["services"])


def _probe(services: dict[str, Any], name: str) -> str:
    """The command text of a probe, whichever form it uses.

    ``CMD-SHELL`` runs ``/bin/sh -c``; ``CMD`` execs the argv directly. The
    difference is load-bearing here — see the dash/bash test below.
    """

    test = services[name]["healthcheck"]["test"]
    assert test[0] in {"CMD", "CMD-SHELL"}, f"{name} probe has no recognised form"
    return " ".join(str(part) for part in test[1:])


def test_qdrant_probe_uses_no_binary_the_image_lacks(services: dict[str, Any]) -> None:
    """bash's /dev/tcp is the only HTTP client in the qdrant image."""

    probe = _probe(services, "qdrant")
    for absent in ("curl", "wget", "nc ", "python"):
        assert absent not in probe, f"qdrant image has no {absent.strip()!r}"
    assert "/dev/tcp/" in probe


def test_qdrant_probe_checks_the_response_not_just_the_port(services: dict[str, Any]) -> None:
    """An open socket is liveness; a 200 is health. Qdrant can accept a
    connection while refusing to serve, so the probe reads the status line."""

    probe = _probe(services, "qdrant")
    assert "healthz" in probe
    assert "200 OK" in probe


def test_qdrant_probe_runs_under_bash_not_sh(services: dict[str, Any]) -> None:
    """/dev/tcp is a BASH builtin and /bin/sh in this image is dash.

    Under CMD-SHELL the probe fails with "cannot create /dev/tcp/...: Directory
    nonexistent", which reads like a missing path and is actually a missing
    shell feature. The first attempt at this fix made exactly that mistake —
    it was verified with `docker exec ... bash -c`, which is not the shell the
    healthcheck uses.
    """

    test = services["qdrant"]["healthcheck"]["test"]
    assert test[0] == "CMD", "CMD-SHELL would run dash, which has no /dev/tcp"
    assert test[1] == "bash"


def test_the_main_stack_no_longer_runs_a_local_langfuse(services: dict[str, Any]) -> None:
    """Retired 2026-09-19: it held zero traces for its entire deployed life.

    All 24 agents point at `https://us.cloud.langfuse.com`, which held 4,424
    traces when this was removed. The local instance cost an 821 MB image, a
    68 MB volume, two healthchecks and a nightly backup leg to store nothing.
    """

    assert not [name for name in services if "langfuse" in name]


def test_langfuse_probe_does_not_use_loopback() -> None:
    """THE seven-week bug. Langfuse listens on eth0 only.

    `localhost` resolves to ::1 inside the container and `127.0.0.1` is refused
    just the same — the service is simply not on loopback. Verified against the
    running container: both refused, `$(hostname -i)` returned
    {"status":"OK","version":"2.95.11"}.

    Kept, and pointed at the revival compose, because the fix is only obvious
    once you have lost seven weeks to it. Anyone bringing this back gets the
    working probe rather than rediscovering the broken one.
    """

    revived = dict(yaml.safe_load(LANGFUSE_LOCAL_COMPOSE.read_text())["services"])
    probe = _probe(revived, "langfuse")
    assert "localhost" not in probe
    assert "127.0.0.1" not in probe
    # $$ is compose's escape for a literal $ passed to the shell.
    assert "$$(hostname -i)" in probe


def test_every_healthcheck_is_a_list_not_a_bare_string(services: dict[str, Any]) -> None:
    """A bare string is run by the daemon's shell rather than the image's, which
    is how a probe ends up depending on a binary nobody checked for."""

    for name, spec in services.items():
        check = spec.get("healthcheck")
        if check is None:
            continue
        assert isinstance(check["test"], list), f"{name} healthcheck must be a list"


# ---------------------------------------------------------------------------
# ib-gateway, a separate compose project (infra/ibgateway/).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ibgateway() -> dict[str, Any]:
    data = yaml.safe_load(Path("infra/ibgateway/docker-compose.yml").read_text())
    return dict(data["services"]["ib-gateway"])


def test_the_gateway_probe_uses_no_binary_the_image_lacks(ibgateway: dict[str, Any]) -> None:
    """The third image in this stack whose probe named a tool it does not ship.

    `gnzsnz/ib-gateway` has bash, socat and timeout — no nc, curl or wget. Its
    own probe ran `nc`, so the container reported `unhealthy` for its entire
    life while the gateway was logged in and serving.
    """

    probe = " ".join(str(p) for p in ibgateway["healthcheck"]["test"])
    assert "nc " not in probe and not probe.endswith(" nc")
    assert "curl" not in probe
    assert "wget" not in probe
    assert "socat" in probe


def test_the_gateway_probe_is_a_connect_not_an_api_session(
    ibgateway: dict[str, Any],
) -> None:
    """Opening an IB API session needs a client id, and a probe claiming one
    every 30 seconds would collide with a real client. Liveness is the right
    assertion here."""

    probe = " ".join(str(p) for p in ibgateway["healthcheck"]["test"])
    assert "TCP:127.0.0.1:4002" in probe
    assert "-u /dev/null" in probe


def test_the_gateway_file_carries_no_credentials(ibgateway: dict[str, Any]) -> None:
    """TWS_USERID and TWS_PASSWORD come from a gitignored .env, never this file."""

    rendered = str(ibgateway.get("environment", {}))
    assert "TWS_USERID" not in rendered
    assert "TWS_PASSWORD" not in rendered
    assert ".env" in str(ibgateway.get("env_file", []))


def test_the_gateway_stays_off_the_firms_network(ibgateway: dict[str, Any]) -> None:
    """ADR-0003 makes Alpaca the paper-phase broker interface.

    Putting a broker gateway on `shrap_net` beside every agent would make IBKR
    a firm service by the back door. It keeps its own network, as it has been.
    """

    assert ibgateway["networks"] == ["shrap-trading"]
    published = " ".join(str(p) for p in ibgateway.get("ports", []))
    assert "4001" not in published and "4002" not in published
    assert "4003" not in published and "4004" not in published


def test_the_gateway_is_paper_by_default(ibgateway: dict[str, Any]) -> None:
    assert "paper" in str(ibgateway["environment"]["TRADING_MODE"])
