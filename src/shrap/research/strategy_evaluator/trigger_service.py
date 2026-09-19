"""Strategy Evaluator trigger: the sweep that makes evaluation happen without Mike.

The Evaluator has always been correct and never been *automatic*. It shipped as
a tools-profile, run-to-completion CLI, so every verdict the firm has ever
produced required a human to type a command. That is the gap between "we did it
once by hand" and "the Research Department is functional" — ADR-0013's
sequencing item 2.

This service closes it with the smallest thing that actually works: a periodic
sweep of ``hypothesis``-stage strategies, each evaluated through the same
:class:`~shrap.research.strategy_evaluator.pipeline.EvaluationPipeline` the CLI
uses. There is no separate code path and no separate protocol, so an automated
verdict and a hand-run one are the same verdict.

**Kills apply, promotes wait (ADR-0015).** Every commit passes
``promote_requires_review=True``. A kill or a hold is applied unattended; a
promote is fully recorded — card, evaluation row, a
``research.strategy.promotion-pending`` event — but the registry transition is
withheld, so the strategy stays at ``hypothesis`` and never reaches the Strategy
Runner's trading path on its own. The asymmetry is the vision's ("kill more
aggressively than you promote") applied to autonomy rather than to thresholds.

**What this deliberately is not.** The spec
(``docs/agents/research/strategy-evaluator.md``) describes a 19:30 ET overnight
*queue* runner with three event triggers. All three named event producers —
``research.hypothesis.proposed``, ``research.strategy.refit.request``, and the
thesis-broken family — do not exist yet; a grep of ``src/shrap/`` finds zero
publishers. Building a queue and three subscriptions for events nothing emits
would be scaffolding around a hole. The one relevant stream that *does* have a
producer is ``research.strategy.registered``, which the spec does not mention;
subscribing to it would only reduce latency from one sweep interval to seconds,
so it is deferred rather than absent by oversight. The spec records both.

Re-evaluation floor: a strategy is skipped while an evaluation of the same
``(strategy_id, spec_hash, protocol_version)`` exists inside
``reeval_interval_hours``. Without it the sweep would re-evaluate every
``hold-for-data`` and every held promote on every pass, writing a duplicate
ledger row and re-publishing the pending event each time. A changed spec or a
bumped protocol resets the floor immediately, because that is a different
question.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Protocol, cast

import structlog
from redis.asyncio import Redis

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.events import EventPublisher, RedisPublisher
from shrap.research.strategy_evaluator.engine import PROTOCOL_VERSION, EvalConfig
from shrap.research.strategy_evaluator.pipeline import (
    DEFAULT_CARD_ROOT,
    CommitResult,
    EvaluationOutcome,
    EvaluationPipeline,
    SpecHygieneError,
)
from shrap.research.strategy_evaluator.store import (
    IntradayEvaluatorReader,
    PostgresEvaluationStore,
    PostgresEvaluatorReader,
)
from shrap.research.strategy_registry import (
    STATUS_HYPOTHESIS,
    PostgresStrategyRegistry,
    StrategyRecord,
)
from shrap.research.strategy_runner.cadence import (
    Cadence,
    alpaca_timeframe,
    read_cadence,
)

log = structlog.get_logger(__name__)

PRODUCED_BY = "strategy-evaluator-trigger"
TRIGGER_NAME = "scheduled-sweep"

DEFAULT_SWEEP_INTERVAL_SECONDS = 900.0
DEFAULT_REEVAL_INTERVAL_HOURS = 24.0

# How much history an intraday evaluation reads. Mandatory below a daily grain:
# the CLI refuses an unbounded intraday lookback because one ticker-year is ~252
# daily rows but ~6,500 at 15Min, and an unattended sweep has no human to ask.
# Six years matches the 15Min depth backfilled on 2026-09-18 (2021-01-04 ->
# present, 37,116 bars). Raise it only after backfilling deeper, or the window
# silently exceeds the data and the panel is shorter than the verdict claims.
DEFAULT_INTRADAY_WINDOW_YEARS = 6


class Disposition(Enum):
    """What one sweep did with one strategy. Exhaustive and mutually exclusive."""

    SKIPPED_RECENT = "skipped-recent"
    REFUSED = "refused"
    FAILED = "failed"
    TRANSITIONED = "transitioned"
    HELD_FOR_REVIEW = "held-for-review"
    RECORDED = "recorded"
    """Evaluated and persisted with no stage change — a ``hold-for-data``."""


@dataclass(frozen=True, slots=True)
class SweepResult:
    """What one sweep did. Every candidate lands in exactly one bucket."""

    candidates: int = 0
    transitioned: int = 0
    held_for_review: int = 0
    recorded: int = 0
    skipped_recent: int = 0
    refused: int = 0
    failed: int = 0
    refusals_reported: int = 0
    """Refusals whose reason changed since the last sweep, so they were logged.
    Lower than ``refused`` whenever a strategy is stuck on the same complaint —
    this is the count worth alerting on, since ``refused`` alone is flat."""
    strategy_ids: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"candidates={self.candidates} transitioned={self.transitioned} "
            f"held_for_review={self.held_for_review} recorded={self.recorded} "
            f"skipped_recent={self.skipped_recent} refused={self.refused} "
            f"failed={self.failed}"
        )


class RegistryPort(Protocol):
    async def list_by_status(self, status: str) -> list[StrategyRecord]: ...


class EvaluationLedgerPort(Protocol):
    async def latest_evaluation_at(
        self, strategy_id: str, spec_hash: str, protocol_version: str
    ) -> datetime | None: ...


class PipelinePort(Protocol):
    async def evaluate(self, strategy_id: str, *, trigger: str = ...) -> EvaluationOutcome: ...

    async def commit(
        self, outcome: EvaluationOutcome, *, promote_requires_review: bool = ...
    ) -> CommitResult: ...


class PipelineResolver(Protocol):
    """Picks the pipeline whose bar grain matches a strategy's declared cadence."""

    def for_cadence(self, cadence: Cadence) -> PipelinePort: ...


