"""Event-loop wrapper for the Risk Officer.

Reads ``trading.decision.intent`` through a Redis consumer group (KI-006):
offsets persist in Redis, so a restart resumes where the group left off
instead of replaying stream history. ``start_id`` only positions the group
the first time it is created.

This process is the Risk Officer. It began as the Pre-Trade Checker, which its
own spec described as "the Month 1 wire-only Risk Officer stub"; the portfolio
layer in ``risk_compliance/risk_officer/`` is what graduates it. The stream
contract is unchanged — ``trading.decision.intent`` in, ``risk.intent.approved``
/ ``risk.intent.vetoed`` out — so the Decision Maker, Execution Agent and Audit
Logger were not touched.

Gates run in order and each may only tighten what the previous allowed:

1. ``PreTradeChecker``  paper-only, kill switch, universe, per-order cap
2. Tier-3 membership    tradeable-universe rule (ADR-0012), flag-gated
3. rate guardrails      velocity, spec step 7
4. **portfolio**        sizing, per-ticker, gross/net and cluster caps

The portfolio gate is last because it is the most expensive — it reads the book,
the equity curve and price history — and there is no reason to establish
exposure for an intent already vetoed for being off-universe.

It is also the only gate that can *scale* rather than veto. Spec step 8: "If
approved at less than the requested size, the intent is scaled down, not
rejected."
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol, cast

import structlog
from redis.asyncio import Redis

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.events import (
    Envelope,
    EventPublisher,
    PublishedEvent,
    ReceivedEvent,
    normalize_redis_fields,
)
from shrap.events.groups import GroupEventSubscriber, RedisGroupClient
from shrap.research.strategy_registry import PostgresStrategyRegistry
from shrap.risk_compliance.pre_trade import PreTradeChecker, RiskPolicy
from shrap.risk_compliance.rate_limit import RateLimitConfig, RateLimitRedis, RedisRateLimiter
from shrap.risk_compliance.risk_officer.limits import PortfolioLimits
from shrap.risk_compliance.risk_officer.monitor import SEVERITY_INFO
from shrap.risk_compliance.risk_officer.officer import RiskOfficer, SweepResult
from shrap.risk_compliance.risk_officer.store import DecisionRow, RiskStore
from shrap.risk_compliance.risk_officer.switch_store import RedisSwitchStore, SwitchRedis
from shrap.risk_compliance.tier3_membership import Tier3MembershipGate

log = structlog.get_logger(__name__)

STREAM_DECISION_INTENT = "trading.decision.intent"
STREAM_RISK_APPROVED = "risk.intent.approved"
STREAM_RISK_VETOED = "risk.intent.vetoed"
STREAM_RISK_ALERT = "risk.alert"
STREAM_KILL_SWITCH_SET = "risk.kill_switch.set"
STREAM_KILL_SWITCH_CLEAR = "risk.kill_switch.clear"
STREAM_REGIME_SIZING_MODIFIER = "intel.regime.sizing-modifier"
PRODUCED_BY = "risk/risk-officer"
SCHEMA_VERSION = "1.0.0"
# Unchanged from the stub. Renaming the group would make the graduated service
# re-read the stream from `start_id` and re-approve historical intents; the
# offsets are the reason it does not.
CONSUMER_GROUP = "pre-trade-checker"


class RedisStreamClient(Protocol):
    async def xadd(self, stream: str, fields: dict[str, str]) -> str: ...

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",
        mkstream: bool = False,
    ) -> Any: ...

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[Any, Any],
        count: int | None = None,
        block: int | None = None,
    ) -> Any: ...

    async def xack(self, name: str, groupname: str, *ids: str) -> Any: ...


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass


def build_risk_decision_payload(event: ReceivedEvent, policy: RiskPolicy) -> dict[str, Any]:
    """Build a deterministic risk decision payload for one intent event."""

    intent_payload = event.envelope.payload
    if intent_payload is None:
        raise ValueError("trading.decision.intent must carry an inline payload")

    decision = PreTradeChecker(policy).check(intent_payload)
    payload = decision.to_event_payload()
    payload["intent_event_id"] = event.envelope.event_id
    payload["intent_stream"] = event.stream
    payload["intent_redis_stream_id"] = event.redis_stream_id
    payload["intent_payload"] = intent_payload
    payload["reason"] = decision.reason_code
    payload["strategy_ids"] = intent_payload.get("strategy_ids", [])
    if decision.approved:
        approved_intent = dict(intent_payload)
        approved_intent["quantity"] = decision.approved_quantity
        payload["approved_intent_payload"] = approved_intent
    return payload


def _downgrade_to_veto(decision_payload: dict[str, Any], reason_code: str, note: str) -> None:
    """Flip an already-approved decision to a veto with ``reason_code``.

    Shared by the stateful gates (Tier 3 membership, rate guardrails) that run
    after the pure policy check and can only ever tighten an approval.
    """

    decision_payload["approved"] = False
    decision_payload["reason_code"] = reason_code
    decision_payload["reason"] = reason_code
    decision_payload["approved_quantity"] = 0
    decision_payload.pop("approved_intent_payload", None)
    reasons = decision_payload.get("reasons")
    if isinstance(reasons, list):
        reasons.append(note)


def _scale_down(
    decision_payload: dict[str, Any], quantity: int, reason_code: str, note: str
) -> None:
    """Reduce an approved intent's quantity, keeping it approved.

    The portfolio gate's distinguishing power (spec step 8). The order stays
    live at a size the book can carry, and ``approved_intent_payload`` — the
    payload the Execution Agent actually submits — is rewritten to match.
    Updating the reported quantity without updating that nested payload would
    send the original size to the broker while reporting the reduced one.
    """

    decision_payload["approved"] = True
    decision_payload["reason_code"] = reason_code
    decision_payload["reason"] = reason_code
    decision_payload["approved_quantity"] = quantity
    approved_intent = decision_payload.get("approved_intent_payload")
    if isinstance(approved_intent, dict):
        approved_intent["quantity"] = quantity
    reasons = decision_payload.get("reasons")
    if isinstance(reasons, list):
        reasons.append(note)


async def latest_regime(redis: Any) -> tuple[str | None, tuple[float, float] | None]:
    """Read the newest ``intel.regime.sizing-modifier`` label and band.

    A missing or malformed event yields ``(None, None)``, which
    ``limits.regime_multiplier`` resolves to the ``unknown`` band — quarter
    size. Absent regime state tightens the firm rather than freeing it.
    """

    try:
        entries = await redis.xrevrange(STREAM_REGIME_SIZING_MODIFIER, count=1)
    except Exception:
        log.warning("risk_officer.regime_read_failed", exc_info=True)
        return None, None
    if not entries:
        return None, None
    try:
        _, fields = entries[0]
        envelope = Envelope.from_redis_fields(normalize_redis_fields(fields))
    except Exception:
        log.warning("risk_officer.malformed_regime_event_skipped")
        return None, None
    if envelope.payload is None:
        return None, None
    label = envelope.payload.get("label")
    raw_band = envelope.payload.get("band")
    band: tuple[float, float] | None = None
    if isinstance(raw_band, list | tuple) and len(raw_band) == 2:
        try:
            band = (float(raw_band[0]), float(raw_band[1]))
        except (TypeError, ValueError):
            band = None
    return (str(label) if label is not None else None), band


async def process_intent_event(
    redis: RedisStreamClient,
    event: ReceivedEvent,
    policy: RiskPolicy,
    produced_by: str = PRODUCED_BY,
    rate_limiter: RedisRateLimiter | None = None,
    tier3_gate: Tier3MembershipGate | None = None,
    officer: RiskOfficer | None = None,
) -> PublishedEvent:
    """Run the pure pre-trade check (plus stateful gates) and publish the result.

    The stateful gates apply after the deterministic policy check and only to
    already-approved intents: an intent the policy would veto never consults
    Tier 3 state or consumes a rate slot. Tier 3 membership is checked before
    the rate guardrail so a non-tradeable ticker never claims a rate slot.
    """

    decision_payload = build_risk_decision_payload(event, policy)
    if decision_payload["approved"] and tier3_gate is not None:
        ticker = str(decision_payload.get("ticker", ""))
        tier3_veto = await tier3_gate.check(ticker)
        if tier3_veto is not None:
            _downgrade_to_veto(decision_payload, tier3_veto, f"tier-3 gate: {tier3_veto}")
            log.warning(
                "pre_trade_checker.tier3_vetoed",
                intent_event_id=event.envelope.event_id,
                ticker=ticker,
                reason=tier3_veto,
            )
    if decision_payload["approved"] and rate_limiter is not None:
        rate_veto = await rate_limiter.acquire(str(decision_payload.get("ticker", "")))
        if rate_veto is not None:
            _downgrade_to_veto(decision_payload, rate_veto, f"rate guardrail: {rate_veto}")
            log.warning(
                "pre_trade_checker.rate_vetoed",
                intent_event_id=event.envelope.event_id,
                ticker=decision_payload.get("ticker"),
                reason=rate_veto,
            )
    if decision_payload["approved"] and officer is not None:
        await _apply_portfolio_gate(redis, decision_payload, event, officer)
    stream = STREAM_RISK_APPROVED if decision_payload["approved"] else STREAM_RISK_VETOED
    published = await EventPublisher(redis).publish(
        stream=stream,
        produced_by=produced_by,
        schema_version=SCHEMA_VERSION,
        payload=decision_payload,
        correlation_id=event.envelope.event_id,
    )
    if officer is not None:
        await _record_decision(officer, published.envelope.event_id, event, decision_payload)
    return published


async def _apply_portfolio_gate(
    redis: RedisStreamClient,
    decision_payload: dict[str, Any],
    event: ReceivedEvent,
    officer: RiskOfficer,
) -> None:
    """Assess exposure and either scale the intent down or veto it."""

    intent = decision_payload.get("intent_payload") or {}
    label, band = await latest_regime(redis)
    assessment = await officer.assess(
        ticker=str(decision_payload.get("ticker", "")),
        side=str(intent.get("side", "buy")),
        quantity=int(decision_payload.get("approved_quantity", 0)),
        strategy_ids=[str(s) for s in decision_payload.get("strategy_ids", [])],
        regime_label=label,
        regime_band=band,
    )
    decision_payload["portfolio"] = assessment.to_payload()
    note = "; ".join(assessment.notes) or assessment.reason_code
    if not assessment.approved:
        _downgrade_to_veto(decision_payload, assessment.reason_code, f"portfolio: {note}")
        log.warning(
            "risk_officer.portfolio_vetoed",
            intent_event_id=event.envelope.event_id,
            ticker=decision_payload.get("ticker"),
            reason=assessment.reason_code,
            binding_limit=assessment.binding_limit,
            account_id=assessment.account_id,
        )
        return
    if assessment.approved_quantity < int(decision_payload.get("approved_quantity", 0)):
        _scale_down(
            decision_payload,
            assessment.approved_quantity,
            assessment.reason_code,
            f"portfolio: {note}",
        )
        log.info(
            "risk_officer.portfolio_scaled",
            intent_event_id=event.envelope.event_id,
            ticker=decision_payload.get("ticker"),
            approved_quantity=assessment.approved_quantity,
            binding_limit=assessment.binding_limit,
        )


async def _record_decision(
    officer: RiskOfficer,
    event_id: str,
    event: ReceivedEvent,
    decision_payload: dict[str, Any],
) -> None:
    """Append the decision to ``risk.decisions``.

    Failure is logged and swallowed: the decision has already been published and
    the Execution Agent acts on the event, not on this row. Raising here would
    make the order path depend on the forensic log, turning a full audit trail
    into a trading outage.
    """

    portfolio = decision_payload.get("portfolio") or {}
    intent = decision_payload.get("intent_payload") or {}
    try:
        await officer.store.record_decision(
            DecisionRow(
                event_id=event_id,
                intent_event_id=event.envelope.event_id,
                account_id=portfolio.get("account_id"),
                ticker=decision_payload.get("ticker"),
                side=str(intent.get("side")) if intent.get("side") else None,
                approved=bool(decision_payload.get("approved")),
                reason_code=str(decision_payload.get("reason_code", "")),
                requested_quantity=decision_payload.get("requested_quantity"),
                approved_quantity=decision_payload.get("approved_quantity"),
                binding_limit=portfolio.get("binding_limit"),
                strategy_ids=[str(s) for s in decision_payload.get("strategy_ids", [])],
                detail={
                    "reasons": decision_payload.get("reasons", []),
                    "portfolio": portfolio,
                },
            )
        )
    except Exception:
        log.error("risk_officer.decision_not_recorded", event_id=event_id, exc_info=True)


async def poll_once(
    redis: RedisStreamClient,
    policy: RiskPolicy,
    subscriber: GroupEventSubscriber,
    count: int,
    block_ms: int,
    rate_limiter: RedisRateLimiter | None = None,
    tier3_gate: Tier3MembershipGate | None = None,
    retry_delay_seconds: float = 0.0,
    officer: RiskOfficer | None = None,
) -> int:
    """Read one batch of decision intents and publish risk decisions.

    Successful and permanently-invalid events are acknowledged; a systemic
    failure leaves the event pending so the group redelivers it first on the
    next cycle, after ``retry_delay_seconds``.
    """

    try:
        events = await subscriber.read(
            streams=[STREAM_DECISION_INTENT], count=count, block_ms=block_ms
        )
    except Exception:
        log.exception("pre_trade_checker.read_failed", group=subscriber.group)
        await asyncio.sleep(retry_delay_seconds)
        return 0
    processed = 0
    for event in events:
        try:
            result = await process_intent_event(
                redis,
                event,
                policy,
                rate_limiter=rate_limiter,
                tier3_gate=tier3_gate,
                officer=officer,
            )
            await subscriber.ack(event)
            processed += 1
            log.info(
                "pre_trade_checker.decision_published",
                intent_event_id=event.envelope.event_id,
                stream=result.stream,
                risk_event_id=result.envelope.event_id,
            )
        except ValueError:
            # Malformed intent: permanent for this event. Ack and skip it or
            # the gate stalls forever on a poison message.
            log.exception(
                "pre_trade_checker.intent_invalid_skipped",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                intent_event_id=event.envelope.event_id,
            )
            await subscriber.ack(event)
            continue
        except Exception:
            # Systemic error: do NOT ack, so the same event retries next cycle.
            log.exception(
                "pre_trade_checker.intent_failed",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                intent_event_id=event.envelope.event_id,
            )
            await asyncio.sleep(retry_delay_seconds)
            break
    return processed


async def monitor_once(
    redis: RedisStreamClient,
    officer: RiskOfficer,
    assignments: Sequence[tuple[str, str]],
    produced_by: str = PRODUCED_BY,
    now: datetime | None = None,
) -> SweepResult:
    """Run one monitoring sweep and publish what it found.

    Alerts are published for warnings as well as breaches, so an account
    approaching a halt is visible before it halts. Switch transitions are
    published separately on ``risk.kill_switch.set`` / ``.clear`` because those
    are state changes other agents may act on, not observations.
    """

    result = await officer.sweep(assignments, now=now)
    publisher = EventPublisher(redis)
    for observation in result.observations:
        if observation.severity == SEVERITY_INFO:
            continue
        await publisher.publish(
            stream=STREAM_RISK_ALERT,
            produced_by=produced_by,
            schema_version=SCHEMA_VERSION,
            payload=observation.to_payload(),
        )
        log.warning(
            "risk_officer.limit_alert",
            limit=observation.limit,
            severity=observation.severity,
            observed=observation.observed,
            threshold=observation.threshold,
            account_id=observation.account_id,
            strategy_id=observation.strategy_id,
        )
    for transition in result.transitions:
        await publisher.publish(
            stream=(STREAM_KILL_SWITCH_SET if transition.was_set else STREAM_KILL_SWITCH_CLEAR),
            produced_by=produced_by,
            schema_version=SCHEMA_VERSION,
            payload=transition.state.to_payload(),
        )
    return result


async def monitor_loop(
    redis: RedisStreamClient,
    officer: RiskOfficer,
    assignments_source: Callable[[], Awaitable[Sequence[tuple[str, str]]]],
    stop: asyncio.Event,
    interval_seconds: float = 300.0,
    produced_by: str = PRODUCED_BY,
) -> None:
    """Re-check the continuous limits every ``interval_seconds``.

    The spec asks for a 5-minute heartbeat "because limits may tighten when
    regime changes". It runs as a task beside the order loop rather than inside
    it: an intent-driven check would only fire when the firm is already trading,
    and a drawdown breach matters most on the day nothing is being traded.
    """

    while not stop.is_set():
        try:
            assignments = await assignments_source()
            if assignments:
                await monitor_once(redis, officer, assignments, produced_by=produced_by)
        except Exception:
            log.exception("risk_officer.monitor_failed")
        await _interruptible_sleep(stop, interval_seconds)


async def _interruptible_sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run_loop(
    redis: RedisStreamClient,
    policy: RiskPolicy,
    stop: asyncio.Event,
    start_id: str = "0-0",
    count: int = 100,
    block_ms: int = 5000,
    retry_delay_seconds: float = 1.0,
    rate_limiter: RedisRateLimiter | None = None,
    tier3_gate: Tier3MembershipGate | None = None,
    group: str = CONSUMER_GROUP,
    consumer: str | None = None,
    officer: RiskOfficer | None = None,
) -> None:
    """Run the Risk Officer loop until ``stop`` is set.

    Offsets persist in the ``group`` consumer group (KI-006); ``start_id``
    only positions the group the first time it is created on the stream. The
    Redis-backed rate limiter remains the guard against re-approving intents
    if the group is ever recreated: replayed approvals hit the cooldown/daily
    cap instead of minting fresh orders.
    """

    subscriber = GroupEventSubscriber(
        cast(RedisGroupClient, redis),
        group=group,
        consumer=consumer,
        start_id=start_id,
    )
    while not stop.is_set():
        try:
            processed = await poll_once(
                redis=redis,
                policy=policy,
                subscriber=subscriber,
                count=count,
                block_ms=block_ms,
                rate_limiter=rate_limiter,
                tier3_gate=tier3_gate,
                retry_delay_seconds=retry_delay_seconds,
                officer=officer,
            )
            if processed:
                log.info("pre_trade_checker.batch", processed=processed, group=group)
            else:
                await asyncio.sleep(0)
        except Exception:
            log.exception("pre_trade_checker.poll_failed")
            await asyncio.sleep(retry_delay_seconds)


def couple_universe_gate(policy: RiskPolicy, tier3_enforcement: bool) -> RiskPolicy:
    """Couple the static allowlist to Tier 3 enforcement.

    When Tier 3 enforcement is on, Tier 3 membership is the authoritative
    universe gate, so the static allowed_universe allowlist (a Month-1 stub)
    is disabled — otherwise only the allowlist∩Tier-3 intersection would be
    tradeable. Disabling is coupled to the SAME flag that builds the Tier 3
    gate in run(), so the static allowlist is off if and only if the Tier 3
    gate is active. There is never a running loop with neither universe gate.
    """
    return replace(policy, universe_check_enabled=not tier3_enforcement)


async def run(
    redis_url: str,
    policy: RiskPolicy,
    service_name: str = "risk/pre-trade-checker",
    log_level: str = "INFO",
    start_id: str = "0-0",
    count: int = 100,
    block_ms: int = 5000,
    retry_delay_seconds: float = 1.0,
    rate_limit_config: RateLimitConfig | None = None,
    tier3_enforcement: bool = False,
    postgres_dsn: str = "",
    tier3_cache_ttl_seconds: float = 30.0,
    group: str = CONSUMER_GROUP,
    consumer: str | None = None,
    portfolio_limits_enforcement: bool = False,
    portfolio_limits: PortfolioLimits | None = None,
    monitor_interval_seconds: float = 300.0,
) -> None:
    """Run the Risk Officer service until SIGINT/SIGTERM."""

    configure_logging(service_name, log_level)
    log.info(
        "pre_trade_checker.starting",
        redis_url=redis_url,
        start_id=start_id,
        count=count,
        block_ms=block_ms,
        group=group,
        consumer=consumer or group,
        tier3_enforcement=tier3_enforcement,
        rate_limit=(
            {
                "max_orders_per_day": rate_limit_config.max_orders_per_day,
                "symbol_cooldown_seconds": rate_limit_config.symbol_cooldown_seconds,
            }
            if rate_limit_config
            else None
        ),
    )
    # Couple the static allowlist to the SAME flag that builds the Tier 3 gate
    # below (ADR-0012; Mike's 2026-07-24 ruling). When Tier 3 enforcement is on,
    # Tier 3 membership is the authoritative universe gate and the Month-1 static
    # allowlist is disabled here; when off, the allowlist stays the interim
    # guardrail. Invariant: in any running loop, universe_check_enabled == False
    # implies the Tier 3 gate is active — and that gate itself fails closed on
    # empty/unavailable state (see tier3_membership.Tier3MembershipGate), so
    # disabling the static allowlist opens no hole even during a DB outage.
    policy = couple_universe_gate(policy, tier3_enforcement)
    log.info(
        "pre_trade_checker.universe_gate",
        static_allowlist_enforced=policy.universe_check_enabled,
        authoritative_gate=("static_allowlist" if policy.universe_check_enabled else "tier3"),
        note=(
            "static allowed_universe allowlist is the binding universe gate"
            if policy.universe_check_enabled
            else "static allowlist unenforced; Tier 3 membership is the universe gate"
        ),
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    redis: Redis = Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_timeout=(block_ms / 1000) + 10,
    )
    rate_limiter = (
        RedisRateLimiter(cast(RateLimitRedis, redis), rate_limit_config)
        if rate_limit_config
        else None
    )
    # Tier 3 enforcement is opt-in (ADR-0012). When off, the rule is skipped
    # entirely and no Postgres connection is opened — the permissive default is
    # an explicit human choice, logged once here. When on, the gate fails closed
    # on any Tier 3 state error (see tier3_membership.Tier3MembershipGate).
    pool: Any = None
    tier3_gate: Tier3MembershipGate | None = None
    if tier3_enforcement:
        log.info(
            "pre_trade_checker.tier3_enforcement_on",
            cache_ttl_seconds=tier3_cache_ttl_seconds,
            note="rejecting any ticker not currently in Tier 3; unavailable state fails closed",
        )
        pool = await create_asyncpg_pool(postgres_dsn)
        tier3_gate = Tier3MembershipGate(pool, ttl_seconds=tier3_cache_ttl_seconds)
    else:
        log.info(
            "pre_trade_checker.tier3_enforcement_off",
            note="Tier 3 membership filter disabled by config; no tier-membership vetoes",
        )

    # The portfolio layer is opt-in for the same reason Tier 3 is: it reads
    # ops.position_snapshots, and until the Reconciliation Agent has run a pass
    # with the positions fetch that table is empty. An empty book is
    # indistinguishable from an unmeasured one, the gate fails closed on the
    # latter, and every order would be vetoed — including the smoke path.
    officer: RiskOfficer | None = None
    monitor_task: asyncio.Task[None] | None = None
    if portfolio_limits_enforcement:
        limits = portfolio_limits or PortfolioLimits()
        if pool is None:
            pool = await create_asyncpg_pool(postgres_dsn)
        risk_store = RiskStore(pool)
        await risk_store.ensure_schema()
        officer = RiskOfficer(
            store=risk_store,
            switch_store=RedisSwitchStore(cast(SwitchRedis, redis)),
            registry=PostgresStrategyRegistry(pool),
            limits=limits,
        )
        # Redis is a cache of the Postgres log, and a Redis flush or restart
        # would silently clear every switch. Rebuilding at startup means a halt
        # survives the thing most likely to end it by accident.
        await officer.rebuild_switches()
        log.info(
            "risk_officer.portfolio_enforcement_on",
            limits=limits,
            monitor_interval_seconds=monitor_interval_seconds,
            note="per-ticker, gross/net and cluster caps enforced; see docs/risk/policy.md",
        )
        monitor_task = asyncio.create_task(
            monitor_loop(
                cast(RedisStreamClient, redis),
                officer,
                lambda: _account_assignments(cast(Any, pool)),
                stop,
                interval_seconds=monitor_interval_seconds,
            )
        )
    else:
        log.warning(
            "risk_officer.portfolio_enforcement_off",
            note=(
                "no per-ticker, gross/net, cluster, daily-loss or drawdown limits are "
                "enforced. The only order-path controls are the per-order cap and the "
                "manual kill switch."
            ),
        )
    try:
        await run_loop(
            cast(RedisStreamClient, redis),
            policy=policy,
            stop=stop,
            start_id=start_id,
            count=count,
            block_ms=block_ms,
            retry_delay_seconds=retry_delay_seconds,
            rate_limiter=rate_limiter,
            tier3_gate=tier3_gate,
            group=group,
            consumer=consumer,
            officer=officer,
        )
    finally:
        stop.set()
        if monitor_task is not None:
            monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await monitor_task
        await redis.aclose()
        if pool is not None:
            await pool.close()
        log.info("pre_trade_checker.stopped")


async def _account_assignments(pool: Any) -> Sequence[tuple[str, str]]:
    """Live ``(strategy_id, account_id)`` pairs for the monitor to sweep.

    Only strategies actually holding an account are swept — ADR-0017 makes the
    account the unit of measurement, so a strategy without one has no equity
    curve to have a drawdown on.
    """

    registry = PostgresStrategyRegistry(pool)
    records = await registry.list_all()
    return [(r.strategy_id, r.account_id) for r in records if r.account_id]


__all__ = [
    "CONSUMER_GROUP",
    "PRODUCED_BY",
    "SCHEMA_VERSION",
    "STREAM_DECISION_INTENT",
    "STREAM_KILL_SWITCH_CLEAR",
    "STREAM_KILL_SWITCH_SET",
    "STREAM_REGIME_SIZING_MODIFIER",
    "STREAM_RISK_ALERT",
    "STREAM_RISK_APPROVED",
    "STREAM_RISK_VETOED",
    "build_risk_decision_payload",
    "couple_universe_gate",
    "latest_regime",
    "monitor_loop",
    "monitor_once",
    "poll_once",
    "process_intent_event",
    "run",
    "run_loop",
]
