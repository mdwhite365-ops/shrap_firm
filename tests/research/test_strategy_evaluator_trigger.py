"""Trigger tests: the autonomy boundary, the re-evaluation floor, and isolation.

The load-bearing assertion in this file is that an unattended ``promote`` does
**not** transition the registry (ADR-0015). Everything else guards the sweep
against turning one bad strategy into a stalled Research Department.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from shrap.research.strategy_evaluator.engine import PROTOCOL_VERSION
from shrap.research.strategy_evaluator.pipeline import (
    STREAM_STRATEGY_PROMOTION_PENDING,
    STREAM_STRATEGY_VERDICT,
    CommitResult,
    SpecHygieneError,
)
from shrap.research.strategy_evaluator.trigger_service import (
    TRIGGER_NAME,
    Disposition,
    EvaluatorTrigger,
)
from shrap.research.strategy_registry import (
    STATUS_HYPOTHESIS,
    STATUS_KILLED,
    STATUS_PAPER,
    StrategyRecord,
)

_NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)


# --- fakes -------------------------------------------------------------------


def _record(strategy_id: str = "01A", spec_hash: str = "hash-1") -> StrategyRecord:
    return StrategyRecord(
        strategy_id=strategy_id,
        name=f"strategy {strategy_id}",
        version=1,
        archetype="technical-catalyst",
        status=STATUS_HYPOTHESIS,
        source="mike-seed",
        thesis="probe",
        anchor={},
        tickers={"long": ["XLE"], "short": []},
        spec={"params": {"fast": 3}, "param_bounds": {"fast": [2, 100]}},
        spec_hash=spec_hash,
        regime_sizing_modifier=None,
        kill_criteria=["too few trades"],
        code_ref=None,
        created_at=None,
        updated_at=None,
    )


class FakeRegistry:
    def __init__(
        self, records: list[StrategyRecord] | None = None, *, raises: bool = False
    ) -> None:
        self._records = records or []
        self._raises = raises

    async def list_by_status(self, status: str) -> list[StrategyRecord]:
        if self._raises:
            raise RuntimeError("postgres is down")
        assert status == STATUS_HYPOTHESIS
        return list(self._records)


class FakeLedger:
    def __init__(self, latest: dict[tuple[str, str, str], datetime] | None = None) -> None:
        self.latest = latest or {}
        self.queries: list[tuple[str, str, str]] = []

    async def latest_evaluation_at(
        self, strategy_id: str, spec_hash: str, protocol_version: str
    ) -> datetime | None:
        key = (strategy_id, spec_hash, protocol_version)
        self.queries.append(key)
        return self.latest.get(key)


class FakeOutcome:
    def __init__(self, verdict: str, to_stage: str | None) -> None:
        self.verdict = verdict
        self.reason = f"{verdict}-reason"
        self.to_stage = to_stage


class FakePipeline:
    """Mimics the real pipeline's promote-gating contract, not its internals."""

    def __init__(self, verdict: str = "kill", *, evaluate_raises: Exception | None = None) -> None:
        self._verdict = verdict
        self._evaluate_raises = evaluate_raises
        self.evaluated: list[tuple[str, str]] = []
        self.commits: list[tuple[str, bool]] = []

    async def evaluate(self, strategy_id: str, *, trigger: str = "on-demand") -> Any:
        if self._evaluate_raises is not None:
            raise self._evaluate_raises
        self.evaluated.append((strategy_id, trigger))
        to_stage = {"kill": STATUS_KILLED, "promote": STATUS_PAPER}.get(self._verdict)
        return FakeOutcome(self._verdict, to_stage)

    async def commit(self, outcome: Any, *, promote_requires_review: bool = False) -> CommitResult:
        held = promote_requires_review and outcome.verdict == "promote"
        self.commits.append((outcome.verdict, promote_requires_review))
        return CommitResult(
            evaluation_id="01EVAL",
            transitioned=outcome.to_stage is not None and not held,
            to_stage=outcome.to_stage,
            card_path="/cards/x.md",
            streams=[STREAM_STRATEGY_PROMOTION_PENDING if held else STREAM_STRATEGY_VERDICT],
            promotion_held=held,
        )


def _trigger(
    registry: FakeRegistry,
    ledger: FakeLedger,
    pipeline: FakePipeline,
    *,
    reeval_interval_hours: float = 24.0,
) -> EvaluatorTrigger:
    return EvaluatorTrigger(
        registry=registry,  # type: ignore[arg-type]
        ledger=ledger,  # type: ignore[arg-type]
        pipeline=pipeline,  # type: ignore[arg-type]
        reeval_interval_hours=reeval_interval_hours,
        clock=lambda: _NOW,
    )


# --- the autonomy boundary (ADR-0015) ----------------------------------------