@dataclass(frozen=True, slots=True)
class SingleGrainPipelines:
    """Every cadence evaluates on the same panel.

    The behaviour this service had before cadence existed, named rather than
    implied, so "this ignores cadence" is a statement in the code. Used by tests
    and by any caller that genuinely has one grain.
    """

    pipeline: PipelinePort

    def for_cadence(self, cadence: Cadence) -> PipelinePort:
        return self.pipeline


class CadencePipelines:
    """One evaluation pipeline per bar grain, chosen by a strategy's cadence.

    The daily pipeline is built eagerly because everything in the registry today
    needs it; intraday pipelines are built on first use and kept, keyed by the
    Alpaca timeframe token ``alpaca_timeframe`` derives. Same shape as the
    Runner's ``CadenceBarReaders`` (#234), for the same reason: the cadence is
    read once from the spec and used everywhere, rather than each component
    deciding for itself what grain a strategy trades.

    **``window_years`` is mandatory below a daily grain and that is deliberate.**
    The CLI refuses an unbounded intraday lookback outright, because one
    ticker-year is ~252 daily rows but ~6,500 at 15Min and ~98,000 at 1Min, and
    a silently truncated window produces a verdict on a different panel than the
    caller believes they asked for. An unattended sweep cannot ask a human, so
    it carries a configured window instead — the depth actually backfilled.

    Everything else in ``EvalConfig`` stays at its defaults. ``min_trades`` and
    ``sharpe_floor`` are the protocol rather than a per-deployment knob, and
    exposing them here would make "lower the gate until something passes" a
    config change.
    """

    def __init__(
        self,
        *,
        pool: object,
        registry: object,
        store: object,
        publisher: object,
        card_root: Path,
        intraday_window_years: int,
        include_extended: bool = False,
    ) -> None:
        self._pool = pool
        self._registry = registry
        self._store = store
        self._publisher = publisher
        self._card_root = card_root
        self._intraday_window_years = intraday_window_years
        self._include_extended = include_extended
        self._daily: PipelinePort = self._build(PostgresEvaluatorReader(pool), EvalConfig())  # type: ignore[arg-type]
        self._intraday: dict[str, PipelinePort] = {}

    def _build(self, reader: object, config: EvalConfig) -> PipelinePort:
        return cast(
            PipelinePort,
            EvaluationPipeline(
                registry=self._registry,  # type: ignore[arg-type]
                reader=reader,  # type: ignore[arg-type]
                store=self._store,  # type: ignore[arg-type]
                publisher=self._publisher,  # type: ignore[arg-type]
                config=config,
                card_root=self._card_root,
            ),
        )

    def for_cadence(self, cadence: Cadence) -> PipelinePort:
        if not cadence.is_intraday:
            return self._daily
        timeframe = alpaca_timeframe(cadence)
        pipeline = self._intraday.get(timeframe)
        if pipeline is None:
            pipeline = self._build(
                IntradayEvaluatorReader(
                    self._pool,  # type: ignore[arg-type]
                    timeframe=timeframe,
                    include_extended=self._include_extended,
                ),
                EvalConfig(window_years=self._intraday_window_years),
            )
            self._intraday[timeframe] = pipeline
        return pipeline


