"""Pipeline tests: hygiene refusals, anchor kill, trade-count kill, promote,
dry-run purity, and the evaluation card's required wording.

Uses fakes for the registry / reader / store and a real ``EventPublisher`` over
a fake Redis (project convention), so the ADR-0006 envelope path is exercised.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from shrap.events import EventPublisher
from shrap.research.strategy_evaluator.costs import CostModel
from shrap.research.strategy_evaluator.engine import EvalConfig
from shrap.research.strategy_evaluator.pipeline import (
    REQUIRED_DISCLAIMER,
    STREAM_STRATEGY_PROMOTION_PENDING,
    STREAM_STRATEGY_VERDICT,
    EvaluationPipeline,
    SpecHygieneError,
)
from shrap.research.strategy_evaluator.strategy import BarSample, PanelWindow
from shrap.research.strategy_evaluator.verdict import (
    REASON_ANCHOR_NOT_LIVE,
    REASON_INSUFFICIENT_TRADES,
    REASON_PROMOTE,
    VERDICT_KILL,
    VERDICT_PROMOTE,
)
from shrap.research.strategy_registry import (
    STATUS_HYPOTHESIS,
    STATUS_KILLED,
    STATUS_PAPER,
    STREAM_STRATEGY_KILLED,
    StrategyRecord,
    StrategyTransition,
)

_FIXED_NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)
_TICKER = "NVDA"
_START = date(2020, 1, 1)


# --- fakes -------------------------------------------------------------------


class FakeRegistry:
    def __init__(self, record: StrategyRecord | None) -> None:
        self._record = record
        self.transitions: list[tuple[str, str, str | None]] = []

    async def get(self, strategy_id: str) -> StrategyRecord | None:
        if self._record is not None and self._record.strategy_id == strategy_id:
            return self._record
        return None

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
    ) -> StrategyTransition:
        self.transitions.append((strategy_id, to_status, expected_from))
        return StrategyTransition(
            transition_id="01T",
            strategy_id=strategy_id,
            from_status=expected_from,
            to_status=to_status,
            reason=reason,
            trigger_kind=trigger_kind,
            trigger_ref=trigger_ref,
            actor=actor,
            occurred_at=_FIXED_NOW,
        )


class FakeReader:
    def __init__(
        self,
        *,
        wc_status: str | None = "promoted",
        tier: str | None = "active",
        bars: list[BarSample] | None = None,
    ) -> None:
        self._wc_status = wc_status
        self._tier = tier
        self._bars = bars or []
        # Recorded so a test can assert the anchor was never *consulted* for an
        # anchor-less archetype, not merely that its result was ignored.
        self.wc_queries: list[str] = []

    async def world_changer_status(self, candidate_id: str) -> str | None:
        self.wc_queries.append(candidate_id)
        return self._wc_status

    async def ticker_tier(self, ticker: str) -> str | None:
        return self._tier

    async def read_bars(
        self, ticker: str, start: date, end: date, adjustment: str
    ) -> list[BarSample]:
        return list(self._bars)


class FakeStore:
    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []

    async def insert_evaluation(self, **kwargs: Any) -> None:
        self.inserted.append(kwargs)


class FakeRedis:
    def __init__(self) -> None:
        self.streams: list[str] = []
        self.fields: list[dict[str, str]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.streams.append(stream)
        self.fields.append(fields)
        return f"{len(self.streams)}-0"


class SquareWaveSignal:
    """Purpose-built promote-worthy signal: long/flat square wave by bar index."""

    def __init__(self, ticker: str, period: int) -> None:
        self._ticker = ticker
        self._period = period

    @property
    def name(self) -> str:
        return "square-wave"

    @property
    def warmup(self) -> int:
        return 1

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        long = (window.current_index // self._period) % 2 == 0
        return {self._ticker: 1.0 if long else 0.0}


# --- builders ----------------------------------------------------------------


def _record(
    *,
    archetype: str = "infra-graph-play",
    tickers: dict[str, Any] | None = None,
    anchor: dict[str, Any] | None = None,
    kill_criteria: list[Any] | None = None,
    spec: dict[str, Any] | None = None,
    status: str = STATUS_HYPOTHESIS,
) -> StrategyRecord:
    return StrategyRecord(
        strategy_id="01STRAT",
        name="nvda-photonics-graph",
        version=1,
        archetype=archetype,
        status=status,
        source="mike-seed",
        thesis="Silicon photonics is the binding infra node downstream of AI compute.",
        anchor={"world_changer_id": "01WC"} if anchor is None else anchor,
        tickers={"long": [_TICKER], "short": []} if tickers is None else tickers,
        spec=spec
        or {
            "params": {"fast": 10, "slow": 30},
            "param_bounds": {"fast": [2, 100], "slow": [5, 400]},
        },
        spec_hash="spec-hash-1",
        regime_sizing_modifier=None,
        kill_criteria=["world-changer thesis-broken"] if kill_criteria is None else kill_criteria,
        code_ref=None,
        created_at=None,
        updated_at=None,
    )


def _bars(closes: list[float], volume: float = 1.0e9) -> list[BarSample]:
    return [
        BarSample(
            session_date=_START + timedelta(days=i),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=volume,
        )
        for i, c in enumerate(closes)
    ]


def _uptrend_bars(n: int = 300) -> list[BarSample]:
    # Monotonic uptrend: the MA crossover goes long once and stays -> ~1 trade.
    return _bars([100.0 * (1.005**i) for i in range(n)])


def _square_wave_bars(period: int = 8, n: int = 1600) -> list[BarSample]:
    # Rises during the long phase and FALLS otherwise, so the square-wave signal
    # captures edge the benchmark cannot: it is out of the market exactly when
    # the market loses money.
    #
    # The off phase used to be flat (1.0), which made this fixture unable to
    # produce a legitimate promotion once benchmark-relative evaluation landed —
    # buy-and-hold captured every rise and gave nothing back, so the timing added
    # only its own costs and was correctly killed as `no-active-edge`. The old
    # fixture only "promoted" because absolute Sharpe cannot tell being invested
    # apart from being skilful.
    closes = [100.0]
    for i in range(1, n):
        long_phase = ((i - 1) // period) % 2 == 0
        closes.append(closes[-1] * (1.01 if long_phase else 0.995))
    return _bars(closes)


def _pipeline(
    *,
    registry: FakeRegistry,
    reader: FakeReader,
    store: FakeStore,
    redis: FakeRedis,
    card_root: Path,
    strategy_factory: Any = None,
    config: EvalConfig | None = None,
) -> EvaluationPipeline:
    return EvaluationPipeline(
        registry=registry,  # type: ignore[arg-type]
        reader=reader,  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        publisher=EventPublisher(redis),  # type: ignore[arg-type]
        config=config or EvalConfig(),
        card_root=card_root,
        clock=lambda: _FIXED_NOW,
        strategy_factory=strategy_factory,
    )


def _payload(redis: FakeRedis, index: int) -> dict[str, Any]:
    payload = json.loads(redis.fields[index]["payload"])
    assert isinstance(payload, dict)
    return payload


# --- spec hygiene refusals ---------------------------------------------------


async def test_bottleneck_rotation_is_refused(tmp_path: Path) -> None:
    registry = FakeRegistry(_record(archetype="bottleneck-rotation"))
    reader, store, redis = FakeReader(), FakeStore(), FakeRedis()
    pipeline = _pipeline(
        registry=registry, reader=reader, store=store, redis=redis, card_root=tmp_path
    )
    with pytest.raises(SpecHygieneError, match="bottleneck-rotation is not evaluable"):
        await pipeline.evaluate("01STRAT")
    assert registry.transitions == []
    assert store.inserted == []
    assert redis.streams == []


async def test_non_tier3_ticker_is_refused(tmp_path: Path) -> None:
    registry = FakeRegistry(_record())
    reader = FakeReader(tier="watch")
    store, redis = FakeStore(), FakeRedis()
    pipeline = _pipeline(
        registry=registry, reader=reader, store=store, redis=redis, card_root=tmp_path
    )
    with pytest.raises(SpecHygieneError, match="not Tier-3 eligible"):
        await pipeline.evaluate("01STRAT")


async def test_missing_kill_criteria_is_refused(tmp_path: Path) -> None:
    registry = FakeRegistry(_record(kill_criteria=[]))
    pipeline = _pipeline(
        registry=registry,
        reader=FakeReader(),
        store=FakeStore(),
        redis=FakeRedis(),
        card_root=tmp_path,
    )
    with pytest.raises(SpecHygieneError, match="kill criteria"):
        await pipeline.evaluate("01STRAT")


async def test_unbounded_param_is_refused(tmp_path: Path) -> None:
    registry = FakeRegistry(_record(spec={"params": {"fast": 10, "slow": 30}}))
    pipeline = _pipeline(
        registry=registry,
        reader=FakeReader(),
        store=FakeStore(),
        redis=FakeRedis(),
        card_root=tmp_path,
    )
    with pytest.raises(SpecHygieneError, match="bounds"):
        await pipeline.evaluate("01STRAT")


# --- anchor kill -------------------------------------------------------------


async def test_anchor_not_live_kills_without_running_engine(tmp_path: Path) -> None:
    registry = FakeRegistry(_record())
    reader = FakeReader(wc_status="at-risk")  # anchor not 'promoted'
    store, redis = FakeStore(), FakeRedis()
    pipeline = _pipeline(
        registry=registry, reader=reader, store=store, redis=redis, card_root=tmp_path
    )
    outcome = await pipeline.evaluate("01STRAT")
    assert outcome.verdict == VERDICT_KILL
    assert outcome.reason == REASON_ANCHOR_NOT_LIVE
    assert outcome.engine_ran is False
    assert outcome.to_stage == STATUS_KILLED

    await pipeline.commit(outcome)
    assert registry.transitions == [("01STRAT", STATUS_KILLED, STATUS_HYPOTHESIS)]
    assert redis.streams == [STREAM_STRATEGY_VERDICT, STREAM_STRATEGY_KILLED]


# --- trade-count kill (real reference strategy, end to end) -------------------


async def test_low_trade_strategy_is_killed(tmp_path: Path) -> None:
    registry = FakeRegistry(_record())
    reader = FakeReader(bars=_uptrend_bars())
    store, redis = FakeStore(), FakeRedis()
    pipeline = _pipeline(
        registry=registry, reader=reader, store=store, redis=redis, card_root=tmp_path
    )
    outcome = await pipeline.evaluate("01STRAT")
    assert outcome.engine_ran is True
    assert outcome.total_trades < 150
    assert outcome.verdict == VERDICT_KILL
    assert outcome.reason == REASON_INSUFFICIENT_TRADES

    result = await pipeline.commit(outcome)
    assert registry.transitions == [("01STRAT", STATUS_KILLED, STATUS_HYPOTHESIS)]
    assert redis.streams == [STREAM_STRATEGY_VERDICT, STREAM_STRATEGY_KILLED]
    assert Path(result.card_path).is_file()


# --- promote (injected promote-worthy signal) --------------------------------


async def test_promote_transitions_to_paper_and_publishes_verdict(tmp_path: Path) -> None:
    registry = FakeRegistry(_record())
    reader = FakeReader(bars=_square_wave_bars())
    store, redis = FakeStore(), FakeRedis()
    pipeline = _pipeline(
        registry=registry,
        reader=reader,
        store=store,
        redis=redis,
        card_root=tmp_path,
        strategy_factory=lambda record, tickers: SquareWaveSignal(tickers[0], 8),
    )
    outcome = await pipeline.evaluate("01STRAT")
    assert outcome.verdict == VERDICT_PROMOTE, outcome.summary()
    assert outcome.reason == REASON_PROMOTE
    assert outcome.total_trades >= 150
    assert outcome.base_sharpe >= pipeline_config_floor()
    assert outcome.stress_sharpe > 0
    assert outcome.to_stage == STATUS_PAPER

    result = await pipeline.commit(outcome)
    # Promotes hypothesis -> paper through the registry state machine.
    assert registry.transitions == [("01STRAT", STATUS_PAPER, STATUS_HYPOTHESIS)]
    # Verdict published; killed NOT published on a promote.
    assert redis.streams == [STREAM_STRATEGY_VERDICT]
    payload = _payload(redis, 0)
    assert set(payload) == {
        "strategy_id",
        "verdict",
        "from_stage",
        "to_stage",
        "metrics_ref",
        "trigger",
    }
    assert payload["verdict"] == VERDICT_PROMOTE
    assert payload["from_stage"] == STATUS_HYPOTHESIS
    assert payload["to_stage"] == STATUS_PAPER
    assert payload["metrics_ref"] == outcome.evaluation_id
    # Persisted exactly once.
    assert len(store.inserted) == 1
    assert store.inserted[0]["verdict"] == VERDICT_PROMOTE
    assert result.transitioned is True


def pipeline_config_floor() -> float:
    return EvalConfig().sharpe_floor


# --- evaluation card + dry-run purity ----------------------------------------


async def test_evaluation_card_has_required_wording(tmp_path: Path) -> None:
    registry = FakeRegistry(_record())
    reader = FakeReader(bars=_uptrend_bars())
    pipeline = _pipeline(
        registry=registry,
        reader=reader,
        store=FakeStore(),
        redis=FakeRedis(),
        card_root=tmp_path,
    )
    outcome = await pipeline.evaluate("01STRAT")
    assert "we have failed to disprove edge under our test protocol" in outcome.card_markdown
    assert REQUIRED_DISCLAIMER in outcome.card_markdown

    result = await pipeline.commit(outcome)
    written = Path(result.card_path).read_text(encoding="utf-8")
    assert "we have failed to disprove edge under our test protocol" in written


async def test_dry_run_evaluate_persists_nothing(tmp_path: Path) -> None:
    registry = FakeRegistry(_record())
    reader = FakeReader(bars=_square_wave_bars())
    store, redis = FakeStore(), FakeRedis()
    pipeline = _pipeline(
        registry=registry,
        reader=reader,
        store=store,
        redis=redis,
        card_root=tmp_path,
        strategy_factory=lambda record, tickers: SquareWaveSignal(tickers[0], 8),
    )
    # evaluate() is exactly what --dry-run runs: compute only, no commit().
    outcome = await pipeline.evaluate("01STRAT")
    assert outcome.verdict == VERDICT_PROMOTE
    assert registry.transitions == []
    assert store.inserted == []
    assert redis.streams == []
    # No card is written to disk without commit().
    assert not (tmp_path / "01STRAT").exists()


async def test_strategy_not_found_raises(tmp_path: Path) -> None:
    pipeline = _pipeline(
        registry=FakeRegistry(None),
        reader=FakeReader(),
        store=FakeStore(),
        redis=FakeRedis(),
        card_root=tmp_path,
    )
    with pytest.raises(Exception, match="not found"):
        await pipeline.evaluate("nope")


async def test_zero_cost_config_is_accepted() -> None:
    # Guards the CostModel override path used by callers/tests.
    cfg = EvalConfig(cost_model=CostModel(commission_bps=0.0))
    assert cfg.cost_model.commission_bps == 0.0


# --- archetype-conditional gates (ADR-0013) ----------------------------------


async def test_technical_catalyst_is_evaluated_without_an_anchor(tmp_path: Path) -> None:
    """The gate this card exists to open.

    Before ADR-0013's fix a `technical-catalyst` record never reached the anchor
    check at all: spec hygiene refused any archetype but `infra-graph-play`.
    """

    registry = FakeRegistry(_record(archetype="technical-catalyst", anchor={}))
    reader = FakeReader(bars=_uptrend_bars())
    store, redis = FakeStore(), FakeRedis()
    pipeline = _pipeline(
        registry=registry, reader=reader, store=store, redis=redis, card_root=tmp_path
    )
    outcome = await pipeline.evaluate("01STRAT")

    # The engine ran: the verdict is a measurement, not a gate artifact.
    assert outcome.engine_ran is True
    assert outcome.reason != REASON_ANCHOR_NOT_LIVE
    assert outcome.anchor_required is False
    # Not consulted at all — no query issued, not merely a result discarded.
    assert reader.wc_queries == []


async def test_technical_catalyst_can_reach_promote(tmp_path: Path) -> None:
    """Anchor-lessness must not block the promote branch either.

    A gate that only let anchor-less strategies through to a kill would be the
    same categorical exclusion wearing a different reason code.
    """

    registry = FakeRegistry(_record(archetype="technical-catalyst", anchor={}))
    reader = FakeReader(bars=_square_wave_bars())
    pipeline = _pipeline(
        registry=registry,
        reader=reader,
        store=FakeStore(),
        redis=FakeRedis(),
        card_root=tmp_path,
        strategy_factory=lambda record, tickers: SquareWaveSignal(tickers[0], 8),
    )
    outcome = await pipeline.evaluate("01STRAT")
    assert outcome.verdict == VERDICT_PROMOTE
    assert outcome.reason == REASON_PROMOTE
    assert outcome.to_stage == STATUS_PAPER


async def test_declared_anchor_on_an_anchorless_archetype_is_not_a_gate(tmp_path: Path) -> None:
    """A stale anchor on a technical strategy must not resurrect the gate.

    Records may carry a leftover anchor. The policy, not the payload, decides
    whether it gates — otherwise the archetype's exemption would depend on
    whoever wrote the row remembering to clear a field.
    """

    registry = FakeRegistry(
        _record(archetype="technical-catalyst", anchor={"world_changer_id": "01WC"})
    )
    reader = FakeReader(wc_status="rejected", bars=_uptrend_bars())
    pipeline = _pipeline(
        registry=registry,
        reader=reader,
        store=FakeStore(),
        redis=FakeRedis(),
        card_root=tmp_path,
    )
    outcome = await pipeline.evaluate("01STRAT")
    assert outcome.engine_ran is True
    assert outcome.reason != REASON_ANCHOR_NOT_LIVE
    assert reader.wc_queries == []


async def test_infra_graph_play_still_dies_on_a_dead_anchor(tmp_path: Path) -> None:
    """The regression that matters: Framework #1 keeps the gate it needs."""

    registry = FakeRegistry(_record(archetype="infra-graph-play"))
    reader = FakeReader(wc_status="at-risk", bars=_uptrend_bars())
    pipeline = _pipeline(
        registry=registry,
        reader=reader,
        store=FakeStore(),
        redis=FakeRedis(),
        card_root=tmp_path,
    )
    outcome = await pipeline.evaluate("01STRAT")
    assert outcome.verdict == VERDICT_KILL
    assert outcome.reason == REASON_ANCHOR_NOT_LIVE
    assert outcome.engine_ran is False
    assert outcome.anchor_required is True
    assert reader.wc_queries == ["01WC"]


