"""Pure-core tests for the paper-strategy runner planner.

Fabricated strategies + bar panels + stored state exercise every branch of
:func:`shrap.research.strategy_runner.engine.plan_session`:

- flat -> invested emits a buy, invested -> flat emits a sell, unchanged emits
  nothing, first-ever invested target emits the first buy;
- missing / insufficient bars skip the strategy (never emit);
- the per-(strategy, session) dedupe prevents a second pass;
- a broken factory skips one strategy without touching the others (fail-safe);
- regime is informational, never a gate;
- the emitted payload schema matches the Strategy Fixture exactly and its
  confidence clears the real Decision Maker threshold.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from shrap.research.strategy_evaluator.strategy import BarSample, PanelWindow
from shrap.research.strategy_registry import (
    SCHEMA_VERSION,
    STATUS_PAPER,
    StrategyRecord,
)
from shrap.research.strategy_runner.engine import (
    SIDE_BUY,
    SIDE_SELL,
    UNKNOWN_REGIME,
    RunnerSignalConfig,
    StrategyInput,
    TargetState,
    plan_session,
)
from shrap.trading_floor.decision_maker_stub import DEFAULT_CONFIDENCE_THRESHOLD

SESSION = date(2026, 7, 24)
YESTERDAY = SESSION - timedelta(days=1)


# --- fabricated strategy seam -------------------------------------------------


@dataclass(frozen=True)
class FakeStrategy:
    """A StrategySignal whose target is fixed, so tests control the transition."""

    name: str
    warmup: int
    weights: Mapping[str, float]

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        return dict(self.weights)


def _make_record(strategy_id: str, tickers: list[str], name: str = "trend-v1") -> StrategyRecord:
    return StrategyRecord(
        strategy_id=strategy_id,
        name=name,
        version=1,
        archetype="infra-graph-play",
        status=STATUS_PAPER,
        source="test",
        thesis="test",
        anchor=None,
        tickers={"long": tickers},
        spec={"params": {"fast": 2, "slow": 3}},
        spec_hash=f"hash-{strategy_id}",
        regime_sizing_modifier=None,
        kill_criteria=["md>0.5"],
        code_ref=None,
        created_at=None,
        updated_at=None,
    )


def _bars(n: int, ticker_close: float = 10.0) -> list[BarSample]:
    return [
        BarSample(
            session_date=date(2026, 1, 1) + timedelta(days=i),
            open=ticker_close,
            high=ticker_close,
            low=ticker_close,
            close=ticker_close,
            volume=1000.0,
        )
        for i in range(n)
    ]


def _factory_returning(strategy: FakeStrategy):
    def factory(record: StrategyRecord, tickers: list[str]) -> FakeStrategy:
        return strategy

    return factory


def _input(strategy_id: str, ticker: str, n_bars: int) -> StrategyInput:
    record = _make_record(strategy_id, [ticker])
    return StrategyInput(
        record=record,
        tickers=[ticker],
        bars_by_ticker={ticker: _bars(n_bars)},
    )


def _plan_one(
    *,
    strategy: FakeStrategy,
    item: StrategyInput,
    stored: dict[tuple[str, str], TargetState],
    regime_label: str | None = "risk-on",
):
    plans = plan_session(
        session_date=SESSION,
        strategies=[item],
        stored_state=stored,
        factory=_factory_returning(strategy),
        config=RunnerSignalConfig(),
        regime_label=regime_label,
    )
    assert len(plans) == 1
    return plans[0]


# --- transition matrix --------------------------------------------------------


def test_first_ever_invested_target_emits_first_buy() -> None:
    strategy = FakeStrategy(name="t", warmup=3, weights={"NVDA": 1.0})
    plan = _plan_one(strategy=strategy, item=_input("s1", "NVDA", 5), stored={})
    assert not plan.skipped
    assert [(s.side, s.ticker) for s in plan.signals] == [(SIDE_BUY, "NVDA")]
    (write,) = plan.state_writes
    assert write.last_target == 1.0
    assert write.last_side == SIDE_BUY
    assert write.last_session_date == SESSION


def test_flat_to_invested_emits_buy() -> None:
    strategy = FakeStrategy(name="t", warmup=3, weights={"NVDA": 1.0})
    stored = {("s1", "NVDA"): TargetState(0.0, None, YESTERDAY)}
    plan = _plan_one(strategy=strategy, item=_input("s1", "NVDA", 5), stored=stored)
    assert [s.side for s in plan.signals] == [SIDE_BUY]


def test_invested_to_flat_emits_sell() -> None:
    strategy = FakeStrategy(name="t", warmup=3, weights={"NVDA": 0.0})
    stored = {("s1", "NVDA"): TargetState(1.0, SIDE_BUY, YESTERDAY)}
    plan = _plan_one(strategy=strategy, item=_input("s1", "NVDA", 5), stored=stored)
    assert [s.side for s in plan.signals] == [SIDE_SELL]
    (write,) = plan.state_writes
    assert write.last_target == 0.0
    assert write.last_side == SIDE_SELL


def test_unchanged_invested_emits_nothing_but_stamps_state() -> None:
    strategy = FakeStrategy(name="t", warmup=3, weights={"NVDA": 1.0})
    stored = {("s1", "NVDA"): TargetState(1.0, SIDE_BUY, YESTERDAY)}
    plan = _plan_one(strategy=strategy, item=_input("s1", "NVDA", 5), stored=stored)
    assert plan.signals == ()
    (write,) = plan.state_writes  # still stamped this session (idempotency guard)
    assert write.last_session_date == SESSION
    assert write.last_side == SIDE_BUY  # carried forward


def test_unchanged_flat_emits_nothing() -> None:
    strategy = FakeStrategy(name="t", warmup=3, weights={"NVDA": 0.0})
    plan = _plan_one(strategy=strategy, item=_input("s1", "NVDA", 5), stored={})
    assert plan.signals == ()
    assert plan.state_writes[0].last_target == 0.0


# --- data guards --------------------------------------------------------------


def test_missing_bars_skips_and_never_emits() -> None:
    strategy = FakeStrategy(name="t", warmup=3, weights={"NVDA": 1.0})
    item = StrategyInput(
        record=_make_record("s1", ["NVDA"]), tickers=["NVDA"], bars_by_ticker={"NVDA": []}
    )
    plan = _plan_one(strategy=strategy, item=item, stored={})
    assert plan.skipped
    assert plan.signals == ()
    assert plan.state_writes == ()
    assert plan.skip_reason is not None and "missing bars" in plan.skip_reason


def test_insufficient_bars_skips() -> None:
    strategy = FakeStrategy(name="t", warmup=10, weights={"NVDA": 1.0})
    plan = _plan_one(strategy=strategy, item=_input("s1", "NVDA", 3), stored={})
    assert plan.skipped
    assert plan.signals == ()
    assert plan.skip_reason is not None and "insufficient bars" in plan.skip_reason


# --- idempotency + fail-safe --------------------------------------------------


def test_per_session_dedupe_prevents_second_pass() -> None:
    strategy = FakeStrategy(name="t", warmup=3, weights={"NVDA": 0.0})
    # already stamped for this session -> a re-delivered open event is a no-op.
    stored = {("s1", "NVDA"): TargetState(1.0, SIDE_BUY, SESSION)}
    plan = _plan_one(strategy=strategy, item=_input("s1", "NVDA", 5), stored=stored)
    assert plan.skipped
    assert plan.signals == ()
    assert plan.state_writes == ()
    assert plan.skip_reason is not None and "already ran" in plan.skip_reason


def test_factory_error_skips_only_that_strategy() -> None:
    good = FakeStrategy(name="good", warmup=3, weights={"NVDA": 1.0})

    def factory(record: StrategyRecord, tickers: list[str]) -> FakeStrategy:
        if record.strategy_id == "bad":
            raise ValueError("broken spec")
        return good

    plans = plan_session(
        session_date=SESSION,
        strategies=[_input("bad", "AAPL", 5), _input("good", "NVDA", 5)],
        stored_state={},
        factory=factory,
        config=RunnerSignalConfig(),
        regime_label=None,
    )
    by_id = {p.strategy_id: p for p in plans}
    assert by_id["bad"].skipped
    assert by_id["bad"].skip_reason is not None and "error" in by_id["bad"].skip_reason
    assert not by_id["good"].skipped
    assert [s.side for s in by_id["good"].signals] == [SIDE_BUY]


# --- payload / regime ---------------------------------------------------------


def test_regime_label_is_informational_not_a_gate() -> None:
    strategy = FakeStrategy(name="t", warmup=3, weights={"NVDA": 1.0})
    plan = _plan_one(strategy=strategy, item=_input("s1", "NVDA", 5), stored={}, regime_label=None)
    assert [s.side for s in plan.signals] == [SIDE_BUY]  # still fires with no regime
    assert plan.signals[0].payload["regime_label"] == UNKNOWN_REGIME


def test_payload_carries_strategy_id_and_transition_justification() -> None:
    strategy = FakeStrategy(name="ma-cross", warmup=3, weights={"NVDA": 1.0})
    plan = _plan_one(strategy=strategy, item=_input("real-strat-id", "NVDA", 5), stored={})
    payload = plan.signals[0].payload
    assert payload["strategy_id"] == "real-strat-id"
    assert payload["ticker"] == "NVDA"
    assert payload["side"] == SIDE_BUY
    assert payload["quantity"] == payload["size_hint"] == 1
    assert payload["urgency"] == "normal"
    text = payload["justification_text"]
    # The justification names the strategy *record* (its registry name), the
    # crossover transition, and the paper-only disclaimer.
    assert "trend-v1" in text and "flat -> invested" in text
    assert "not investment advice" in text.lower()


def test_confidence_clears_the_decision_maker_threshold() -> None:
    strategy = FakeStrategy(name="t", warmup=3, weights={"NVDA": 1.0})
    plan = _plan_one(strategy=strategy, item=_input("s1", "NVDA", 5), stored={})
    assert plan.signals[0].payload["confidence"] > DEFAULT_CONFIDENCE_THRESHOLD


async def test_payload_schema_is_identical_to_the_fixture() -> None:
    # Read the fixture's real payload keys by firing it once, then assert the
    # runner emits exactly the same key set (its true drop-in successor).
    from shrap.research.strategy_fixture import (
        STREAM_STRATEGY_SIGNAL as FIXTURE_STREAM,
    )
    from shrap.research.strategy_fixture import (
        FixtureConfig,
        fire_once,
    )

    class FakeFixtureRedis:
        def __init__(self) -> None:
            self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
            self.flags: dict[str, str] = {}

        async def xadd(self, stream: str, fields: dict[str, str]) -> str:
            entries = self.streams.setdefault(stream, [])
            entry_id = f"1-{len(entries)}"
            entries.append((entry_id, fields))
            return entry_id

        async def xrevrange(
            self, name: str, max: str = "+", min: str = "-", count: int | None = None
        ) -> list[tuple[str, dict[str, str]]]:
            return self.streams.get(name, [])[-(count or 1) :][::-1]

        async def set(
            self, name: str, value: str, nx: bool = False, ex: int | None = None
        ) -> bool | None:
            if nx and name in self.flags:
                return None
            self.flags[name] = value
            return True

    from shrap.events import EventPublisher

    redis = FakeFixtureRedis()
    await EventPublisher(redis).publish(
        stream="intel.regime.sizing-modifier",
        produced_by="intelligence/regime-classifier",
        schema_version=SCHEMA_VERSION,
        payload={"label": "crisis-recovery"},
    )
    event = await fire_once(redis, FixtureConfig())
    assert event is not None and event.envelope.payload is not None
    assert FIXTURE_STREAM == "trading.strategy.signal"
    fixture_keys = set(event.envelope.payload)

    strategy = FakeStrategy(name="t", warmup=3, weights={"NVDA": 1.0})
    plan = _plan_one(strategy=strategy, item=_input("s1", "NVDA", 5), stored={})
    assert set(plan.signals[0].payload) == fixture_keys
