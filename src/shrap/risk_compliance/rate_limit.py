"""Redis-backed order-rate guardrails for the pre-trade gate.

Two limits, both enforced at approval time:

- **Daily order cap**: at most N approvals per UTC day, firm-wide. A runaway
  signal loop becomes a stream of RATE-vetoed intents instead of a stream of
  real orders.
- **Per-symbol cooldown**: after one approval for a symbol, further intents
  for that symbol are vetoed for a configured window.

State lives in Redis (AOF-persisted), not process memory, so restarts do not
reset the limits — which also blunts the restart-replay hazard: replayed
intents that were already approved once hit the cooldown/cap instead of
minting fresh orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

DAILY_CAP_REASON = "DAILY_ORDER_CAP_REACHED"
SYMBOL_COOLDOWN_REASON = "SYMBOL_COOLDOWN_ACTIVE"

_DAY_KEY_TTL_SECONDS = 2 * 24 * 3600  # keep yesterday's counter around for audit


class RateLimitRedis(Protocol):
    async def incr(self, name: str) -> int: ...

    async def expire(self, name: str, time: int) -> Any: ...

    async def set(
        self,
        name: str,
        value: str,
        nx: bool = False,
        ex: int | None = None,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class RateLimitConfig:
    """Guardrail knobs. Zero/negative values disable the respective limit."""

    max_orders_per_day: int = 10
    symbol_cooldown_seconds: int = 300


class RedisRateLimiter:
    """Consume one approval slot; return a veto reason code when exhausted.

    The daily counter is incremented before the cap check, so a capped day
    keeps counting attempts (useful in the audit trail). The cooldown key is
    only claimed when the daily cap allows, and only for the checked symbol.
    """

    def __init__(self, redis: RateLimitRedis, config: RateLimitConfig) -> None:
        self._redis = redis
        self._config = config

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).date().isoformat()

    async def acquire(self, ticker: str) -> str | None:
        """Return None when the order may proceed; a veto reason code otherwise."""

        symbol = ticker.strip().upper()
        if self._config.max_orders_per_day > 0:
            day_key = f"risk:approved-count:{self._today()}"
            approved_today = await self._redis.incr(day_key)
            if approved_today == 1:
                await self._redis.expire(day_key, _DAY_KEY_TTL_SECONDS)
            if approved_today > self._config.max_orders_per_day:
                return DAILY_CAP_REASON
        if self._config.symbol_cooldown_seconds > 0:
            claimed = await self._redis.set(
                f"risk:cooldown:{symbol}",
                "1",
                nx=True,
                ex=self._config.symbol_cooldown_seconds,
            )
            if not claimed:
                return SYMBOL_COOLDOWN_REASON
        return None