async def test_unknown_archetype_is_refused_not_silently_ungated(tmp_path: Path) -> None:
    """Fail closed. An archetype with no declared policy is not evaluable."""

    registry = FakeRegistry(_record(archetype="fragility-cascade"))
    pipeline = _pipeline(
        registry=registry,
        reader=FakeReader(),
        store=FakeStore(),
        redis=FakeRedis(),
        card_root=tmp_path,
    )
    with pytest.raises(SpecHygieneError, match="no declared evaluation policy"):
        await pipeline.evaluate("01STRAT")


async def test_card_and_summary_distinguish_not_required_from_not_live(tmp_path: Path) -> None:
    """`anchor_fresh=False` means two different things; the card must not blur them.

    Rendering an anchor-less strategy as "not live" would report a falsified
    thesis for a strategy that never claimed one.
    """

    registry = FakeRegistry(_record(archetype="technical-catalyst", anchor={}))
    pipeline = _pipeline(
        registry=registry,
        reader=FakeReader(bars=_uptrend_bars()),
        store=FakeStore(),
        redis=FakeRedis(),
        card_root=tmp_path,
    )
    outcome = await pipeline.evaluate("01STRAT")
    assert "not required" in outcome.card_markdown
    assert "not live" not in outcome.card_markdown
    assert "anchor=not-required" in outcome.summary()
    # The number the promote gate turns on has to be legible in the one line an
    # operator actually reads, not inferable from gate ordering.
    assert "ir=" in outcome.summary()

    dead = FakeRegistry(_record(archetype="infra-graph-play"))
    dead_pipeline = _pipeline(
        registry=dead,
        reader=FakeReader(wc_status="at-risk"),
        store=FakeStore(),
        redis=FakeRedis(),
        card_root=tmp_path,
    )
    dead_outcome = await dead_pipeline.evaluate("01STRAT")
    assert "anchor=not-live" in dead_outcome.summary()
    assert "not required" not in dead_outcome.card_markdown


