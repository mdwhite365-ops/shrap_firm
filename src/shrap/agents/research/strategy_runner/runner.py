"""Paper-strategy runner service loop.

Long-running consumer of ``operations.market-phase``. On each entry into phase
``open`` it runs one evaluation pass: for every active *paper-stage* strategy in
the registry it reads a trailing daily-bar window, computes today's flat/invested
target through the reused Strategy Evaluator factory seam, and emits a
``trading.strategy.signal`` for each target *transition* (flat -> invested = buy,
invested -> flat = sell). It emits **signals only** — the Decision Maker ->
Pre-Trade Checker -> Execution chain owns everything downstream. PAPER ONLY: no
intents, no broker calls, no real money.

Delivery / idempotency (KI-006 consumer group + a per-session state guard):

- Offsets live in the ``strategy-runner`` consumer group, so restarts resume
  where the group left off. ``start_id`` defaults to ``"$"`` (new events only);
  a market-phase event published while the runner was down is not replayed.
- The pass is idempotent on ``(strategy_id, session_date)``: a strategy already
  stamped for the session is skipped by the pure planner, so a re-delivered
  ``open`` event, a ``startup``/catch-up event, or a restart mid-session never
  double-emits. We do *not* gate on ``reason``; the session-date guard is the
  guard.
- Poison discipline: a malformed phase payload (bad ``session_date``) is acked
  and skipped; a systemic error (DB/Redis down) is *not* acked, so the event
  stays pending and the pass is retried in full next cycle.

Fail-safe: a single bad strategy (missing bars, bad spec, factory error) is
skipped by the planner with a logged reason; it never crashes the loop and
never emits a partial signal.

Sizing: each pass reads equity for **its own account** from
``ops.account_snapshots`` (written by the Reconciliation Agent) and converts every
entry's target weight into a share count. Unusable equity — missing, stale, or
belonging to no configured account — refuses the *whole pass* rather than falling
back, and the phase event is left un-acked so the pass retries once a fresh
snapshot lands.

``account_id`` is required. ADR-0017 gives each strategy its own broker account,
and an unscoped read returns whichever account reported most recently: a
plausible number from the wrong book, which is the worst kind of wrong.
"""

from __future__ import annotations

import asyncio
import signal
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol, cast

import structlog
from redis.asyncio import Redis

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.events import EventPublisher, RedisPublisher
from shrap.events.groups import GroupEventSubscriber, RedisGroupClient
from shrap.operations.market_phase import Phase
from shrap.research.strategy_evaluator.pipeline import _default_strategy_factory, _extract_tickers
from shrap.research.strategy_evaluator.store import PostgresEvaluatorReader
from shrap.research.strategy_evaluator.strategy import BarSample
from shrap.research.strategy_fixture import FixtureRedis, latest_regime_label
from shrap.research.strategy_registry import (
    STATUS_LIVE_PAPER,
    STATUS_PAPER,
    STATUS_SMALL_SIZE_PAPER,
    PostgresStrategyRegistry,
    StrategyRecord,
)
from shrap.research.strategy_runner.engine import (
    PRODUCED_BY,
    SCHEMA_VERSION,
    STREAM_STRATEGY_SIGNAL,
    PlannedStateWrite,
    RunnerSignalConfig,
    StrategyInput,
    TargetState,
    plan_session,
)
from shrap.research.strategy_runner.sizing import SizingRefused, assert_equity_usable
from shrap.research.strategy_runner.store import PostgresStrategyRunnerStateStore

log = structlog.get_logger(__name__)

STREAM_MARKET_PHASE = "operations.market-phase"
CONSUMER_GROUP = "strategy-runner"

# The paper stages a signal may be emitted for. Deliberately NOT the registry's
# ``_ACTIVE_STAGES`` (which also includes ``hypothesis``): a hypothesis strategy
# has not been evaluated/promoted and must never reach the trading path.
ACTIVE_PAPER_STAGES: tuple[str, ...] = (
    STATUS_PAPER,
    STATUS_SMALL_SIZE_PAPER,
    STATUS_LIVE_PAPER,
)


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

    async def xrevrange(
        self, name: str, max: str = "+", min: str = "-", count: int | None = None
    ) -> Any: ...


