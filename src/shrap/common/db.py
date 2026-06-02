"""Shared PostgreSQL pool helpers."""

from __future__ import annotations

from typing import Any


async def create_asyncpg_pool(dsn: str, *, min_size: int = 1, max_size: int = 5) -> Any:
    """Create an asyncpg pool without importing asyncpg for non-DB agents."""

    try:
        import asyncpg  # type: ignore[import-untyped]
    except ImportError as e:  # pragma: no cover - runtime packaging guard
        raise RuntimeError("Install the optional dependency group that provides asyncpg") from e
    return await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size)
