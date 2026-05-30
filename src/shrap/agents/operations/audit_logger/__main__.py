"""Entrypoint for `shrap-audit-logger`."""

from __future__ import annotations

import asyncio

from shrap.agents.operations.audit_logger.agent import run
from shrap.agents.operations.audit_logger.config import Settings


def main() -> None:
    settings = Settings()
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