async def test_anchor_required_is_persisted_with_the_evaluation(tmp_path: Path) -> None:
    """The ledger must be able to tell the two False cases apart after the fact."""

    registry = FakeRegistry(_record(archetype="technical-catalyst", anchor={}))
    store = FakeStore()
    pipeline = _pipeline(
        registry=registry,
        reader=FakeReader(bars=_uptrend_bars()),
        store=store,
        redis=FakeRedis(),
        card_root=tmp_path,
    )
    outcome = await pipeline.evaluate("01STRAT")
    await pipeline.commit(outcome)
    assert store.inserted[0]["anchor_required"] is False
    assert store.inserted[0]["anchor_fresh"] is False


# --- the promote review gate (ADR-0015) --------------------------------------


async def test_promote_requires_review_records_everything_but_the_transition(
    tmp_path: Path,
) -> None:
    """A held promote must still be fully auditable — only the move is withheld."""

    registry = FakeRegistry(_record())
    store, redis = FakeStore(), FakeRedis()
    pipeline = _pipeline(
        registry=registry,
        reader=FakeReader(bars=_square_wave_bars()),
        store=store,
        redis=redis,
        card_root=tmp_path,
        strategy_factory=lambda record, tickers: SquareWaveSignal(tickers[0], 8),
    )
    outcome = await pipeline.evaluate("01STRAT")
    assert outcome.verdict == VERDICT_PROMOTE

    result = await pipeline.commit(outcome, promote_requires_review=True)

    assert result.promotion_held is True
    assert result.transitioned is False
    assert registry.transitions == []  # the strategy is still at hypothesis
    assert len(store.inserted) == 1  # but the ledger row exists
    assert Path(result.card_path).exists()  # and so does the card


