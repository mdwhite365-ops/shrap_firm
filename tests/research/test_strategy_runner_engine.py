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
  confidence clears the real Decision Maker threshold;
- entries are sized in dollars against account equity, exits sell the recorded
  entry, and an entry that cannot be funded records *flat* rather than a
  position the firm does not hold.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

import pytest

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
    allocate_equity,
    plan_session,
)
from shrap.research.strategy_runner.sizing import SizingRefused
from shrap.trading_floor.decision_maker_stub import DEFAULT_CONFIDENCE_THRESHOLD

SESSION = date(2026, 7, 24)
# Any wall-clock instant works: every strategy in these tests has no declared
# cadence, so each resolves to the constant `session` slot regardless of NOW.
NOW = datetime(2026, 7, 24, 14, 30, tzinfo=UTC)
YESTERDAY = SESSION - timedelta(days=1)
EQUITY = 10_000.0

# The transition tests are about transitions, so they run with the per-order cap
# effectively off. The cap has its own tests below; leaving the production
# default (1) in place here would make every buy assert the clamp by accident.
UNCAPPED = RunnerSignalConfig(max_quantity=1_000_000)
ACCOUNT = "PA3TESTACCT"


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
    config: RunnerSignalConfig = UNCAPPED,
    equity: float = EQUITY,
):
    plans = plan_session(
        session_date=SESSION,
        now=NOW,
        strategies=[item],
        stored_state=stored,
        factory=_factory_returning(strategy),
        config=config,
        regime_label=regime_label,
        equity=equity,
        account_id=ACCOUNT,
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
    stored = {("s1", "NVDA"): TargetState(1.0, SIDE_BUY, YESTERDAY, 40)}
    plan = _plan_one(strategy=strategy, item=_input("s1", "NVDA", 5), stored=stored)
    assert [s.side for s in plan.signals] == [SIDE_SELL]
    (write,) = plan.state_writes
    assert write.last_target == 0.0
    assert write.last_side == SIDE_SELL
    assert write.last_quantity == 0  # position closed


def test_unchanged_invested_emits_nothing_but_stamps_state() -> None:
    strategy = FakeStrategy(name="t", warmup=3, weights={"NVDA": 1.0})
    stored = {("s1", "NVDA"): TargetState(1.0, SIDE_BUY, YESTERDAY, 40)}
    plan = _plan_one(strategy=strategy, item=_input("s1", "NVDA", 5), stored=stored)
    assert plan.signals == ()
    (write,) = plan.state_writes  # still stamped this session (idempotency guard)
    assert write.last_session_date == SESSION
    assert write.last_side == SIDE_BUY  # carried forward
    # The held position is carried forward untouched. Re-sizing a hold would
    # drift the recorded quantity away from the shares actually held, and the
    # eventual exit sells the recorded quantity.
    assert write.last_quantity == 40


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
        now=NOW,
        strategies=[_input("bad", "AAPL", 5), _input("good", "NVDA", 5)],
        stored_state={},
        factory=factory,
        config=UNCAPPED,
        regime_label=None,
        equity=EQUITY,
        account_id=ACCOUNT,
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
    # $10,000 fully weighted into a $10 name is 1,000 shares — sized, not fixed.
    assert payload["quantity"] == payload["size_hint"] == 1_000
    assert payload["urgency"] == "normal"
    text = payload["justification_text"]
    # The justification names the strategy *record* (its registry name), the
    # transition, the size and its basis, and the paper-only disclaimer.
    assert "trend-v1" in text and "flat -> invested" in text
    assert "1000 share(s)" in text and "$10,000.00 equity" in text
    assert "not investment advice" in text.lower()


# --- sizing: the live book has to be the evaluated book -----------------------


def _multi_input(strategy_id: str, closes: dict[str, float], n_bars: int = 5) -> StrategyInput:
    record = _make_record(strategy_id, list(closes))
    return StrategyInput(
        record=record,
        tickers=list(closes),
        bars_by_ticker={t: _bars(n_bars, close) for t, close in closes.items()},
    )


def test_an_entry_is_a_dollar_slot_not_a_share_count() -> None:
    """20% of $10,000 is $2,000; at $50 that is 40 shares, not 1."""

    strategy = FakeStrategy(name="t", warmup=3, weights={"NVDA": 0.20})
    item = _multi_input("s1", {"NVDA": 50.0})
    plan = _plan_one(strategy=strategy, item=item, stored={})
    assert plan.signals[0].payload["quantity"] == 40
    assert plan.state_writes[0].last_quantity == 40


def test_equal_weights_across_a_universe_become_equal_dollars() -> None:
    """The property the fixed-quantity path could not express.

    One share of a $500 name and one share of a $25 name are a 20x difference in
    exposure. An equal-weight strategy has to trade equal *dollars*, or the live
    book is not the book the Evaluator measured.
    """

    weights = dict.fromkeys(("AAA", "BBB", "CCC"), 1 / 3)
    strategy = FakeStrategy(name="t", warmup=3, weights=weights)
    item = _multi_input("s1", {"AAA": 500.0, "BBB": 25.0, "CCC": 100.0})
    plan = _plan_one(strategy=strategy, item=item, stored={})

    notionals = {s.ticker: s.payload["quantity"] for s in plan.signals}
    prices = {"AAA": 500.0, "BBB": 25.0, "CCC": 100.0}
    spent = {t: q * prices[t] for t, q in notionals.items()}
    # Each slot is ~$3,333; flooring costs at most one share, so allow 1 share
    # of the priciest name as tolerance.
    assert max(spent.values()) - min(spent.values()) <= 500.0
    assert notionals == {"AAA": 6, "BBB": 133, "CCC": 33}


def test_an_exit_sells_the_recorded_entry_not_a_freshly_sized_one() -> None:
    """The price moved since entry. Re-sizing the exit would leave a residual
    (price up) or oversell into a short (price down)."""

    strategy = FakeStrategy(name="t", warmup=3, weights={"NVDA": 0.0})
    stored = {("s1", "NVDA"): TargetState(0.20, SIDE_BUY, YESTERDAY, 40)}
    # Price has doubled since the entry; a re-sized exit would sell 20.
    item = _multi_input("s1", {"NVDA": 100.0})
    plan = _plan_one(strategy=strategy, item=item, stored=stored)
    assert plan.signals[0].payload["quantity"] == 40


def test_an_exit_of_a_pre_sizing_row_sells_one_share() -> None:
    """Rows written before this card hold exactly 1 share — that is what the
    fixed-quantity path emitted, so 1 is a fact about them, not a guess."""

    strategy = FakeStrategy(name="t", warmup=3, weights={"NVDA": 0.0})
    stored = {("s1", "NVDA"): TargetState(1.0, SIDE_BUY, YESTERDAY, 0)}
    plan = _plan_one(strategy=strategy, item=_input("s1", "NVDA", 5), stored=stored)
    assert plan.signals[0].payload["quantity"] == 1


def test_an_unfundable_entry_emits_nothing_and_records_flat() -> None:
    """THE trap in this card.

    A 10% slot on $10,000 is $1,000, so a $1,500 name cannot be held at all.
    Recording the *intended* weight anyway would make next session read
    invested -> flat and emit a sell for a position that was never opened.
    """

    strategy = FakeStrategy(name="t", warmup=3, weights={"BRKA": 0.10})
    item = _multi_input("s1", {"BRKA": 1_500.0})
    plan = _plan_one(strategy=strategy, item=item, stored={})

    assert plan.signals == ()
    (write,) = plan.state_writes
    assert write.last_target == 0.0  # flat, not 0.10
    assert write.last_quantity == 0
    assert any("smaller than one share" in note for note in plan.sizing_notes)


def test_a_clamped_entry_is_reported_rather_than_silent() -> None:
    """The runner's cap mirrors the Pre-Trade Checker's, which clamps rather
    than vetoes. An unreported clamp is a strategy quietly under-weight."""

    strategy = FakeStrategy(name="t", warmup=3, weights={"NVDA": 1.0})
    plan = _plan_one(
        strategy=strategy,
        item=_input("s1", "NVDA", 5),
        stored={},
        config=RunnerSignalConfig(max_quantity=1),
    )
    assert plan.signals[0].payload["quantity"] == 1
    assert any("clamped 1000 -> 1" in note for note in plan.sizing_notes)
    # State records what was actually ordered, so the exit closes what exists.
    assert plan.state_writes[0].last_quantity == 1


def test_the_production_default_matches_the_pre_trade_cap() -> None:
    """These two must move together.

    The Pre-Trade Checker clamps to its own cap, so a runner sizing above it
    records an intent larger than the fill — and the exit sells shares that were
    never bought. This test fails the moment one is raised without the other.
    """

    from shrap.agents.risk_compliance.pre_trade_checker.config import (
        Settings as PreTradeSettings,
    )

    assert RunnerSignalConfig().max_quantity == PreTradeSettings().max_quantity_per_order


def test_a_zero_equity_pass_cannot_be_planned_at_all() -> None:
    """plan_session takes equity with no default, so there is no code path that
    sizes against a fabricated account. The service asserts usability first."""

    import inspect

    assert inspect.signature(plan_session).parameters["equity"].default is inspect.Parameter.empty


# --- firm-wide exposure: the book cannot lever itself by promoting strategies --


def _plan_many(
    strategies: list[tuple[str, dict[str, float]]],
    *,
    config: RunnerSignalConfig = UNCAPPED,
    equity: float = EQUITY,
) -> float:
    """Plan N strategies and return total ordered notional at PRICE_FLAT."""

    price = 10.0  # _bars default close
    items: list[StrategyInput] = []
    fakes: dict[str, FakeStrategy] = {}
    for sid, weights in strategies:
        tickers = list(weights)
        items.append(
            StrategyInput(
                record=_make_record(sid, tickers),
                tickers=tickers,
                bars_by_ticker={t: _bars(5) for t in tickers},
            )
        )
        fakes[sid] = FakeStrategy(name=sid, warmup=3, weights=weights)

    plans = plan_session(
        session_date=SESSION,
        now=NOW,
        strategies=items,
        stored_state={},
        factory=lambda rec, tks: fakes[rec.strategy_id],
        config=config,
        regime_label=None,
        equity=equity,
        account_id=ACCOUNT,
    )
    return sum(s.payload["quantity"] * price for p in plans for s in p.signals)


def test_one_strategy_fully_invested_deploys_the_account_once() -> None:
    assert _plan_many([("s1", {"AAA": 1.0})]) == pytest.approx(EQUITY)


def test_two_strategies_do_not_order_two_accounts_worth() -> None:
    """THE test in this card, and it was a real defect.

    Before the exposure budget, every strategy sized against the *whole*
    account, so two strategies at full investment ordered $20,000 against
    $10,000 of equity. Measured on this engine — arithmetic, not a hypothetical.
    """

    total = _plan_many([("s1", {"AAA": 1.0}), ("s2", {"BBB": 1.0})])
    assert total == pytest.approx(EQUITY)  # not 2 x EQUITY


def test_four_strategies_still_deploy_exactly_one_account() -> None:
    """Four was 4.0x gross — precisely the FINRA 25% maintenance ceiling, so a
    fifth strategy would have breached it and opened a margin deficit."""

    total = _plan_many([(f"s{i}", {f"T{i}": 1.0}) for i in range(4)])
    assert total == pytest.approx(EQUITY)


def test_promoting_a_strategy_shrinks_the_others_rather_than_growing_the_book() -> None:
    one = _plan_many([("s1", {"AAA": 1.0})])
    four = _plan_many([(f"s{i}", {f"T{i}": 1.0}) for i in range(4)])
    assert one == pytest.approx(four)


def test_the_exposure_budget_is_a_multiple_of_equity() -> None:
    """The knob exists so leverage is a decision someone makes, not a side
    effect of how many strategies happen to be promoted."""

    levered = _plan_many(
        [("s1", {"AAA": 1.0}), ("s2", {"BBB": 1.0})],
        config=RunnerSignalConfig(max_quantity=1_000_000, max_gross_exposure=2.0),
    )
    assert levered == pytest.approx(2 * EQUITY)


def test_the_default_is_unlevered() -> None:
    """Mike's ruling is 'it can be aggressive', but leverage is how accounts
    reach zero and there is still no drawdown limit or margin-deficit model."""

    assert RunnerSignalConfig().max_gross_exposure == 1.0


def test_weights_within_a_strategy_keep_their_shape_as_others_are_added() -> None:
    """Equal slices, not proportional rescaling.

    A strategy must trade the shape the Evaluator measured. Only its scale may
    depend on what else is running.
    """

    alone = _plan_many([("s1", {"AAA": 0.75, "BBB": 0.25})])
    shared = _plan_many([("s1", {"AAA": 0.75, "BBB": 0.25}), ("s2", {"CCC": 1.0})])
    # s1's own split is untouched; only its slice halved.
    assert shared == pytest.approx(alone)


def test_allocate_equity_refuses_a_zero_budget_rather_than_sizing_to_nothing() -> None:
    with pytest.raises(SizingRefused, match="no capital may be deployed"):
        allocate_equity(EQUITY, 2, 0.0)


def test_allocate_equity_handles_an_empty_pass() -> None:
    assert allocate_equity(EQUITY, 0, 1.0) == 0.0


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


# --- cadence: firing more than once per session (2.9) -------------------------
#
# The risk this section guards is not "can an intraday strategy trade often".
# It is "does turning intraday firing on make the twelve daily strategies
# already in the registry trade on every wake". So the daily case is tested
# first and hardest.


def _cadence_input(strategy_id: str, ticker: str, cadence: object) -> StrategyInput:
    record = _make_record(strategy_id, [ticker])
    spec = dict(record.spec)
    if cadence is not None:
        spec["cadence"] = cadence
    return StrategyInput(
        record=replace(record, spec=spec),
        tickers=[ticker],
        bars_by_ticker={ticker: _bars(5)},
    )


def _plan_at(item: StrategyInput, stored: dict[tuple[str, str], TargetState], now: datetime):
    plans = plan_session(
        session_date=SESSION,
        now=now,
        strategies=[item],
        stored_state=stored,
        factory=_factory_returning(FakeStrategy(name="cadence", warmup=1, weights={"NVDA": 1.0})),
        config=UNCAPPED,
        regime_label="risk-on",
        equity=EQUITY,
        account_id=ACCOUNT,
    )
    return plans[0]


def _apply(stored: dict[tuple[str, str], TargetState], plan) -> None:
    for write in plan.state_writes:
        stored[(write.strategy_id, write.ticker)] = TargetState(
            last_target=write.last_target,
            last_side=write.last_side,
            last_session_date=write.last_session_date,
            last_quantity=write.last_quantity,
            last_slot=write.last_slot,
        )


def test_a_daily_strategy_acts_once_however_many_times_the_runner_wakes() -> None:
    item = _cadence_input("daily-1", "NVDA", None)
    stored: dict[tuple[str, str], TargetState] = {}
    emitted = 0

    # Seventy-eight wakes: a full session at a five-minute tick.
    for minute in range(0, 390, 5):
        now = datetime(2026, 7, 24, 13, 30, tzinfo=UTC) + timedelta(minutes=minute)
        plan = _plan_at(item, stored, now)
        emitted += len(plan.signals)
        _apply(stored, plan)

    # Exactly one. This is the regression that would empty an account.
    assert emitted == 1


def test_an_intraday_strategy_acts_once_per_interval() -> None:
    item = _cadence_input("intraday-1", "NVDA", {"kind": "intraday", "interval_minutes": 30})
    stored: dict[tuple[str, str], TargetState] = {}
    slots: list[str] = []

    for minute in range(0, 120, 5):
        now = datetime(2026, 7, 24, 14, 0, tzinfo=UTC) + timedelta(minutes=minute)
        plan = _plan_at(item, stored, now)
        _apply(stored, plan)
        if not plan.skipped:
            slots.append(stored[("intraday-1", "NVDA")].last_slot)

    # Four thirty-minute slots across two hours, each entered exactly once —
    # not twenty-four, which is how often the planner was asked.
    assert slots == ["14:00", "14:30", "15:00", "15:30"]


def test_a_stored_row_from_before_cadence_existed_does_not_re_trade() -> None:
    # Rows written by the pre-cadence Runner carry no slot; the column default
    # backfills them to `session`. If that default were anything else, every one
    # of them would compare unequal to today's slot and trade a second time on
    # the first pass after deploy.
    item = _cadence_input("legacy-1", "NVDA", None)
    stored = {
        ("legacy-1", "NVDA"): TargetState(
            last_target=1.0,
            last_side="buy",
            last_session_date=SESSION,
            last_quantity=10,
        )
    }

    plan = _plan_at(item, stored, NOW)

    assert plan.skipped
    assert plan.signals == ()


def test_a_malformed_cadence_trades_daily_rather_than_every_tick() -> None:
    item = _cadence_input("typo-1", "NVDA", {"kind": "intrday", "interval_minutes": 1})
    stored: dict[tuple[str, str], TargetState] = {}
    emitted = 0

    for minute in range(0, 60, 5):
        now = datetime(2026, 7, 24, 14, 0, tzinfo=UTC) + timedelta(minutes=minute)
        plan = _plan_at(item, stored, now)
        emitted += len(plan.signals)
        _apply(stored, plan)

    assert emitted == 1