async def test_unattended_promote_is_held_and_never_transitions() -> None:
    """The whole reason this service is safe to deploy.

    A promote transitions hypothesis -> paper, and the Strategy Runner emits
    trading signals for paper-stage strategies. An unattended transition would
    put a strategy into paper trading with no human in the loop.
    """

    pipeline = FakePipeline("promote")
    result = await _trigger(FakeRegistry([_record()]), FakeLedger(), pipeline).sweep_once()

    assert result.held_for_review == 1
    assert result.transitioned == 0
    # The gate is requested explicitly on every commit, not inherited by default.
    assert pipeline.commits == [("promote", True)]


async def test_unattended_kill_is_applied() -> None:
    """The asymmetry has to cut one way only, or it is just a switch-off."""

    pipeline = FakePipeline("kill")
    result = await _trigger(FakeRegistry([_record()]), FakeLedger(), pipeline).sweep_once()

    assert result.transitioned == 1
    assert result.held_for_review == 0


async def test_hold_for_data_is_recorded_without_a_transition() -> None:
    pipeline = FakePipeline("hold-for-data")
    result = await _trigger(FakeRegistry([_record()]), FakeLedger(), pipeline).sweep_once()

    assert result.recorded == 1
    assert result.transitioned == 0
    assert result.held_for_review == 0


async def test_sweep_stamps_its_own_trigger_name() -> None:
    """Verdicts must be attributable to the sweep rather than to a person."""

    pipeline = FakePipeline("kill")
    await _trigger(FakeRegistry([_record()]), FakeLedger(), pipeline).sweep_once()
    assert pipeline.evaluated == [("01A", TRIGGER_NAME)]


# --- the re-evaluation floor -------------------------------------------------


async def test_recent_evaluation_is_skipped() -> None:
    """Without this the sweep rewrites the ledger every pass.

    A hold-for-data and a held promote both stay at `hypothesis`, so they are
    candidates forever; re-evaluating each pass would append a duplicate row and
    re-publish the pending event every interval.
    """

    ledger = FakeLedger({("01A", "hash-1", PROTOCOL_VERSION): _NOW - timedelta(hours=1)})
    pipeline = FakePipeline("kill")
    result = await _trigger(FakeRegistry([_record()]), ledger, pipeline).sweep_once()

    assert result.skipped_recent == 1
    assert pipeline.evaluated == []


async def test_evaluation_older_than_the_floor_is_redone() -> None:
    ledger = FakeLedger({("01A", "hash-1", PROTOCOL_VERSION): _NOW - timedelta(hours=25)})
    pipeline = FakePipeline("kill")
    result = await _trigger(FakeRegistry([_record()]), ledger, pipeline).sweep_once()

    assert result.skipped_recent == 0
    assert result.transitioned == 1


async def test_a_changed_spec_hash_resets_the_floor_immediately() -> None:
    """A different spec is a different question, not a repeat of the last one."""

    ledger = FakeLedger({("01A", "old-hash", PROTOCOL_VERSION): _NOW - timedelta(minutes=1)})
    pipeline = FakePipeline("kill")
    result = await _trigger(
        FakeRegistry([_record(spec_hash="new-hash")]), ledger, pipeline
    ).sweep_once()

    assert result.skipped_recent == 0
    assert result.transitioned == 1


async def test_the_floor_is_keyed_on_the_running_protocol_version() -> None:
    """A protocol bump must re-ask, or old verdicts silently outlive their protocol."""

    ledger = FakeLedger({("01A", "hash-1", "0.0.1-ancient"): _NOW})
    pipeline = FakePipeline("kill")
    result = await _trigger(FakeRegistry([_record()]), ledger, pipeline).sweep_once()

    assert result.skipped_recent == 0
    assert ledger.queries == [("01A", "hash-1", PROTOCOL_VERSION)]


async def test_naive_timestamps_from_the_driver_do_not_crash_the_sweep() -> None:
    """asyncpg returns naive datetimes for TIMESTAMP; subtraction would raise."""

    naive = (_NOW - timedelta(hours=1)).replace(tzinfo=None)
    ledger = FakeLedger({("01A", "hash-1", PROTOCOL_VERSION): naive})
    result = await _trigger(FakeRegistry([_record()]), ledger, FakePipeline()).sweep_once()

    assert result.skipped_recent == 1


# --- isolation: one bad strategy must not stall the department ---------------


async def test_a_refusal_is_not_a_kill_and_does_not_stop_the_sweep() -> None:
    """A refused spec has not been evaluated, so it has earned no verdict."""

    pipeline = FakePipeline(evaluate_raises=SpecHygieneError("bottleneck-rotation deferred"))
    result = await _trigger(FakeRegistry([_record()]), FakeLedger(), pipeline).sweep_once()

    assert result.refused == 1
    assert result.transitioned == 0
    assert pipeline.commits == []