async def test_a_held_promote_never_publishes_the_verdict_stream(tmp_path: Path) -> None:
    """The gate's one real failure mode, and the reason it is asserted here.

    The Strategy Librarian consumes `research.strategy.verdict` and applies the
    transition itself. Publishing a promote verdict while withholding the
    Evaluator's own transition would promote the strategy anyway, one hop later
    — a review gate that holds nothing.
    """

    registry = FakeRegistry(_record())
    redis = FakeRedis()
    pipeline = _pipeline(
        registry=registry,
        reader=FakeReader(bars=_square_wave_bars()),
        store=FakeStore(),
        redis=redis,
        card_root=tmp_path,
        strategy_factory=lambda record, tickers: SquareWaveSignal(tickers[0], 8),
    )
    outcome = await pipeline.evaluate("01STRAT")
    result = await pipeline.commit(outcome, promote_requires_review=True)

    assert STREAM_STRATEGY_VERDICT not in redis.streams
    assert STREAM_STRATEGY_KILLED not in redis.streams
    assert redis.streams == [STREAM_STRATEGY_PROMOTION_PENDING]
    assert result.streams == [STREAM_STRATEGY_PROMOTION_PENDING]

    payload = _payload(redis, 0)
    assert payload["strategy_id"] == "01STRAT"
    # `recommended_stage`, not `to_stage`: the strategy did not go there.
    assert payload["recommended_stage"] == STATUS_PAPER
    assert "shrap-strategy-evaluate --strategy-id 01STRAT" in str(payload["review_command"])


