"""Entrypoint: `python -m shrap.agents.operations.market_phase` and the
`shrap-market-phase` console script."""

from __future__ import annotations

import asyncio

from shrap.agents.operations.market_phase.agent import run
from shrap.agents.operations.market_phase.config import Settings


def main() -> None:
    """Run the Market Phase Scheduler from environment settings."""

    settings = Settings()
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
