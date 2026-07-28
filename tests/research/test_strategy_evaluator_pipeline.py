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
    # Rises only during the long phase -> the square-wave signal captures edge
    # and trades on every phase flip (~150+ trades over the window).
    closes = [100.0]
    for i in range(1, n):
        long_phase = ((i - 1) // period) % 2 == 0
        closes.append(closes[-1] * (1.01 if long_phase else 1.0))
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