class Registry(Protocol):
    async def list_by_status(self, status: str) -> list[StrategyRecord]: ...


class BarReader(Protocol):
    async def read_bars(
        self, ticker: str, start: date, end: date, adjustment: str
    ) -> list[BarSample]: ...


class StateStore(Protocol):
    async def read_state(self) -> dict[tuple[str, str], TargetState]: ...

    async def latest_equity(self, account_id: str) -> tuple[float | None, datetime | None]: ...

    async def upsert(self, write: PlannedStateWrite) -> None: ...


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass


def _parse_session_date(payload: dict[str, Any]) -> date:
    raw = payload.get("session_date")
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"market-phase payload missing session_date: {raw!r}")
    return date.fromisoformat(raw)  # ValueError on a malformed date -> poison, acked


def _lookback_start(session_date: date, warmup: int, buffer_days: int, max_days: int) -> date:
    """Trailing calendar window comfortably longer than ``warmup`` trading days."""

    span = min(max(warmup, 1) * 2 + buffer_days, max_days)
    return session_date - timedelta(days=span)


async def _active_paper_strategies(registry: Registry) -> list[StrategyRecord]:
    records: list[StrategyRecord] = []
    seen: set[str] = set()
    for stage in ACTIVE_PAPER_STAGES:
        for record in await registry.list_by_status(stage):
            if record.strategy_id not in seen:
                seen.add(record.strategy_id)
                records.append(record)
    return records


async def _build_input(
    record: StrategyRecord,
    reader: BarReader,
    session_date: date,
    *,
    adjustment: str,
    buffer_days: int,
    max_days: int,
) -> StrategyInput:
    """Assemble the trailing bars for one strategy (I/O; planner does the logic)."""

    tickers = _extract_tickers(record.tickers)
    if not tickers:
        return StrategyInput(record=record, tickers=tickers, bars_by_ticker={})
    try:
        warmup = int(_default_strategy_factory(record, tickers).warmup)
    except Exception:
        # A broken spec: the planner re-derives and skips it fail-safe. Read a
        # short window; the bars will be unused.
        warmup = 1
    start = _lookback_start(session_date, warmup, buffer_days, max_days)
    bars_by_ticker: dict[str, list[BarSample]] = {}
    for ticker in tickers:
        bars_by_ticker[ticker] = await reader.read_bars(ticker, start, session_date, adjustment)
    return StrategyInput(record=record, tickers=tickers, bars_by_ticker=bars_by_ticker)


