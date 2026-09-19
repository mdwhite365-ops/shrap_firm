"""Redis-backed order-rate guardrails for the pre-trade gate.

Two limits, both enforced at approval time and both scoped to **one account**:

- **Daily order cap**: at most N approvals per UTC day per account. A runaway
  signal loop becomes a stream of RATE-vetoed intents instead of a stream of
  real orders, and it exhausts only its own account's budget.
- **Per-symbol cooldown**: one approval per symbol per *decision slot*, or —
  for an intent that declares no slot — per configured time window.

State lives in Redis (AOF-persisted), not process memory, so restarts do not
reset the limits — which also blunts the restart-replay hazard: replayed
intents that were already approved once hit the cooldown/cap instead of
minting fresh orders.

**Why both limits are account-scoped (2026-09-18).** They were firm-wide, and
on 2026-09-18 that cost real money. At 09:30:01 a ``U`` sell intent for 0.0126
shares on ``PA3KQN57WVXY`` claimed the global ``risk:cooldown:U`` key and was
then vetoed ``BELOW_BROKER_MINIMUM`` — no order was ever sent. At 09:34:03 the
firm's first-ever stop-loss fired on ``U`` at -10.05% on a *different* account,
hit that key, and was vetoed ``SYMBOL_COOLDOWN_ACTIVE``. It executed on the
Runner's 900s re-publish, at **-10.47%**. Three accounts are three independent
books (ADR-0017); one book's traffic must not gate another's risk control.

**Why the cooldown became slot-aware.** A fixed 300s window and a five-minute
cadence are the same number, so an intraday strategy would have had roughly
every other decision vetoed — silently, as a rate veto rather than an error.
The window was never really about time: it was about "this strategy already
decided about this symbol." :mod:`shrap.research.strategy_runner.cadence`
already computes that as the strategy's *slot*, so an intent carrying its slot
is deduplicated exactly — once per session for a daily strategy, once per
interval for an intraday one — and no operator has to keep a timeout in sync
with a cadence.

Intents with no slot (exits, which fire on the book rather than on a cadence)
keep the time-based window. That is deliberate: an exit has no decision slot,
and the time window is the documented cross-restart backstop for the Runner's
own in-process suppression.
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

    # Per ACCOUNT, per UTC day. A runaway guard, not a trading budget: it bounds
    # the damage a signal loop can do, and any level that a working strategy
    # reaches in normal operation is throttling rather than guarding.
    #
    # Raised 80 -> 300 (Mike, 2026-09-18) for the intraday path. A 15-minute
    # cadence is 26 decision slots a session, so a top-10 book has a ceiling of
    # 260 orders a day; the realistic number is far lower because a six-month
    # momentum ranking rarely turns over within a session. 80 would have bound,
    # and it would have bound SILENTLY, as a rate veto indistinguishable from a
    # correctly suppressed duplicate. 300 still bounds a runaway to 300.
    max_orders_per_day: int = 300
    symbol_cooldown_seconds: int = 300


# An account that somehow reaches the limiter unnamed. Better than falling back
# to a shared key: an unnamed intent gets its own bucket and cannot consume, or
# be blocked by, a real account's budget. It is also greppable in Redis.
UNATTRIBUTED_ACCOUNT = "unattributed"


class RedisRateLimiter:
    """Consume one approval slot; return a veto reason code when exhausted.

    The daily counter is incremented before the cap check, so a capped day
    keeps counting attempts (useful in the audit trail). The cooldown key is
    only claimed when the daily cap allows, and only for the checked symbol
    **on the checked account**.

    Every key is namespaced by account, so the three paper accounts exhaust,
    and are blocked by, only their own budgets.
    """

    def __init__(self, redis: RateLimitRedis, config: RateLimitConfig) -> None:
        self._redis = redis
        self._config = config

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).date().isoformat()

    def _cooldown_key(self, account: str, symbol: str, slot: str | None) -> tuple[str, int]:
        """The dedup key for this intent, and how long it should live.

        With a slot, identity is ``(day, account, symbol, slot)`` and the key
        lives a day: the slot label repeats every session (``"09:45"``), so the
        date is what stops today's key from silencing tomorrow's decision. The
        TTL must outlive the slot rather than the cooldown window — a 300s TTL
        on a 15-minute slot would expire mid-slot and let the same decision
        through twice.

        Without a slot, it is the old time-boxed window, account-scoped.
        """

        if slot:
            return f"risk:cooldown:{self._today()}:{account}:{symbol}:{slot}", _DAY_KEY_TTL_SECONDS
        return f"risk:cooldown:{account}:{symbol}", self._config.symbol_cooldown_seconds

    async def acquire(
        self, ticker: str, *, account_id: str | None = None, slot: str | None = None
    ) -> str | None:
        """Return None when the order may proceed; a veto reason code otherwise.

        ``slot`` is the decision slot the emitting strategy was in. Entry
        signals carry it; exits do not, because an exit is a response to the
        book rather than a scheduled decision.
        """

        symbol = ticker.strip().upper()
        account = (account_id or "").strip() or UNATTRIBUTED_ACCOUNT
        if self._config.max_orders_per_day > 0:
            day_key = f"risk:approved-count:{self._today()}:{account}"
            approved_today = await self._redis.incr(day_key)
            if approved_today == 1:
                await self._redis.expire(day_key, _DAY_KEY_TTL_SECONDS)
            if approved_today > self._config.max_orders_per_day:
                return DAILY_CAP_REASON
        cooldown_key, ttl = self._cooldown_key(account, symbol, slot)
        if ttl > 0:
            claimed = await self._redis.set(cooldown_key, "1", nx=True, ex=ttl)
            if not claimed:
                return SYMBOL_COOLDOWN_REASON
        return None