async def test_the_review_gate_does_not_touch_kills(tmp_path: Path) -> None:
    """Asymmetric by design: kills apply unattended, promotes do not."""

    registry = FakeRegistry(_record())
    redis = FakeRedis()
    pipeline = _pipeline(
        registry=registry,
        reader=FakeReader(bars=_uptrend_bars()),
        store=FakeStore(),
        redis=redis,
        card_root=tmp_path,
    )
    outcome = await pipeline.evaluate("01STRAT")
    assert outcome.verdict == VERDICT_KILL

    result = await pipeline.commit(outcome, promote_requires_review=True)

    assert result.promotion_held is False
    assert result.transitioned is True
    assert registry.transitions == [("01STRAT", STATUS_KILLED, STATUS_HYPOTHESIS)]
    assert redis.streams == [STREAM_STRATEGY_VERDICT, STREAM_STRATEGY_KILLED]


async def test_the_gate_is_off_by_default_so_the_manual_cli_still_promotes(
    tmp_path: Path,
) -> None:
    """Running the CLI by hand IS the review; it must not need a second step."""

    registry = FakeRegistry(_record())
    pipeline = _pipeline(
        registry=registry,
        reader=FakeReader(bars=_square_wave_bars()),
        store=FakeStore(),
        redis=FakeRedis(),
        card_root=tmp_path,
        strategy_factory=lambda record, tickers: SquareWaveSignal(tickers[0], 8),
    )
    outcome = await pipeline.evaluate("01STRAT")
    result = await pipeline.commit(outcome)

    assert result.promotion_held is False
    assert result.transitioned is True
    assert registry.transitions == [("01STRAT", STATUS_PAPER, STATUS_HYPOTHESIS)]