async def run_pass(
    *,
    session_date: date,
    redis: RedisStreamClient,
    registry: Registry,
    reader: BarReader,
    state_store: StateStore,
    config: RunnerSignalConfig,
    adjustment: str,
    lookback_buffer_days: int,
    lookback_max_days: int,
    account_id: str,
    produced_by: str = PRODUCED_BY,
) -> int:
    """Run one evaluation pass for ``session_date``; returns signals emitted.

    Systemic errors (registry/bars/state/publish) propagate so the caller can
    leave the market-phase event un-acked and retry the whole pass next cycle.
    """

    records = await _active_paper_strategies(registry)
    if not records:
        log.info("strategy_runner.no_active_strategies", session_date=session_date.isoformat())
        return 0

    # Account size first: every entry this pass is a fraction of it, so an
    # unusable snapshot must stop the pass before any signal is planned.
    # SizingRefused propagates — the caller leaves the phase event un-acked and
    # the pass retries once the Reconciliation Agent writes a fresh snapshot.
    if not account_id:
        raise SizingRefused(
            "no account configured (STRATEGY_RUNNER_ACCOUNT_ID is unset), so there "
            "is no book to size against. Refusing rather than picking whichever "
            "account reported most recently."
        )
    raw_equity, observed_at = await state_store.latest_equity(account_id)
    equity = assert_equity_usable(raw_equity, observed_at, datetime.now(UTC))

    stored_state = await state_store.read_state()
    regime_label = await latest_regime_label(cast(FixtureRedis, redis))  # informational only

    inputs = [
        await _build_input(
            record,
            reader,
            session_date,
            adjustment=adjustment,
            buffer_days=lookback_buffer_days,
            max_days=lookback_max_days,
        )
        for record in records
    ]

    plans = plan_session(
        session_date=session_date,
        strategies=inputs,
        stored_state=stored_state,
        factory=_default_strategy_factory,
        config=config,
        regime_label=regime_label,
        equity=equity,
    )

    publisher = EventPublisher(cast(RedisPublisher, redis))
    emitted = 0
    for plan in plans:
        if plan.skipped:
            log.info(
                "strategy_runner.strategy_skipped",
                strategy_id=plan.strategy_id,
                reason=plan.skip_reason,
                session_date=session_date.isoformat(),
            )
            continue
        # A clamped or unfundable entry means the live book is not the evaluated
        # book for that name. Silence here is the failure mode this card exists
        # to remove, so it is logged even though nothing went wrong.
        for note in plan.sizing_notes:
            log.warning(
                "strategy_runner.sizing_note",
                strategy_id=plan.strategy_id,
                note=note,
                equity=equity,
                session_date=session_date.isoformat(),
            )
        for planned in plan.signals:
            result = await publisher.publish(
                stream=STREAM_STRATEGY_SIGNAL,
                produced_by=produced_by,
                schema_version=SCHEMA_VERSION,
                payload=planned.payload,
            )
            emitted += 1
            log.info(
                "strategy_runner.signal_published",
                strategy_id=planned.strategy_id,
                ticker=planned.ticker,
                side=planned.side,
                quantity=planned.payload["quantity"],
                event_id=result.envelope.event_id,
                session_date=session_date.isoformat(),
            )
        # Stamp state only after this strategy's signals are published, so a
        # crash mid-strategy re-runs (and re-emits) at most this one strategy.
        for write in plan.state_writes:
            await state_store.upsert(write)
    return emitted


async def poll_once(
    redis: RedisStreamClient,
    subscriber: GroupEventSubscriber,
    *,
    registry: Registry,
    reader: BarReader,
    state_store: StateStore,
    config: RunnerSignalConfig,
    adjustment: str,
    lookback_buffer_days: int,
    lookback_max_days: int,
    account_id: str,
    count: int,
    block_ms: int,
    retry_delay_seconds: float = 0.0,
    produced_by: str = PRODUCED_BY,
) -> int:
    """Process one batch of market-phase events; returns signals emitted."""

    try:
        events = await subscriber.read(
            streams=[STREAM_MARKET_PHASE], count=count, block_ms=block_ms
        )
    except Exception:
        log.exception("strategy_runner.read_failed", group=subscriber.group)
        await asyncio.sleep(retry_delay_seconds)
        return 0

    emitted = 0
    for event in events:
        try:
            payload = event.envelope.payload
            if payload is None:
                log.warning(
                    "strategy_runner.phase_skipped",
                    reason="no payload",
                    phase_event_id=event.envelope.event_id,
                )
                await subscriber.ack(event)
                continue
            phase = str(payload.get("phase", ""))
            if phase != Phase.OPEN:
                # Only entry into `open` triggers a pass; ack every other phase.
                await subscriber.ack(event)
                continue
            session_date = _parse_session_date(payload)
            emitted += await run_pass(
                session_date=session_date,
                redis=redis,
                registry=registry,
                reader=reader,
                state_store=state_store,
                config=config,
                adjustment=adjustment,
                lookback_buffer_days=lookback_buffer_days,
                lookback_max_days=lookback_max_days,
                account_id=account_id,
                produced_by=produced_by,
            )
            await subscriber.ack(event)
            log.info(
                "strategy_runner.pass_complete",
                session_date=session_date.isoformat(),
                emitted=emitted,
                phase_event_id=event.envelope.event_id,
            )
        except SizingRefused as exc:
            # Unknown or stale account equity. Deliberately NOT acked: this is a
            # transient dependency failure (the Reconciliation Agent writes the
            # snapshot), so the phase event stays pending and the whole pass
            # retries next cycle. Trading on an unknown account size is worse
            # than trading late.
            log.error(
                "strategy_runner.sizing_refused",
                reason=str(exc),
                phase_event_id=event.envelope.event_id,
            )
            await asyncio.sleep(retry_delay_seconds)
            break
        except ValueError:
            # Malformed phase payload: permanent for this event. Ack and skip,
            # or the consumer stalls forever on a poison message.
            log.exception(
                "strategy_runner.phase_invalid_skipped",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                phase_event_id=event.envelope.event_id,
            )
            await subscriber.ack(event)
            continue
        except Exception:
            # Systemic error: no ack, so the phase event is redelivered next
            # cycle and the pass retries. The session-date guard makes it safe.
            log.exception(
                "strategy_runner.pass_failed",
                stream=event.stream,
                redis_stream_id=event.redis_stream_id,
                phase_event_id=event.envelope.event_id,
            )
            await asyncio.sleep(retry_delay_seconds)
            break
    return emitted