async def test_an_unexpected_error_is_contained_to_its_own_strategy() -> None:
    class HalfBrokenPipeline(FakePipeline):
        async def evaluate(self, strategy_id: str, *, trigger: str = "on-demand") -> Any:
            if strategy_id == "01BAD":
                raise RuntimeError("engine exploded")
            return await super().evaluate(strategy_id, trigger=trigger)

    registry = FakeRegistry([_record("01BAD", "h1"), _record("01GOOD", "h2")])
    pipeline = HalfBrokenPipeline("kill")
    result = await _trigger(registry, FakeLedger(), pipeline).sweep_once()

    assert result.failed == 1
    assert result.transitioned == 1
    assert result.strategy_ids == ["01GOOD"]


async def test_registry_failure_returns_a_result_rather_than_raising() -> None:
    """A dead database must not crash the service into a restart loop."""

    result = await _trigger(FakeRegistry(raises=True), FakeLedger(), FakePipeline()).sweep_once()

    assert result.failed == 1
    assert result.candidates == 0


async def test_an_unchanged_refusal_is_retried_every_sweep_but_reported_once() -> None:
    """Retried because a Tier-3 change can make a refused strategy evaluable.

    Reported once because a permanently-unevaluable strategy would otherwise
    emit a line every sweep interval, forever. `refusals_reported` is the count
    worth alerting on; `refused` alone stays flat and says nothing new.
    """

    pipeline = FakePipeline(evaluate_raises=SpecHygieneError("XLE is not Tier-3 eligible"))
    trigger = _trigger(FakeRegistry([_record()]), FakeLedger(), pipeline)

    first = await trigger.sweep_once()
    second = await trigger.sweep_once()

    assert first.refused == second.refused == 1  # retried, not suppressed
    assert first.refusals_reported == 1
    assert second.refusals_reported == 0


async def test_a_changed_refusal_reason_is_reported_again() -> None:
    """Suppression must key on the reason, not on the strategy.

    Otherwise a strategy that starts failing for a *new* reason goes unreported.
    """

    pipeline = FakePipeline(evaluate_raises=SpecHygieneError("first reason"))
    trigger = _trigger(FakeRegistry([_record()]), FakeLedger(), pipeline)

    first = await trigger.sweep_once()
    pipeline._evaluate_raises = SpecHygieneError("second reason")
    second = await trigger.sweep_once()

    assert first.refusals_reported == second.refusals_reported == 1


async def test_a_strategy_that_becomes_evaluable_clears_its_refusal_memory() -> None:
    """Otherwise a later relapse to the same reason would go unreported."""

    pipeline = FakePipeline("kill", evaluate_raises=SpecHygieneError("not Tier-3 yet"))
    trigger = _trigger(FakeRegistry([_record()]), FakeLedger(), pipeline)

    assert (await trigger.sweep_once()).refusals_reported == 1
    pipeline._evaluate_raises = None  # the Curator promoted the ticker
    assert (await trigger.sweep_once()).transitioned == 1
    pipeline._evaluate_raises = SpecHygieneError("not Tier-3 yet")  # and evicted it again
    assert (await trigger.sweep_once()).refusals_reported == 1


# --- reporting ---------------------------------------------------------------


async def test_every_candidate_lands_in_exactly_one_bucket() -> None:
    """The counts must reconcile, or the sweep log quietly loses strategies."""

    class MixedPipeline(FakePipeline):
        async def evaluate(self, strategy_id: str, *, trigger: str = "on-demand") -> Any:
            if strategy_id == "01REFUSE":
                raise SpecHygieneError("no policy")
            if strategy_id == "01FAIL":
                raise RuntimeError("boom")
            self.evaluated.append((strategy_id, trigger))
            verdict = "promote" if strategy_id == "01PROMOTE" else "kill"
            to_stage = STATUS_PAPER if verdict == "promote" else STATUS_KILLED
            return FakeOutcome(verdict, to_stage)

    records = [
        _record("01KILL", "h1"),
        _record("01PROMOTE", "h2"),
        _record("01REFUSE", "h3"),
        _record("01FAIL", "h4"),
        _record("01RECENT", "h5"),
    ]
    ledger = FakeLedger({("01RECENT", "h5", PROTOCOL_VERSION): _NOW})
    result = await _trigger(FakeRegistry(records), ledger, MixedPipeline()).sweep_once()

    buckets = (
        result.transitioned
        + result.held_for_review
        + result.recorded
        + result.skipped_recent
        + result.refused
        + result.failed
    )
    assert buckets == result.candidates == len(records)
    assert result.summary().startswith("candidates=5")


def test_disposition_values_are_unique() -> None:
    """Buckets are counted by identity; a duplicate value would merge two."""

    values = [d.value for d in Disposition]
    assert len(values) == len(set(values))