def _outcome_with(active: dict[str, object], **over: object):
    """A minimal EvaluationOutcome; only the summary fields matter here."""

    from datetime import UTC, datetime

    from shrap.research.strategy_evaluator.pipeline import EvaluationOutcome

    fields: dict[str, object] = {
        "evaluation_id": "01EVAL",
        "strategy_id": "01TEST",
        "strategy_name": "test",
        "spec_hash": "hash",
        "protocol_version": "0.1",
        "verdict": "hold-for-data",
        "reason": "below-sharpe-floor",
        "anchor_required": False,
        "anchor_fresh": False,
        "anchor_status": None,
        "total_trades": 641,
        "base_sharpe": 0.797,
        "stress_sharpe": 0.592,
        "from_stage": "hypothesis",
        "to_stage": None,
        "engine_ran": True,
        "aggregate_metrics": {},
        "fold_metrics": [],
        "stress_metrics": {},
        "active_metrics": active,
        "config": {},
        "trigger": "manual",
        "ts": datetime(2026, 7, 29, tzinfo=UTC),
        "card_markdown": "",
    }
    fields.update(over)
    return EvaluationOutcome(**fields)  # type: ignore[arg-type]


def test_the_summary_reports_the_information_ratio() -> None:
    """The first real verdict on the Dell read

        hold-for-data (below-sharpe-floor): ... sharpe=0.797 stress_sharpe=0.592

    and the question it left open — did it beat equal-weight buy-and-hold? — was
    answerable only by reasoning backwards from gate ordering (`no-active-edge`
    fires before `below-sharpe-floor`, so surviving to the latter proves IR > 0).
    Correct, and a ridiculous way to read a number the run already computed.
    """

    assert "ir=0.420" in _outcome_with({"information_ratio": 0.42}).summary()


