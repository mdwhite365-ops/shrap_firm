"""Thin Prometheus HTTP API wrapper."""

from __future__ import annotations

from typing import Any

import httpx


class PrometheusClient:
    """Minimal Prometheus query client."""

    def __init__(self, base_url: str = "http://prometheus:9090", timeout: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def query_instant(self, q: str) -> float | None:
        """Run an instant query; return the first scalar/vector sample value or None."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f"{self._base_url}/api/v1/query", params={"query": q})
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        if data.get("status") != "success":
            return None
        result = data.get("data", {}).get("result", [])
        if not result:
            return None
        first = result[0]
        value = first.get("value")
        if not value or len(value) < 2:
            return None
        try:
            return float(value[1])
        except (TypeError, ValueError):
            return None

    async def query_targets_up(self) -> dict[str, bool]:
        """Return {target_name: up?} from /api/v1/targets active list."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f"{self._base_url}/api/v1/targets", params={"state": "active"})
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        out: dict[str, bool] = {}
        if data.get("status") != "success":
            return out
        for t in data.get("data", {}).get("activeTargets", []):
            labels = t.get("labels", {})
            name = labels.get("job") or labels.get("instance") or "unknown"
            out[name] = t.get("health") == "up"
        return out