class EvaluatorTrigger:
    """One sweep's worth of policy, kept separate from the process loop."""

    def __init__(
        self,
        *,
        registry: RegistryPort,
        ledger: EvaluationLedgerPort,
        pipeline: PipelinePort | None = None,
        pipelines: PipelineResolver | None = None,
        reeval_interval_hours: float = DEFAULT_REEVAL_INTERVAL_HOURS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if pipelines is None:
            if pipeline is None:
                raise ValueError("EvaluatorTrigger needs a pipeline or a pipeline resolver")
            pipelines = SingleGrainPipelines(pipeline)
        self._registry = registry
        self._ledger = ledger
        self._pipelines = pipelines
        self._reeval_interval = timedelta(hours=reeval_interval_hours)
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        # A refusal is re-attempted every sweep on purpose: `_check_tickers_tradeable`
        # reads research.universe_tiers, so a Curator change can make a refused
        # strategy evaluable without its spec changing. Only the *logging* is
        # suppressed while the reason is unchanged, so a permanently-unevaluable
        # strategy costs one line per process rather than one per sweep.
        self._last_refusal: dict[str, str] = {}

    async def sweep_once(self) -> SweepResult:
        """Evaluate every due ``hypothesis``-stage strategy. Never raises."""

        try:
            records = await self._registry.list_by_status(STATUS_HYPOTHESIS)
        except Exception:
            # The registry being unreachable is a systemic fault, not a verdict.
            # Report it and let the next sweep retry; do not crash the service.
            log.exception("strategy_evaluator_trigger.list_failed")
            return SweepResult(failed=1)

        counts: dict[Disposition, int] = dict.fromkeys(Disposition, 0)
        touched: list[str] = []
        reported = 0
        for record in records:
            disposition, newly_reported = await self._evaluate_one(record)
            counts[disposition] += 1
            reported += int(newly_reported)
            if disposition in _EVALUATED:
                touched.append(record.strategy_id)

        result = SweepResult(
            candidates=len(records),
            transitioned=counts[Disposition.TRANSITIONED],
            held_for_review=counts[Disposition.HELD_FOR_REVIEW],
            recorded=counts[Disposition.RECORDED],
            skipped_recent=counts[Disposition.SKIPPED_RECENT],
            refused=counts[Disposition.REFUSED],
            failed=counts[Disposition.FAILED],
            refusals_reported=reported,
            strategy_ids=touched,
        )
        log.info("strategy_evaluator_trigger.sweep_complete", **_log_fields(result))
        return result

    async def _evaluate_one(self, record: StrategyRecord) -> tuple[Disposition, bool]:
        """Returns the bucket, and whether a refusal was newly reported."""

        strategy_id = record.strategy_id
        try:
            if await self._recently_evaluated(record):
                return Disposition.SKIPPED_RECENT, False
            # The strategy's OWN declared cadence picks the panel it is judged
            # on. Before this the sweep evaluated everything on daily bars, and
            # an intraday strategy's horizons are counted in BARS — so a
            # 15-minute rule with a six-month formation asked for 3,276 daily
            # sessions, thirteen years against a 5.1-year panel, and came back
            # `insufficient-data` with zero periods. Measured on
            # 01M2V2ZG57AY3773KR3HJ8WZHN, 2026-09-18: the sweep auto-killed it
            # within the hour it was seeded, on a grain it never claimed.
            pipeline = self._pipelines.for_cadence(read_cadence(record.spec))
            outcome = await pipeline.evaluate(strategy_id, trigger=TRIGGER_NAME)
            result = await pipeline.commit(outcome, promote_requires_review=True)
        except SpecHygieneError as exc:
            # Not a kill. A spec we refused to evaluate has not earned a terminal
            # verdict, so nothing is written and the strategy stays at hypothesis.
            return Disposition.REFUSED, self._log_refusal(strategy_id, str(exc))
        except Exception:
            # One bad strategy must not stall the sweep for the others.
            log.exception("strategy_evaluator_trigger.evaluation_failed", strategy_id=strategy_id)
            return Disposition.FAILED, False

        self._last_refusal.pop(strategy_id, None)
        log.info(
            "strategy_evaluator_trigger.evaluated",
            strategy_id=strategy_id,
            verdict=outcome.verdict,
            reason=outcome.reason,
            to_stage=result.to_stage,
            transitioned=result.transitioned,
            promotion_held=result.promotion_held,
            evaluation_id=result.evaluation_id,
        )
        if result.promotion_held:
            # The one line an operator must not miss: a strategy cleared every
            # gate and is waiting on a human. Warning, not info.
            log.warning(
                "strategy_evaluator_trigger.promotion_pending_review",
                strategy_id=strategy_id,
                recommended_stage=result.to_stage,
                evaluation_id=result.evaluation_id,
                review_command=f"shrap-strategy-evaluate --strategy-id {strategy_id}",
            )
            return Disposition.HELD_FOR_REVIEW, False
        if result.transitioned:
            return Disposition.TRANSITIONED, False
        return Disposition.RECORDED, False

    def _log_refusal(self, strategy_id: str, message: str) -> bool:
        """Log an unseen refusal; return whether it was new."""

        if self._last_refusal.get(strategy_id) == message:
            return False
        log.info("strategy_evaluator_trigger.refused", strategy_id=strategy_id, detail=message)
        self._last_refusal[strategy_id] = message
        return True

    async def _recently_evaluated(self, record: StrategyRecord) -> bool:
        latest = await self._ledger.latest_evaluation_at(
            record.strategy_id, record.spec_hash, PROTOCOL_VERSION
        )
        if latest is None:
            return False
        if latest.tzinfo is None:
            # asyncpg returns naive datetimes for a TIMESTAMP column and aware
            # ones for TIMESTAMPTZ. The column is TIMESTAMPTZ, but assuming that
            # here would make a schema change silently produce a TypeError on
            # subtraction rather than a wrong-but-obvious answer.
            latest = latest.replace(tzinfo=UTC)
        return self._clock() - latest < self._reeval_interval


_EVALUATED = frozenset(
    {Disposition.TRANSITIONED, Disposition.HELD_FOR_REVIEW, Disposition.RECORDED}
)


def _log_fields(result: SweepResult) -> dict[str, int]:
    return {
        "candidates": result.candidates,
        "transitioned": result.transitioned,
        "held_for_review": result.held_for_review,
        "recorded": result.recorded,
        "skipped_recent": result.skipped_recent,
        "refused": result.refused,
        "failed": result.failed,
        "refusals_reported": result.refusals_reported,
    }


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)