def test_a_missing_benchmark_reports_na_not_zero() -> None:
    """A benchmark that did not run and a benchmark the strategy exactly matched
    are different facts. Printing 0.000 for both would conflate them."""

    assert "ir=n/a" in _outcome_with({}, engine_ran=False).summary()


def _launch_list_coverage():
    """The real 50-name shape under the ragged panel: full span, thin at the start."""

    from shrap.research.strategy_evaluator.strategy import PanelCoverage, TickerCoverage

    return PanelCoverage(
        n_bars=1303,
        fully_covered=525,
        first_date=date(2021, 7, 29),
        last_date=date(2026, 7, 27),
        per_ticker=(
            TickerCoverage("SPY", 1303, 0, date(2021, 7, 29), date(2026, 7, 27)),
            TickerCoverage("IBIT", 663, 640, date(2024, 1, 11), date(2026, 7, 27)),
            TickerCoverage("ETHA", 525, 778, date(2024, 7, 23), date(2026, 7, 27)),
        ),
    )


def test_the_summary_reports_how_much_history_the_test_had() -> None:
    """Every gate in the protocol is a claim about a sample, and the sample size
    was the one number the verdict never carried.

    The first cross-sectional run held on the Sharpe floor at 0.797. Whether that
    is a weak edge measured over five years or a coin-flip measured over two
    changes what to do about it, and nothing in the output distinguished them.
    """

    summary = _outcome_with({"information_ratio": 0.42}, coverage=_launch_list_coverage()).summary()

    assert "bars=1303" in summary
    # The span alone would read as "the universe looked like this throughout".
    assert "complete=525" in summary
    assert "thinnest=ETHA" in summary


