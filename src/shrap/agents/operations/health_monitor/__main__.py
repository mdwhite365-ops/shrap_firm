"""Entrypoint: `python -m shrap.agents.operations.health_monitor` and the
`shrap-health-monitor` console script."""

from __future__ import annotations

import asyncio

from shrap.agents.operations.health_monitor.agent import run
from shrap.agents.operations.health_monitor.config import Settings


def main() -> None:
    """Sync entrypoint for [project.scripts]."""
    settings = Settings()
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