async def run(
    *,
    redis_url: str,
    postgres_dsn: str,
    service_name: str = PRODUCED_BY,
    log_level: str = "INFO",
    card_root: str = str(DEFAULT_CARD_ROOT),
    sweep_interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
    reeval_interval_hours: float = DEFAULT_REEVAL_INTERVAL_HOURS,
    intraday_window_years: int = DEFAULT_INTRADAY_WINDOW_YEARS,
) -> None:
    """Run the sweep loop until SIGTERM/SIGINT."""

    configure_logging(service_name, log_level)
    redis: Redis = Redis.from_url(redis_url, decode_responses=True)
    pool = await create_asyncpg_pool(postgres_dsn)
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    try:
        registry = PostgresStrategyRegistry(pool)
        store = PostgresEvaluationStore(pool)
        await registry.ensure_schema()
        await store.ensure_schema()
        # One pipeline per grain, resolved from each strategy's own cadence.
        # EvalConfig defaults deliberately: min_trades and sharpe_floor are the
        # protocol, not a per-deployment knob. Exposing them as env vars would
        # make "lower the gate until something passes" a config change.
        pipelines = CadencePipelines(
            pool=pool,
            registry=registry,
            store=store,
            publisher=EventPublisher(cast(RedisPublisher, redis)),
            card_root=Path(card_root),
            intraday_window_years=intraday_window_years,
        )
        trigger = EvaluatorTrigger(
            registry=registry,
            ledger=store,
            pipelines=pipelines,
            reeval_interval_hours=reeval_interval_hours,
        )
        log.info(
            "strategy_evaluator_trigger.starting",
            sweep_interval_seconds=sweep_interval_seconds,
            reeval_interval_hours=reeval_interval_hours,
            card_root=card_root,
            protocol_version=PROTOCOL_VERSION,
        )
        while not stop.is_set():
            await trigger.sweep_once()
            # wait_for on the stop event rather than sleep(): SIGTERM during the
            # idle window shuts down immediately instead of after a full interval.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=sweep_interval_seconds)
    finally:
        await redis.aclose()
        await pool.close()


__all__ = [
    "DEFAULT_REEVAL_INTERVAL_HOURS",
    "DEFAULT_SWEEP_INTERVAL_SECONDS",
    "PRODUCED_BY",
    "TRIGGER_NAME",
    "Disposition",
    "EvaluatorTrigger",
    "SweepResult",
    "run",
]
