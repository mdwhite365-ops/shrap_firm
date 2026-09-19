"""The scheduled sweep judges a strategy on the grain it declared.

Before this, the sweep built ONE pipeline on daily bars and evaluated everything
with it. That was correct for as long as every strategy was daily, and it became
a silent auto-kill the moment one was not.

Measured, not hypothesised. On 2026-09-18 an intraday momentum strategy was
seeded at `hypothesis` and the sweep reached it within the hour:

    trigger        | scheduled-sweep
    reason         | insufficient-data
    total_trades   | 0
    timeframe      | (null -> daily)

Its horizons are counted in BARS — a six-month formation at a 15-minute grain is
3,276 bars — so read against daily bars it asked for 3,276 SESSIONS, thirteen
years against a 5.1-year panel. `insufficient-data`, zero periods, no error.
The same strategy evaluated on its own grain ran 17,553 trades over 37,116 bars.

The verdict was wrong in the direction that looks like diligence, which is the
dangerous one: a strategy that never trades is easy to read as a strategy that
was correctly rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from shrap.research.strategy_evaluator.trigger_service import (
    EvaluatorTrigger,
    SingleGrainPipelines,
)
from shrap.research.strategy_runner.cadence import (
    CADENCE_INTRADAY,
    DAILY,
    Cadence,
)


@dataclass
class FakeOutcome:
    verdict: str = "hold-for-data"
    reason: str = "below-sharpe-floor"


@dataclass
class FakeCommit:
    to_stage: str = "hypothesis"
    transitioned: bool = False
    promotion_held: bool = False
    evaluation_id: str = "eval-1"


@dataclass
class FakePipeline:
    label: str
    evaluated: list[str]

    async def evaluate(self, strategy_id: str, *, trigger: str = "") -> FakeOutcome:
        self.evaluated.append(strategy_id)
        return FakeOutcome()

    async def commit(self, outcome: Any, *, promote_requires_review: bool = True) -> FakeCommit:
        return FakeCommit()


class SplitPipelines:
    """Distinguishable by which grain was asked for."""

    def __init__(self) -> None:
        self.daily = FakePipeline("daily", [])
        self.intraday = FakePipeline("intraday", [])
        self.asked: list[Cadence] = []

    def for_cadence(self, cadence: Cadence) -> FakePipeline:
        self.asked.append(cadence)
        return self.intraday if cadence.is_intraday else self.daily


@dataclass
class FakeRecord:
    strategy_id: str
    spec: dict[str, Any]
    spec_hash: str = "sha256:x"
    name: str = "s"
    status: str = "hypothesis"
    tickers: Any = None


class FakeRegistry:
    def __init__(self, records: list[FakeRecord]) -> None:
        self._records = records

    async def list_by_status(self, status: str) -> list[FakeRecord]:
        return self._records


class NeverEvaluated:
    async def latest_evaluation_at(self, *a: object, **k: object) -> None:
        return None


def _trigger(records: list[FakeRecord], pipelines: Any) -> EvaluatorTrigger:
    return EvaluatorTrigger(
        registry=FakeRegistry(records),  # type: ignore[arg-type]
        ledger=NeverEvaluated(),  # type: ignore[arg-type]
        pipelines=pipelines,
    )


@pytest.mark.asyncio
async def test_a_daily_strategy_is_judged_on_daily_bars() -> None:
    """Everything in the registry today. Behaviour must be unchanged."""

    pipelines = SplitPipelines()
    await _trigger([FakeRecord("s1", {"rule": "cross-sectional-momentum"})], pipelines).sweep_once()
    assert pipelines.daily.evaluated == ["s1"]
    assert pipelines.intraday.evaluated == []


@pytest.mark.asyncio
async def test_an_intraday_strategy_is_judged_on_its_own_grain() -> None:
    """THE regression. Before this it went to the daily pipeline and died there."""

    pipelines = SplitPipelines()
    record = FakeRecord("s1", {"cadence": {"kind": "intraday", "interval_minutes": 15}})
    await _trigger([record], pipelines).sweep_once()
    assert pipelines.intraday.evaluated == ["s1"]
    assert pipelines.daily.evaluated == []


@pytest.mark.asyncio
async def test_a_malformed_cadence_is_judged_daily() -> None:
    """`read_cadence` is total and resolves anything it cannot parse to DAILY.

    The sweep must land on the same answer the Runner and the seed do, or a typo
    would be evaluated on one grain and traded on another.
    """

    pipelines = SplitPipelines()
    records = [
        FakeRecord("typo", {"cadence": "intrday"}),
        FakeRecord("bad-interval", {"cadence": {"kind": "intraday", "interval_minutes": 0}}),
        FakeRecord("nonsense", {"cadence": 17}),
    ]
    await _trigger(records, pipelines).sweep_once()
    assert sorted(pipelines.daily.evaluated) == ["bad-interval", "nonsense", "typo"]
    assert pipelines.intraday.evaluated == []


@pytest.mark.asyncio
async def test_a_mixed_registry_routes_each_strategy_separately() -> None:
    """One sweep, two grains. The sweep must not pick one mode for the batch."""

    pipelines = SplitPipelines()
    records = [
        FakeRecord("daily-1", {}),
        FakeRecord("intra-1", {"cadence": {"kind": "intraday", "interval_minutes": 5}}),
        FakeRecord("daily-2", {}),
    ]
    await _trigger(records, pipelines).sweep_once()
    assert sorted(pipelines.daily.evaluated) == ["daily-1", "daily-2"]
    assert pipelines.intraday.evaluated == ["intra-1"]


@pytest.mark.asyncio
async def test_a_single_pipeline_caller_still_works() -> None:
    """The pre-cadence constructor, named rather than implied."""

    only = FakePipeline("only", [])
    trigger = EvaluatorTrigger(
        registry=FakeRegistry([FakeRecord("s1", {"cadence": {"kind": "intraday"}})]),  # type: ignore[arg-type]
        ledger=NeverEvaluated(),  # type: ignore[arg-type]
        pipeline=only,  # type: ignore[arg-type]
    )
    await trigger.sweep_once()
    assert only.evaluated == ["s1"]


def test_a_trigger_needs_one_of_the_two() -> None:
    with pytest.raises(ValueError, match="pipeline or a pipeline resolver"):
        EvaluatorTrigger(
            registry=FakeRegistry([]),  # type: ignore[arg-type]
            ledger=NeverEvaluated(),  # type: ignore[arg-type]
        )


def test_single_grain_resolver_ignores_cadence() -> None:
    only = FakePipeline("only", [])
    resolver = SingleGrainPipelines(only)  # type: ignore[arg-type]
    assert resolver.for_cadence(DAILY) is only
    assert resolver.for_cadence(Cadence(kind=CADENCE_INTRADAY, interval_minutes=5)) is only


def test_the_intraday_window_is_mandatory_and_matches_the_backfill() -> None:
    """An unattended sweep has no human to ask how deep the data goes.

    The CLI refuses an unbounded intraday lookback outright — one ticker-year is
    ~252 daily rows but ~6,500 at 15Min — so the sweep carries a configured
    window instead. Six years matches the 15Min depth backfilled 2026-09-18
    (2021-01-04 to present, 37,116 bars).
    """

    from shrap.research.strategy_evaluator.trigger_service import (
        DEFAULT_INTRADAY_WINDOW_YEARS,
    )

    assert DEFAULT_INTRADAY_WINDOW_YEARS >= 6