def test_a_run_that_never_built_a_panel_reports_na_not_zero() -> None:
    """A spec refusal and a dead anchor stop before the dataset is fetched.
    Reporting `bars=0` there would read as an empty backfill — an operator would
    go check the market-data service for a fault that does not exist."""

    assert "bars=n/a" in _outcome_with({}, engine_ran=False).summary()


def test_the_card_explains_how_thin_the_early_universe_was() -> None:
    """The summary names the thinnest ticker; the card shows the arithmetic,
    which is where an operator goes once the one-liner surprises them."""

    from shrap.research.strategy_evaluator.pipeline import render_evaluation_card

    card = render_evaluation_card(
        _outcome_with({"information_ratio": 0.42}, coverage=_launch_list_coverage())
    )

    assert "## Panel coverage" in card
    assert "complete for 525 of 1303 bars" in card
    assert "2021-07-29 to 2026-07-27" in card
    # Ranked thinnest-first, so the name to look at is the first row.
    assert card.index("| ETHA |") < card.index("| IBIT |")
    # A fully-covered ticker is not noise worth printing.
    assert "| SPY |" not in card


def test_the_card_omits_the_coverage_section_when_no_panel_was_built() -> None:
    """A refusal card should not carry an empty table implying a data problem."""

    from shrap.research.strategy_evaluator.pipeline import render_evaluation_card

    card = render_evaluation_card(_outcome_with({}, engine_ran=False))
    assert "## Panel coverage" not in card


class RaggedReader(FakeReader):
    """A reader whose tickers have different histories, as the real one does."""

    def __init__(self, bars_by_ticker: dict[str, list[BarSample]]) -> None:
        super().__init__()
        self._bars_by_ticker = bars_by_ticker

    async def read_bars(
        self, ticker: str, start: date, end: date, adjustment: str
    ) -> list[BarSample]:
        return list(self._bars_by_ticker.get(ticker, []))


async def test_evaluate_reports_coverage_end_to_end(tmp_path: Path) -> None:
    """The wiring test that matters.

    `coverage` defaults to None so refusal paths stay honest, which means a
    missed hand-off in `_build_panel` would not fail a type check or a unit
    test — it would just print `bars=n/a` on every real run forever, exactly
    the silence this card exists to remove.
    """

    from shrap.research.strategy_evaluator.pipeline import render_evaluation_card

    full = _square_wave_bars()
    late = full[len(full) // 2 :]
    registry = FakeRegistry(_record(tickers={"long": [_TICKER, "LATE"], "short": []}))
    pipeline = _pipeline(
        registry=registry,
        reader=RaggedReader({_TICKER: full, "LATE": late}),
        store=FakeStore(),
        redis=FakeRedis(),
        card_root=tmp_path,
        strategy_factory=lambda record, tickers: SquareWaveSignal(tickers[0], 8),
    )

    outcome = await pipeline.evaluate("01STRAT")

    assert outcome.coverage is not None
    # The ragged panel end-to-end: LATE has half the history and costs the panel
    # NOTHING. Under the intersection this ran on `len(late)` bars.
    assert outcome.coverage.n_bars == len(full)
    assert outcome.coverage.fully_covered == len(late)
    assert "bars=" in outcome.summary()
    assert "thinnest=LATE" in outcome.summary()
    # And it survives the frozen-dataclass rebuild that attaches the card.
    assert "## Panel coverage" in render_evaluation_card(outcome)