async def run_loop(
    redis: RedisStreamClient,
    *,
    registry: Registry,
    reader: BarReader,
    state_store: StateStore,
    config: RunnerSignalConfig,
    stop: asyncio.Event,
    adjustment: str,
    lookback_buffer_days: int,
    lookback_max_days: int,
    account_id: str,
    start_id: str = "$",
    count: int = 100,
    block_ms: int = 5000,
    retry_delay_seconds: float = 1.0,
    group: str = CONSUMER_GROUP,
    consumer: str | None = None,
) -> None:
    """Run the runner consumer loop until ``stop`` is set."""

    subscriber = GroupEventSubscriber(
        cast(RedisGroupClient, redis),
        group=group,
        consumer=consumer,
        start_id=start_id,
    )
    while not stop.is_set():
        try:
            emitted = await poll_once(
                redis,
                subscriber,
                registry=registry,
                reader=reader,
                state_store=state_store,
                config=config,
                adjustment=adjustment,
                lookback_buffer_days=lookback_buffer_days,
                lookback_max_days=lookback_max_days,
                account_id=account_id,
                count=count,
                block_ms=block_ms,
                retry_delay_seconds=retry_delay_seconds,
            )
            if emitted:
                log.info("strategy_runner.batch", emitted=emitted, group=group)
            else:
                await asyncio.sleep(0)
        except Exception:
            log.exception("strategy_runner.poll_failed")
            await asyncio.sleep(retry_delay_seconds)


async def run(
    redis_url: str,
    postgres_dsn: str,
    config: RunnerSignalConfig,
    *,
    service_name: str = CONSUMER_GROUP,
    log_level: str = "INFO",
    adjustment: str = "all",
    lookback_buffer_days: int = 10,
    lookback_max_days: int = 1200,
    account_id: str = "",
    start_id: str = "$",
    count: int = 100,
    block_ms: int = 5000,
    retry_delay_seconds: float = 1.0,
    group: str = CONSUMER_GROUP,
    consumer: str | None = None,
) -> None:
    """Run the Strategy Runner service until SIGINT/SIGTERM."""

    configure_logging(service_name, log_level)
    log.info(
        "strategy_runner.starting",
        redis_url=redis_url,
        postgres_dsn="***",
        start_id=start_id,
        group=group,
        consumer=consumer or group,
        confidence=config.confidence,
        max_quantity=config.max_quantity,
        adjustment=adjustment,
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
    reader = PostgresEvaluatorReader(pool)
    state_store = PostgresStrategyRunnerStateStore(pool)
    await state_store.ensure_schema()
    try:
        await run_loop(
            cast(RedisStreamClient, redis),
            registry=cast(Registry, registry),
            reader=cast(BarReader, reader),
            state_store=cast(StateStore, state_store),
            config=config,
            stop=stop,
            adjustment=adjustment,
            lookback_buffer_days=lookback_buffer_days,
            lookback_max_days=lookback_max_days,
            account_id=account_id,
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
        log.info("strategy_runner.stopped")


__all__ = [
    "ACTIVE_PAPER_STAGES",
    "CONSUMER_GROUP",
    "STREAM_MARKET_PHASE",
    "poll_once",
    "run",
    "run_loop",
    "run_pass",
]
