"""Strategy Librarian service: verdict events → registry transitions → lifecycle events.

The Librarian is the registry's event-facing half (spec:
``docs/agents/research/strategy-librarian.md``). It consumes
``research.strategy.verdict`` events, applies the corresponding lifecycle
transition through :class:`~shrap.research.strategy_registry.PostgresStrategyRegistry`
(which enforces the state machine), and publishes the resulting
``research.strategy.*`` lifecycle event. It never originates a decision — a
verdict with no valid transition is skipped loudly, not reinterpreted.

Poison discipline (three-class, per PR #27/#32/#37):

- Malformed payload, unknown strategy, or a transition the state machine
  rejects (typically a replayed verdict whose transition already applied,
  caught by ``expected_from``) → ack and skip.
- Systemic error (DB/Redis down) → no ack; the event stays pending and is
  redelivered next cycle.

Consumer group ``start_id`` defaults to ``"0"``: verdicts are decisions that
must be applied even if the Librarian was down when they were published, and
replay is idempotent because ``expected_from`` rejects already-applied moves.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any, Protocol, cast

import structlog
from redis.asyncio import Redis

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.events import EventPublisher, ReceivedEvent
from shrap.events.groups import GroupEventSubscriber, RedisGroupClient
from shrap.research.strategy_registry import (
    InvalidTransitionError,
    PostgresStrategyRegistry,
    StrategyNotFoundError,
    StrategyTransition,
    stream_for_transition,
    transition_event_payload,
)

log = structlog.get_logger(__name__)

STREAM_STRATEGY_VERDICT = "research.strategy.verdict"
CONSUMER_GROUP = "strategy-librarian"
PRODUCED_BY = "strategy-librarian"
SCHEMA_VERSION = "1.0.0"

# Verdicts that legitimately change no lifecycle state.
NOOP_VERDICTS = frozenset({"hold-for-data", "accept-refit", "reject-refit-keep-prior"})


class StrategyRegistry(Protocol):
    """The slice of the registry the Librarian needs."""

    async def transition(
        self,
        strategy_id: str,
        to_status: str,
        *,
        reason: str,
        trigger_kind: str,
        actor: str,
        trigger_ref: str | None = None,
        expected_from: str | None = None,
    ) -> StrategyTransition: ...


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


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"verdict payload must include a non-empty {key!r}")
    return value.strip()


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


async def apply_verdict_event(
    registry: StrategyRegistry,
    event: ReceivedEvent,
    actor: str = PRODUCED_BY,
) -> StrategyTransition | None:
    """Apply one verdict event to the registry.

    Returns the transition, or None for verdicts that change no state.
    Raises ValueError for malformed payloads; StrategyNotFoundError and
    InvalidTransitionError pass through from the registry.
    """

    payload = event.envelope.payload
    if payload is None:
        raise ValueError("verdict event must carry an inline payload")
    strategy_id = _required_str(payload, "strategy_id")
    verdict = _required_str(payload, "verdict")
    if verdict in NOOP_VERDICTS:
        return None
    to_stage = _required_str(payload, "to_stage")
    from_stage = _optional_str(payload, "from_stage")
    reason = _optional_str(payload, "reason") or f"evaluator verdict: {verdict}"
    trigger_ref = (
        _optional_str(payload, "metrics_ref")
        or _optional_str(payload, "trigger")
        or event.envelope.event_id
    )
    return await registry.transition(
        strategy_id,
        to_stage,
        reason=reason,
        trigger_kind="evaluation",
        trigger_ref=trigger_ref,
        actor=actor,
        expected_from=from_stage,
    )


async def poll_once(
    redis: RedisStreamClient,
    registry: StrategyRegistry,
    subscriber: GroupEventSubscriber,
    count: int,
    block_ms: int,
    retry_delay_seconds: float = 0.0,
    produced_by: str = PRODUCED_BY,
) -> int:
    """Process one batch of verdict events; returns transitions applied."""

    try:
        events = await subscriber.read(
            streams=[STREAM_STRATEGY_VERDICT], count=count, block_ms=block_ms
        )
    except Exception:
        log.exception("strategy_librarian.read_failed", group=subscriber.group)
        await asyncio.sleep(retry_delay_seconds)
        return 0

    applied = 0
    for event in events:
        try:
            transition = await apply_verdict_event(registry, event, actor=produced_by)
            if transition is None:
                log.info(
                    "strategy_librarian.verdict_noop",
                    verdict_event_id=event.envelope.event_id,
                )
                await subscriber.ack(event)
                continue
            stream = stream_for_transition(transition.from_status, transition.to_status)
            result = await EventPublisher(redis).publish(
                stream=stream,
                produced_by=produced_by,
                schema_version=SCHEMA_VERSION,
                payload=transition_event_payload(transition),
                correlation_id=event.envelope.event_id,
            )
            await subscriber.ack(event)
            applied += 1
            log.info(
                "strategy_librarian.transition_applied",
                strategy_id=transition.strategy_id,
                from_status=transition.from_status,
                to_status=transition.to_status,
                lifecycle_stream=stream,
                lifecycle_event_id=result.envelope.event_id,
                verdict_event_id=event.envelope.event_id,
            )
        except (ValueError, StrategyNotFoundError, InvalidTransitionError):
            # Permanent for this event: malformed payload, unknown strategy,
            # or a move the state machine rejects (usually a replayed verdict
            # already applied — expected_from catches it). Ack and skip, or
            # the consumer stalls forever on a poison message.
            log.exception(
                "strategy_librarian.verdict_skipped",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                verdict_event_id=event.envelope.event_id,
            )
            await subscriber.ack(event)
            continue
        except Exception:
            # Systemic error (database down): no ack, so the same verdict is
            # redelivered next cycle.
            log.exception(
                "strategy_librarian.verdict_failed",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                verdict_event_id=event.envelope.event_id,
            )
            await asyncio.sleep(retry_delay_seconds)
            break
    return applied


async def run_loop(
    redis: RedisStreamClient,
    registry: StrategyRegistry,
    stop: asyncio.Event,
    start_id: str = "0",
    count: int = 100,
    block_ms: int = 5000,
    retry_delay_seconds: float = 1.0,
    group: str = CONSUMER_GROUP,
    consumer: str | None = None,
) -> None:
    """Run the Librarian consumer loop until ``stop`` is set."""

    subscriber = GroupEventSubscriber(
        cast(RedisGroupClient, redis),
        group=group,
        consumer=consumer,
        start_id=start_id,
    )
    while not stop.is_set():
        try:
            applied = await poll_once(
                redis=redis,
                registry=registry,
                subscriber=subscriber,
                count=count,
                block_ms=block_ms,
                retry_delay_seconds=retry_delay_seconds,
            )
            if applied:
                log.info("strategy_librarian.batch", applied=applied, group=group)
            else:
                await asyncio.sleep(0)
        except Exception:
            log.exception("strategy_librarian.poll_failed")
            await asyncio.sleep(retry_delay_seconds)


async def run(
    redis_url: str,
    postgres_dsn: str,
    service_name: str = PRODUCED_BY,
    log_level: str = "INFO",
    start_id: str = "0",
    count: int = 100,
    block_ms: int = 5000,
    retry_delay_seconds: float = 1.0,
    group: str = CONSUMER_GROUP,
    consumer: str | None = None,
) -> None:
    """Run the Strategy Librarian service until SIGINT/SIGTERM."""

    configure_logging(service_name, log_level)
    log.info(
        "strategy_librarian.starting",
        redis_url=redis_url,
        postgres_dsn="***",
        start_id=start_id,
        group=group,
        consumer=consumer or group,
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    redis: Redis = Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_timeout=(block_ms / 1000) + 10,
    )
    pool = await create_asyncpg_pool(postgres_dsn)
    registry = PostgresStrategyRegistry(pool)
    await registry.ensure_schema()
    try:
        await run_loop(
            cast(RedisStreamClient, redis),
            registry=registry,
            stop=stop,
            start_id=start_id,
            count=count,
            block_ms=block_ms,
            retry_delay_seconds=retry_delay_seconds,
            group=group,
            consumer=consumer,
        )
    finally:
        await redis.aclose()
        await pool.close()
        log.info("strategy_librarian.stopped")


__all__ = [
    "CONSUMER_GROUP",
    "NOOP_VERDICTS",
    "PRODUCED_BY",
    "STREAM_STRATEGY_VERDICT",
    "apply_verdict_event",
    "poll_once",
    "run",
    "run_loop",
]
