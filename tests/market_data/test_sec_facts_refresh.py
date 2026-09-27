"""The weekly SEC refresh: scheduled, identified, and watched.

Both SEC backfills (share counts, #258; accounting figures, #280) were one-shots,
so market cap and every spec over filed figures aged silently between hand runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from shrap.operations.staleness import DEFAULT_TARGETS


def _service() -> dict[str, Any]:
    compose = yaml.safe_load(Path("infra/docker-compose.yml").read_text())
    svc: dict[str, Any] = compose["services"]["sec-facts-refresh"]
    return svc


def test_the_refresh_runs_both_backfills_on_a_loop() -> None:
    svc = _service()
    script = " ".join(svc["command"])

    assert "profiles" not in svc  # always on, not a tool
    assert svc["restart"] == "unless-stopped"
    assert "shrap-market-data-shares-backfill --launch-list" in script
    assert "shrap-market-data-fundamentals-backfill --launch-list" in script
    assert "while true" in script and "sleep" in script


def test_sec_is_told_who_is_asking() -> None:
    # SEC's fair-access policy asks for a real contact; the CLIs' default is a
    # placeholder, and both read SEC_USER_AGENT.
    agent = _service()["environment"]["SEC_USER_AGENT"]

    assert "example.com" not in agent
    assert "@" in agent


def test_both_tables_are_watched_by_the_service_that_writes_them() -> None:
    by_name = {t.name: t for t in DEFAULT_TARGETS}

    for name in ("market_data.shares_outstanding", "market_data.fundamentals"):
        assert by_name[name].producer == "sec-facts-refresh"
        assert by_name[name].timestamp_column == "fetched_at"
