"""Paper-strategy runner service loop.

Long-running consumer of ``operations.market-phase``. It runs an evaluation
pass: for every active *paper-stage* strategy in the registry it reads a
trailing daily-bar window, computes the flat/invested target through the reused
Strategy Evaluator factory seam, and emits a ``trading.strategy.signal`` for
each target *transition* (flat -> invested = buy, invested -> flat = sell). It
emits **signals only** — the Decision Maker -> Pre-Trade Checker -> Execution
chain owns everything downstream. PAPER ONLY: no intents, no broker calls, no
real money.

**Two things trigger a pass** (timeline 2.9). Entry into phase ``open``, as
before — and, while that session remains open, a timer every
``intraday_tick_seconds``. market-phase publishes transitions rather than ticks,
so :class:`SessionTracker` holds the "we are inside open" state between events
and no second scheduler is needed.

The timer offers an *opportunity*, never a trade rate. Whether a strategy acts
is decided by its own declared cadence
(:mod:`shrap.research.strategy_runner.cadence`), and **a strategy with no
declared cadence acts once per session no matter how often the loop wakes**.
That default is what makes interval firing safe to switch on with strategies
already in the registry.

Delivery / idempotency (KI-006 consumer group + a per-session-and-slot guard):

- Offsets live in the ``strategy-runner`` consumer group, so restarts resume
  where the group left off. ``start_id`` defaults to ``"$"`` (new events only);
  a market-phase event published while the runner was down is not replayed.
- The pass is idempotent on ``(strategy_id, session_date, slot)``: a strategy
  already stamped for its current slot is skipped by the pure planner, so a
  re-delivered ``open`` event, a ``startup``/catch-up event, an interval tick,
  or a restart mid-session never double-emits. We do *not* gate on ``reason``;
  the state guard is the guard.
- The guard is applied *before* bars are read. Under a one-minute tick the pass
  runs hundreds of times a session and is a no-op for nearly all of them;
  reading a trailing window per ticker per tick to compute a skip already
  decided is the difference between "cheap" and "quietly expensive".
- Poison discipline: a malformed phase payload (bad ``session_date``) is acked
  and skipped; a systemic error (DB/Redis down) is *not* acked, so the event
  stays pending and the pass is retried in full next cycle.

Fail-safe: a single bad strategy (missing bars, bad spec, factory error) is
skipped by the planner with a logged reason; it never crashes the loop and
never emits a partial signal.

Sizing: each strategy's target weight becomes a share count against its own
account's equity, read from ``ops.account_snapshots`` (written by the
Reconciliation Agent). There is no fixed-quantity fallback — an account whose
equity cannot be established simply does not trade this pass.

The account comes from the **strategy**, not from config. ADR-0017 gives each
strategy its own broker account, so the pass groups strategies by account and
sizes each group against that account's equity. The exposure budget is therefore
per-account too: with one strategy per account it gets the whole book.

Accounts are independent. A stale snapshot on one account defers only its own
strategies — the others still trade — and the phase event stays un-acked so the
deferred ones get another chance this session. A strategy with *no* account is
dropped rather than deferred: no snapshot will ever arrive for it, so retrying
would be a poison loop. It needs `shrap-strategy-stage assign-account`.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Sequence
from dataclasses import dataclass
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
from shrap.research.strategy_runner.cadence import read_cadence, slot_for
from shrap.research.strategy_runner.engine import (
    PRODUCED_BY,
    SCHEMA_VERSION,
    STREAM_STRATEGY_SIGNAL,
    PlannedStateWrite,
    RunnerSignalConfig,
    StrategyInput,
    TargetState,
    already_ran,
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


@dataclass
class SessionTracker:
    """Which session, if any, the market is currently ``open`` for.

    The Runner's only clock is ``operations.market-phase``, and that stream
    publishes *transitions*, not ticks — it sleeps until the next boundary. So
    acting more than once a session means remembering, between events, that we
    are inside ``open``. This is that memory, and it is the whole reason the
    loop can fire on an interval without a second scheduler.

    Mutable and owned by :func:`run_loop`, deliberately outside :func:`poll_once`
    so the state survives a batch that returned no events — which is the normal
    case, because a session has two boundaries and hundreds of ticks.

    A restart mid-session clears it, and the next phase event restores it. The
    cost of that gap is bounded: slots are floored wall-clock, so a Runner that
    comes back at 14:22 resolves the same slot it would have had it never left,
    and the state guard still refuses a second action in that slot.
    """

    open_session: date | None = None

    def observe(self, phase: str, session_date: date | None) -> None:
        """Fold in one market-phase event."""

        if phase == Phase.OPEN:
            self.open_session = session_date
        else:
            # Any other phase ends the session for our purposes. Explicitly
            # including `closed`, `pre`, `post` and anything added later: the
            # safe reading of an unrecognised phase is "not open".
            self.open_session = None


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


@dataclass(frozen=True, slots=True)
class PassResult:
    """What one pass did, and whether it is finished.

    ``deferred`` names strategies skipped for a *retryable* reason — their
    account has no usable equity snapshot right now. The caller leaves the phase
    event un-acked when this is non-empty, so the pass runs again and they get
    another chance this session. Strategies that already traded are stamped, so
    the retry does not re-emit for them (the ``(strategy_id, session_date)``
    guard).

    Unassigned strategies are **not** deferred. No snapshot will ever arrive for
    a strategy with no account; that needs a human, so retrying forever would be
    a poison loop.
    """

    emitted: int
    deferred: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        return not self.deferred


def _group_by_account(records: Sequence[StrategyRecord]) -> dict[str, list[StrategyRecord]]:
    """Partition strategies by the account they trade. Unassigned excluded."""

    grouped: dict[str, list[StrategyRecord]] = {}
    for record in records:
        if record.account_id:
            grouped.setdefault(record.account_id, []).append(record)
    return grouped


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
    produced_by: str = PRODUCED_BY,
) -> PassResult:
    """Run one evaluation pass for ``session_date``, account by account.

    Each strategy is sized against **its own** broker account (ADR-0017), so the
    accounts are independent: a stale snapshot on one does not stop the others
    trading, and the exposure budget is per-account rather than firm-wide.

    Systemic errors (registry/bars/state/publish) propagate so the caller can
    leave the market-phase event un-acked and retry the whole pass next cycle.
    """

    records = await _active_paper_strategies(registry)
    if not records:
        log.info("strategy_runner.no_active_strategies", session_date=session_date.isoformat())
        return PassResult(emitted=0)

    unassigned = [r for r in records if not r.account_id]
    for record in unassigned:
        # Permanent until a human acts, so this is logged and dropped rather than
        # deferred — there is no book to send its orders to, and no snapshot will
        # ever arrive to change that.
        log.error(
            "strategy_runner.strategy_unassigned",
            strategy_id=record.strategy_id,
            name=record.name,
            reason=(
                "no account_id — assign one with `shrap-strategy-stage "
                "assign-account`, or it will never trade"
            ),
            session_date=session_date.isoformat(),
        )

    stored_state = await state_store.read_state()
    now = datetime.now(UTC)

    # Apply the idempotency guard BEFORE reading any bars. Under intraday
    # cadence this pass runs every tick of the session, and all but a handful
    # of those ticks are no-ops for every strategy: a daily rule is stamped for
    # the whole session after its first pass, and an intraday one between its
    # intervals. Letting those reach `_build_input` would read a trailing bar
    # window per ticker per tick — hundreds of times a session — to compute a
    # skip the planner had already decided. The planner still re-checks; this
    # only avoids paying for the answer twice.
    due = [
        record
        for record in records
        if not already_ran(
            record.strategy_id,
            _extract_tickers(record.tickers),
            stored_state,
            session_date,
            slot_for(read_cadence(record.spec), now),
        )
    ]
    if not due:
        return PassResult(emitted=0)

    grouped = _group_by_account(due)
    if not grouped:
        return PassResult(emitted=0)

    regime_label = await latest_regime_label(cast(FixtureRedis, redis))  # informational only
    publisher = EventPublisher(cast(RedisPublisher, redis))

    emitted = 0
    deferred: list[str] = []
    for account_id, account_records in sorted(grouped.items()):
        raw_equity, observed_at = await state_store.latest_equity(account_id)
        try:
            equity = assert_equity_usable(raw_equity, observed_at, now)
        except SizingRefused as exc:
            # Retryable and isolated: this account cannot be sized right now, but
            # the others still can. Deferring rather than raising is what keeps
            # one stale snapshot from halting the whole firm.
            log.error(
                "strategy_runner.account_equity_unusable",
                account_id=account_id,
                reason=str(exc),
                strategies=[r.strategy_id for r in account_records],
                session_date=session_date.isoformat(),
            )
            deferred.extend(r.strategy_id for r in account_records)
            continue

        inputs = [
            await _build_input(
                record,
                reader,
                session_date,
                adjustment=adjustment,
                buffer_days=lookback_buffer_days,
                max_days=lookback_max_days,
            )
            for record in account_records
        ]

        # One plan_session per account, so the exposure budget divides this
        # account's equity among the strategies in this account only. With one
        # strategy per account (ADR-0017) that gives it the whole book.
        plans = plan_session(
            session_date=session_date,
            now=now,
            strategies=inputs,
            stored_state=stored_state,
            factory=_default_strategy_factory,
            config=config,
            regime_label=regime_label,
            equity=equity,
            account_id=account_id,
        )

        for plan in plans:
            if plan.skipped:
                log.info(
                    "strategy_runner.strategy_skipped",
                    strategy_id=plan.strategy_id,
                    account_id=account_id,
                    reason=plan.skip_reason,
                    session_date=session_date.isoformat(),
                )
                continue
            # A clamped or unfundable entry means the live book is not the
            # evaluated book for that name. Silence here is the failure mode
            # sizing exists to remove, so it is logged even though nothing broke.
            for note in plan.sizing_notes:
                log.warning(
                    "strategy_runner.sizing_note",
                    strategy_id=plan.strategy_id,
                    account_id=account_id,
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
                    account_id=account_id,
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

    return PassResult(emitted=emitted, deferred=tuple(deferred))


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
    count: int,
    block_ms: int,
    tracker: SessionTracker | None = None,
    retry_delay_seconds: float = 0.0,
    produced_by: str = PRODUCED_BY,
) -> int:
    """Process one batch of market-phase events; returns signals emitted.

    ``tracker``, when given, is updated with every phase observed so the caller
    knows whether the market is currently open. Optional so the existing
    event-driven tests construct this unchanged.
    """

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
                # Not open: record that fact so the interval firing stops, then
                # ack. Every non-open phase is otherwise uninteresting here.
                if tracker is not None:
                    tracker.observe(phase, None)
                await subscriber.ack(event)
                continue
            session_date = _parse_session_date(payload)
            if tracker is not None:
                tracker.observe(phase, session_date)
            result = await run_pass(
                session_date=session_date,
                redis=redis,
                registry=registry,
                reader=reader,
                state_store=state_store,
                config=config,
                adjustment=adjustment,
                lookback_buffer_days=lookback_buffer_days,
                lookback_max_days=lookback_max_days,
                produced_by=produced_by,
            )
            emitted += result.emitted
            if not result.is_complete:
                # Some account could not be sized. Leave the phase event pending
                # so those strategies get another chance this session; the ones
                # that already traded are stamped and will not re-emit. Break
                # rather than continue: the next event is a later phase and
                # acking it would advance past this one.
                log.warning(
                    "strategy_runner.pass_deferred",
                    session_date=session_date.isoformat(),
                    emitted=result.emitted,
                    deferred=list(result.deferred),
                    phase_event_id=event.envelope.event_id,
                )
                await asyncio.sleep(retry_delay_seconds)
                break
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
    start_id: str = "$",
    count: int = 100,
    block_ms: int = 5000,
    intraday_tick_seconds: float = 60.0,
    retry_delay_seconds: float = 1.0,
    group: str = CONSUMER_GROUP,
    consumer: str | None = None,
) -> None:
    """Run the runner consumer loop until ``stop`` is set.

    Two things wake a pass. A market-phase ``open`` event, as before — and,
    while that session stays open, a timer every ``intraday_tick_seconds``.

    The timer does not decide *whether* anything trades; it only offers the
    opportunity. Each strategy's own cadence resolves its slot and the state
    guard does the rest, so a tick with nothing due costs one registry read and
    one state read and emits nothing. Setting ``intraday_tick_seconds`` to 0
    disables interval firing entirely and restores the pure event-driven
    behaviour.
    """

    subscriber = GroupEventSubscriber(
        cast(RedisGroupClient, redis),
        group=group,
        consumer=consumer,
        start_id=start_id,
    )
    tracker = SessionTracker()
    last_tick = 0.0
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
                count=count,
                block_ms=block_ms,
                tracker=tracker,
                retry_delay_seconds=retry_delay_seconds,
            )
            now = asyncio.get_running_loop().time()
            session = tracker.open_session
            if (
                intraday_tick_seconds > 0
                and session is not None
                and now - last_tick >= intraday_tick_seconds
            ):
                last_tick = now
                # No event backs this pass, so there is nothing to ack or leave
                # pending: a deferred account simply gets another chance on the
                # next tick, which is the same retry the phase-event path buys
                # by withholding an ack.
                result = await run_pass(
                    session_date=session,
                    redis=redis,
                    registry=registry,
                    reader=reader,
                    state_store=state_store,
                    config=config,
                    adjustment=adjustment,
                    lookback_buffer_days=lookback_buffer_days,
                    lookback_max_days=lookback_max_days,
                )
                emitted += result.emitted
                if result.emitted or result.deferred:
                    log.info(
                        "strategy_runner.intraday_tick",
                        session_date=session.isoformat(),
                        emitted=result.emitted,
                        deferred=list(result.deferred),
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
    start_id: str = "$",
    count: int = 100,
    block_ms: int = 5000,
    intraday_tick_seconds: float = 60.0,
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
            start_id=start_id,
            count=count,
            block_ms=block_ms,
            intraday_tick_seconds=intraday_tick_seconds,
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
    "PassResult",
    "poll_once",
    "run",
    "run_loop",
    "run_pass",
]
