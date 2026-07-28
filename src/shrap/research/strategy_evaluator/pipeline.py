"""The evaluation pipeline: spec hygiene → anchor → dataset → engine → verdict.

This wraps the deterministic engine with the state and I/O the spec requires
(Processing, evaluation pipeline). It is split into two phases so ``--dry-run``
is exact:

- :meth:`EvaluationPipeline.evaluate` — pure compute. Reads the strategy, runs
  hygiene, checks anchor freshness, builds the dataset, runs the walk-forward,
  and maps the verdict. Returns an :class:`EvaluationOutcome`. **No writes.**
- :meth:`EvaluationPipeline.commit` — the side effects: the registry status
  transition (``hypothesis → paper`` on promote, ``hypothesis → killed`` on
  kill), the append-only ``research.evaluations`` row, the Markdown evaluation
  card, and the ``research.strategy.verdict`` / ``research.strategy.killed``
  events. ``--dry-run`` simply never calls this.

Failure taxonomy (deliberate — see ``docs/research/eval-protocol.md``):

- **Refusal** (:class:`SpecHygieneError`): the spec is malformed or not yet
  evaluable (wrong/deferred archetype, non-Tier-3 ticker, unbounded params,
  no kill criteria, regime used as a gate). Nothing is written; the strategy
  stays at ``hypothesis`` so it can be fixed or wait for the deferred
  dependency (e.g. Bottleneck Scout). A refusal is *not* a kill: a proposal we
  never evaluated has not earned a terminal verdict.
- **Kill verdict**: something we *did* evaluate is dead — a broken anchor, too
  few trades, no edge, or edge that dies under friction. Transitions to
  ``killed``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import structlog
from ulid import ULID

from shrap.events import EventPublisher
from shrap.research.strategy_evaluator.engine import (
    PROTOCOL_VERSION,
    EvalConfig,
    InsufficientDataError,
    walk_forward,
)
from shrap.research.strategy_evaluator.reference_strategy import ReferenceTrendStrategy
from shrap.research.strategy_evaluator.strategy import BarSample, PricePanel, StrategySignal
from shrap.research.strategy_evaluator.verdict import (
    REASON_INSUFFICIENT_DATA,
    VERDICT_HOLD,
    VERDICT_KILL,
    VERDICT_PROMOTE,
    Verdict,
    map_verdict,
)
from shrap.research.strategy_registry import (
    STATUS_HYPOTHESIS,
    STATUS_KILLED,
    STATUS_PAPER,
    STREAM_STRATEGY_KILLED,
    StrategyRecord,
    StrategyTransition,
)

log = structlog.get_logger(__name__)

PRODUCED_BY = "strategy-evaluator"
SCHEMA_VERSION = "1.0.0"
DEFAULT_TRIGGER = "on-demand"

STREAM_STRATEGY_VERDICT = "research.strategy.verdict"

# ADR-0015: where an unattended promote goes instead of the verdict stream.
# Nothing consumes this to apply a transition — that is the whole point.
STREAM_STRATEGY_PROMOTION_PENDING = "research.strategy.promotion-pending"

# The only archetype this card can evaluate; bottleneck-rotation is deferred
# until Bottleneck Scout populates research.bottlenecks (resequencing ruling).
ARCHETYPE_INFRA_GRAPH_PLAY = "infra-graph-play"
ARCHETYPE_BOTTLENECK_ROTATION = "bottleneck-rotation"

# World-changer anchor is referenced by one of these keys in the strategy's
# anchor JSONB (seam convention — see the protocol doc).
ANCHOR_WORLD_CHANGER_KEYS = ("world_changer_id", "candidate_id")
WORLD_CHANGER_LIVE_STATUS = "promoted"
TIER_ACTIVE = "active"

DEFAULT_CARD_ROOT = Path("docs/strategies/evaluations")

# The record → signal-code binding. Until the strategy-authoring system exists
# (a DSL / plugin registry — deferred), an infra-graph-play record is evaluated
# by instantiating the REFERENCE trend rule from its params. This factory is the
# seam that later card replaces; tests inject their own signal through it.
StrategyFactory = Callable[[StrategyRecord, list[str]], StrategySignal]


def _default_strategy_factory(record: StrategyRecord, tickers: list[str]) -> StrategySignal:
    return ReferenceTrendStrategy.from_spec(tickers[0], _params(record.spec))


# The exact wording the spec requires in every evaluation report.
REQUIRED_DISCLAIMER = (
    "Passing these tests means we have failed to disprove edge under our test "
    "protocol, not that edge is real."
)


class EvaluationError(Exception):
    """The evaluation cannot proceed (strategy missing or in the wrong stage)."""


class SpecHygieneError(EvaluationError):
    """The spec is malformed or not yet evaluable — refused, fail-closed."""


class RegistryPort(Protocol):
    async def get(self, strategy_id: str) -> StrategyRecord | None: ...

    async def transition(
        self,
        strategy_id: str,
        to_status: str,
        *,
        reason: str,
        trigger_kind: str,
        actor: str,
        trigger_ref: str | None = ...,
        expected_from: str | None = ...,
    ) -> StrategyTransition: ...


class ReaderPort(Protocol):
    async def world_changer_status(self, candidate_id: str) -> str | None: ...

    async def ticker_tier(self, ticker: str) -> str | None: ...

    async def read_bars(
        self, ticker: str, start: date, end: date, adjustment: str
    ) -> list[BarSample]: ...


class EvaluationStorePort(Protocol):
    async def insert_evaluation(
        self,
        *,
        evaluation_id: str,
        strategy_id: str,
        spec_hash: str,
        protocol_version: str,
        verdict: str,
        reason: str,
        anchor_fresh: bool,
        total_trades: int,
        from_stage: str,
        to_stage: str | None,
        aggregate_metrics: dict[str, Any],
        fold_metrics: list[dict[str, Any]],
        stress_metrics: dict[str, Any],
        config: dict[str, Any],
        card_path: str | None,
        trigger: str,
        created_at: datetime,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    """The pure result of :meth:`EvaluationPipeline.evaluate` — no side effects."""

    evaluation_id: str
    strategy_id: str
    strategy_name: str
    spec_hash: str
    protocol_version: str
    verdict: str
    reason: str
    anchor_fresh: bool
    anchor_status: str | None
    total_trades: int
    base_sharpe: float
    stress_sharpe: float
    from_stage: str
    to_stage: str | None
    engine_ran: bool
    aggregate_metrics: dict[str, Any]
    fold_metrics: list[dict[str, Any]]
    stress_metrics: dict[str, Any]
    config: dict[str, Any]
    trigger: str
    ts: datetime
    card_markdown: str

    def summary(self) -> str:
        return (
            f"{self.verdict} ({self.reason}): {self.strategy_id} "
            f"[{self.from_stage} -> {self.to_stage or self.from_stage}] "
            f"trades={self.total_trades} sharpe={self.base_sharpe:.3f} "
            f"stress_sharpe={self.stress_sharpe:.3f} protocol={self.protocol_version}"
        )


@dataclass(frozen=True, slots=True)
class CommitResult:
    """What :meth:`EvaluationPipeline.commit` did."""

    evaluation_id: str
    transitioned: bool
    to_stage: str | None
    card_path: str
    streams: list[str] = field(default_factory=list)
    promotion_held: bool = False
    """A ``promote`` verdict was recorded but not applied — ADR-0015's review
    gate. The strategy is still at ``hypothesis``; ``to_stage`` is what the
    verdict *recommended*, not where the strategy now sits."""


def _extract_tickers(tickers: object) -> list[str]:
    """Ordered, de-duplicated tickers from a strategy record's ``tickers`` blob."""

    collected: list[str] = []
    if isinstance(tickers, Mapping):
        for key in ("long", "short"):
            vals = tickers.get(key)
            if isinstance(vals, list):
                collected += [str(v) for v in vals]
        if not collected:
            collected += [str(k) for k in tickers]
    elif isinstance(tickers, list):
        collected += [str(v) for v in tickers]
    seen: set[str] = set()
    ordered: list[str] = []
    for t in collected:
        sym = t.strip().upper()
        if sym and sym not in seen:
            seen.add(sym)
            ordered.append(sym)
    return ordered


def _anchor_world_changer_id(anchor: Mapping[str, Any] | None) -> str | None:
    if not anchor:
        return None
    for key in ANCHOR_WORLD_CHANGER_KEYS:
        value = anchor.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _empty_metrics() -> dict[str, Any]:
    return {
        "total_return": 0.0,
        "sharpe": 0.0,
        "max_drawdown": 0.0,
        "trade_count": 0,
        "n_periods": 0,
    }


class EvaluationPipeline:
    """Evaluate one strategy end to end (compute) and, separately, commit it."""

    def __init__(
        self,
        *,
        registry: RegistryPort,
        reader: ReaderPort,
        store: EvaluationStorePort,
        publisher: EventPublisher,
        config: EvalConfig | None = None,
        card_root: Path = DEFAULT_CARD_ROOT,
        clock: Callable[[], datetime] | None = None,
        strategy_factory: StrategyFactory | None = None,
    ) -> None:
        self._registry = registry
        self._reader = reader
        self._store = store
        self._publisher = publisher
        self._config = config or EvalConfig()
        self._card_root = card_root
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._strategy_factory: StrategyFactory = strategy_factory or _default_strategy_factory

    async def evaluate(
        self, strategy_id: str, *, trigger: str = DEFAULT_TRIGGER
    ) -> EvaluationOutcome:
        record = await self._registry.get(strategy_id)
        if record is None:
            raise EvaluationError(f"strategy {strategy_id} not found")
        if record.status != STATUS_HYPOTHESIS:
            raise EvaluationError(
                f"strategy {strategy_id} is {record.status!r}; this card evaluates "
                f"only {STATUS_HYPOTHESIS!r}-stage strategies"
            )

        tickers = self._check_spec_hygiene(record)
        await self._check_tickers_tradeable(tickers)

        anchor_id = _anchor_world_changer_id(record.anchor)
        anchor_status = await self._reader.world_changer_status(anchor_id) if anchor_id else None
        anchor_fresh = anchor_status == WORLD_CHANGER_LIVE_STATUS

        ts = self._clock()
        if not anchor_fresh:
            verdict = map_verdict(
                anchor_fresh=False,
                total_trades=0,
                base_sharpe=0.0,
                stress_sharpe=0.0,
                min_trades=self._config.min_trades,
                sharpe_floor=self._config.sharpe_floor,
            )
            return self._build_outcome(
                record=record,
                verdict=verdict,
                anchor_fresh=False,
                anchor_status=anchor_status,
                engine_ran=False,
                total_trades=0,
                base_sharpe=0.0,
                stress_sharpe=0.0,
                aggregate_metrics=_empty_metrics(),
                fold_metrics=[],
                stress_metrics=_empty_metrics(),
                trigger=trigger,
                ts=ts,
            )

        panel = await self._build_panel(tickers, ts.date())
        strategy = self._strategy_factory(record, tickers)
        try:
            result = walk_forward(panel, strategy, self._config)
        except InsufficientDataError:
            verdict = Verdict(VERDICT_HOLD, REASON_INSUFFICIENT_DATA)
            return self._build_outcome(
                record=record,
                verdict=verdict,
                anchor_fresh=True,
                anchor_status=anchor_status,
                engine_ran=False,
                total_trades=0,
                base_sharpe=0.0,
                stress_sharpe=0.0,
                aggregate_metrics=_empty_metrics(),
                fold_metrics=[],
                stress_metrics=_empty_metrics(),
                trigger=trigger,
                ts=ts,
            )

        verdict = map_verdict(
            anchor_fresh=True,
            total_trades=result.aggregate.trade_count,
            base_sharpe=result.aggregate.sharpe,
            stress_sharpe=result.stress.sharpe,
            min_trades=self._config.min_trades,
            sharpe_floor=self._config.sharpe_floor,
        )
        return self._build_outcome(
            record=record,
            verdict=verdict,
            anchor_fresh=True,
            anchor_status=anchor_status,
            engine_ran=True,
            total_trades=result.aggregate.trade_count,
            base_sharpe=result.aggregate.sharpe,
            stress_sharpe=result.stress.sharpe,
            aggregate_metrics=result.aggregate.as_dict(),
            fold_metrics=[f.as_dict() for f in result.folds],
            stress_metrics=result.stress.as_dict(),
            trigger=trigger,
            ts=ts,
        )

    async def commit(
        self, outcome: EvaluationOutcome, *, promote_requires_review: bool = False
    ) -> CommitResult:
        """Apply the verdict: transition, persist, write the card, publish.

        ``promote_requires_review`` implements ADR-0015's asymmetry. With it
        set, a ``promote`` verdict is fully *recorded* — card, evaluation row,
        a ``promotion-pending`` event — but the registry transition is not
        applied, so the strategy stays at ``hypothesis`` and does not reach the
        Strategy Runner's trading path. Kills and holds are unaffected.

        It defaults to ``False`` because the manual CLI *is* the review: a human
        running ``shrap-strategy-evaluate`` has already made the decision the
        gate exists to require. Only the unattended trigger passes ``True``.
        """

        card_path = write_evaluation_card(
            self._card_root, outcome.strategy_id, outcome.ts, outcome.card_markdown
        )
        promotion_held = promote_requires_review and outcome.verdict == VERDICT_PROMOTE
        transitioned = False
        if outcome.to_stage is not None and not promotion_held:
            await self._registry.transition(
                outcome.strategy_id,
                outcome.to_stage,
                reason=(
                    f"{outcome.verdict}: {outcome.reason} "
                    f"(protocol {outcome.protocol_version}, "
                    f"sharpe={outcome.base_sharpe:.3f}, trades={outcome.total_trades})"
                ),
                trigger_kind="evaluation",
                actor=PRODUCED_BY,
                trigger_ref=outcome.evaluation_id,
                expected_from=outcome.from_stage,
            )
            transitioned = True

        await self._store.insert_evaluation(
            evaluation_id=outcome.evaluation_id,
            strategy_id=outcome.strategy_id,
            spec_hash=outcome.spec_hash,
            protocol_version=outcome.protocol_version,
            verdict=outcome.verdict,
            reason=outcome.reason,
            anchor_fresh=outcome.anchor_fresh,
            total_trades=outcome.total_trades,
            from_stage=outcome.from_stage,
            to_stage=outcome.to_stage,
            aggregate_metrics=outcome.aggregate_metrics,
            fold_metrics=outcome.fold_metrics,
            stress_metrics=outcome.stress_metrics,
            config=outcome.config,
            card_path=str(card_path),
            trigger=outcome.trigger,
            created_at=outcome.ts,
        )

        streams = await self._publish(outcome, promotion_held=promotion_held)
        log.info(
            "strategy_evaluator.committed",
            strategy_id=outcome.strategy_id,
            verdict=outcome.verdict,
            reason=outcome.reason,
            to_stage=outcome.to_stage,
            evaluation_id=outcome.evaluation_id,
            promotion_held=promotion_held,
        )
        return CommitResult(
            evaluation_id=outcome.evaluation_id,
            transitioned=transitioned,
            to_stage=outcome.to_stage,
            card_path=str(card_path),
            streams=streams,
            promotion_held=promotion_held,
        )

    async def _publish(
        self, outcome: EvaluationOutcome, *, promotion_held: bool = False
    ) -> list[str]:
        if promotion_held:
            # Deliberately NOT research.strategy.verdict. The Strategy Librarian
            # consumes that stream and applies the transition itself, so
            # publishing a promote verdict here would promote the strategy
            # through the back door — the gate would hold the Evaluator's own
            # transition and nothing else. This stream has no such consumer.
            await self._publisher.publish(
                stream=STREAM_STRATEGY_PROMOTION_PENDING,
                produced_by=PRODUCED_BY,
                schema_version=SCHEMA_VERSION,
                payload={
                    "strategy_id": outcome.strategy_id,
                    "strategy_name": outcome.strategy_name,
                    "verdict": outcome.verdict,
                    "reason": outcome.reason,
                    "from_stage": outcome.from_stage,
                    "recommended_stage": outcome.to_stage,
                    "metrics_ref": outcome.evaluation_id,
                    "trigger": outcome.trigger,
                    "total_trades": outcome.total_trades,
                    "base_sharpe": outcome.base_sharpe,
                    "stress_sharpe": outcome.stress_sharpe,
                    # Reviewing IS re-running the manual CLI: it defaults to
                    # promote_requires_review=False, so a human running this
                    # applies the promotion. No separate approval tool exists,
                    # and adding one would be a second path to the same effect.
                    "review_command": (
                        f"shrap-strategy-evaluate --strategy-id {outcome.strategy_id}"
                    ),
                },
            )
            return [STREAM_STRATEGY_PROMOTION_PENDING]

        streams: list[str] = []
        await self._publisher.publish(
            stream=STREAM_STRATEGY_VERDICT,
            produced_by=PRODUCED_BY,
            schema_version=SCHEMA_VERSION,
            payload={
                "strategy_id": outcome.strategy_id,
                "verdict": outcome.verdict,
                "from_stage": outcome.from_stage,
                "to_stage": outcome.to_stage,
                "metrics_ref": outcome.evaluation_id,
                "trigger": outcome.trigger,
            },
        )
        streams.append(STREAM_STRATEGY_VERDICT)
        if outcome.verdict == VERDICT_KILL:
            await self._publisher.publish(
                stream=STREAM_STRATEGY_KILLED,
                produced_by=PRODUCED_BY,
                schema_version=SCHEMA_VERSION,
                payload={
                    "strategy_id": outcome.strategy_id,
                    "verdict": outcome.verdict,
                    "reason": outcome.reason,
                    "from_stage": outcome.from_stage,
                    "to_stage": outcome.to_stage,
                    "metrics_ref": outcome.evaluation_id,
                    "trigger": outcome.trigger,
                },
            )
            streams.append(STREAM_STRATEGY_KILLED)
        return streams

    def _check_spec_hygiene(self, record: StrategyRecord) -> list[str]:
        if record.archetype == ARCHETYPE_BOTTLENECK_ROTATION:
            raise SpecHygieneError(
                "bottleneck-rotation is not evaluable yet: research.bottlenecks has no "
                "rows until Bottleneck Scout exists (resequencing ruling 2026-07-23)"
            )
        if record.archetype != ARCHETYPE_INFRA_GRAPH_PLAY:
            raise SpecHygieneError(
                f"archetype {record.archetype!r} is not evaluable; this card evaluates "
                f"only {ARCHETYPE_INFRA_GRAPH_PLAY!r}"
            )
        tickers = _extract_tickers(record.tickers)
        if not tickers:
            raise SpecHygieneError("strategy declares no tickers")
        if not record.kill_criteria:
            raise SpecHygieneError("kill criteria are not declared")
        spec = record.spec if isinstance(record.spec, Mapping) else {}
        if spec.get("regime_gate"):
            raise SpecHygieneError(
                "regime must be a sizing modifier, not an entry/exit gate "
                "(found spec['regime_gate'])"
            )
        _validate_param_bounds(spec)
        return tickers

    async def _check_tickers_tradeable(self, tickers: Sequence[str]) -> None:
        for ticker in tickers:
            tier = await self._reader.ticker_tier(ticker)
            if tier != TIER_ACTIVE:
                raise SpecHygieneError(
                    f"ticker {ticker} is not Tier-3 eligible "
                    f"(research.universe_tiers tier={tier!r}, need {TIER_ACTIVE!r})"
                )

    async def _build_panel(self, tickers: Sequence[str], today: date) -> PricePanel:
        start = today - timedelta(days=self._config.window_years * 365)
        bars_by_ticker: dict[str, list[BarSample]] = {}
        for ticker in tickers:
            bars = await self._reader.read_bars(ticker, start, today, self._config.adjustment)
            bars_by_ticker[ticker] = bars
        return PricePanel.from_bars(bars_by_ticker)

    def _build_outcome(
        self,
        *,
        record: StrategyRecord,
        verdict: Verdict,
        anchor_fresh: bool,
        anchor_status: str | None,
        engine_ran: bool,
        total_trades: int,
        base_sharpe: float,
        stress_sharpe: float,
        aggregate_metrics: dict[str, Any],
        fold_metrics: list[dict[str, Any]],
        stress_metrics: dict[str, Any],
        trigger: str,
        ts: datetime,
    ) -> EvaluationOutcome:
        to_stage = _verdict_to_stage(verdict.verdict)
        evaluation_id = str(ULID())
        config = self._config.as_dict()
        outcome = EvaluationOutcome(
            evaluation_id=evaluation_id,
            strategy_id=record.strategy_id,
            strategy_name=record.name,
            spec_hash=record.spec_hash,
            protocol_version=PROTOCOL_VERSION,
            verdict=verdict.verdict,
            reason=verdict.reason,
            anchor_fresh=anchor_fresh,
            anchor_status=anchor_status,
            total_trades=total_trades,
            base_sharpe=base_sharpe,
            stress_sharpe=stress_sharpe,
            from_stage=STATUS_HYPOTHESIS,
            to_stage=to_stage,
            engine_ran=engine_ran,
            aggregate_metrics=aggregate_metrics,
            fold_metrics=fold_metrics,
            stress_metrics=stress_metrics,
            config=config,
            trigger=trigger,
            ts=ts,
            card_markdown="",
        )
        return _with_card(outcome, render_evaluation_card(outcome))


def _verdict_to_stage(verdict: str) -> str | None:
    if verdict == VERDICT_PROMOTE:
        return STATUS_PAPER
    if verdict == VERDICT_KILL:
        return STATUS_KILLED
    return None


def _with_card(outcome: EvaluationOutcome, card: str) -> EvaluationOutcome:
    # EvaluationOutcome is frozen; rebuild with the rendered card attached.
    return EvaluationOutcome(
        evaluation_id=outcome.evaluation_id,
        strategy_id=outcome.strategy_id,
        strategy_name=outcome.strategy_name,
        spec_hash=outcome.spec_hash,
        protocol_version=outcome.protocol_version,
        verdict=outcome.verdict,
        reason=outcome.reason,
        anchor_fresh=outcome.anchor_fresh,
        anchor_status=outcome.anchor_status,
        total_trades=outcome.total_trades,
        base_sharpe=outcome.base_sharpe,
        stress_sharpe=outcome.stress_sharpe,
        from_stage=outcome.from_stage,
        to_stage=outcome.to_stage,
        engine_ran=outcome.engine_ran,
        aggregate_metrics=outcome.aggregate_metrics,
        fold_metrics=outcome.fold_metrics,
        stress_metrics=outcome.stress_metrics,
        config=outcome.config,
        trigger=outcome.trigger,
        ts=outcome.ts,
        card_markdown=card,
    )


def _params(spec: object) -> Mapping[str, Any]:
    if isinstance(spec, Mapping):
        params = spec.get("params")
        if isinstance(params, Mapping):
            return params
    return {}


def _validate_param_bounds(spec: Mapping[str, Any]) -> None:
    params = spec.get("params")
    if not isinstance(params, Mapping):
        raise SpecHygieneError("strategy spec declares no 'params' block")
    bounds = spec.get("param_bounds")
    bounds_map = bounds if isinstance(bounds, Mapping) else {}
    for key, value in params.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue  # non-numeric params (e.g. long_only) need no numeric bound
        if not math.isfinite(float(value)):
            raise SpecHygieneError(f"parameter {key!r} is not finite")
        bound = bounds_map.get(key)
        if not (isinstance(bound, (list, tuple)) and len(bound) == 2):
            raise SpecHygieneError(f"parameter {key!r} has no declared [lo, hi] bounds")
        lo, hi = float(bound[0]), float(bound[1])
        if not lo <= float(value) <= hi:
            raise SpecHygieneError(
                f"parameter {key!r}={value} is outside its declared bounds [{lo}, {hi}]"
            )


def render_evaluation_card(outcome: EvaluationOutcome) -> str:
    """Render the Markdown evaluation card, including the required disclaimer."""

    lines: list[str] = [
        f"# Strategy evaluation — {outcome.strategy_name}",
        "",
        f"- **Strategy ID:** `{outcome.strategy_id}`",
        f"- **Evaluation ID:** `{outcome.evaluation_id}`",
        f"- **Verdict:** **{outcome.verdict}** ({outcome.reason})",
        f"- **Stage:** {outcome.from_stage} -> {outcome.to_stage or outcome.from_stage}",
        f"- **Protocol version:** {outcome.protocol_version}",
        f"- **Spec hash:** `{outcome.spec_hash}`",
        f"- **Anchor:** {'live' if outcome.anchor_fresh else 'not live'} "
        f"(world_changer status: {outcome.anchor_status or 'n/a'})",
        f"- **Trigger:** {outcome.trigger}",
        f"- **Evaluated at:** {outcome.ts.isoformat()}",
        "",
        f"> {REQUIRED_DISCLAIMER}",
        "",
    ]
    if not outcome.engine_ran:
        lines += [
            "The engine did not run: the evaluation stopped before backtest "
            f"(reason: {outcome.reason}).",
            "",
        ]
    else:
        agg = outcome.aggregate_metrics
        stress = outcome.stress_metrics
        lines += [
            "## Aggregate (out-of-sample)",
            "",
            f"- Trades: {agg.get('trade_count')}",
            f"- Total return: {_pct(agg.get('total_return'))}",
            f"- Sharpe (annualized): {_num(agg.get('sharpe'))}",
            f"- Max drawdown: {_pct(agg.get('max_drawdown'))}",
            "",
            "## Realistic-friction stress (+50% costs, +1 day execution lag)",
            "",
            f"- Sharpe (annualized): {_num(stress.get('sharpe'))} (must stay positive to promote)",
            f"- Total return: {_pct(stress.get('total_return'))}",
            "",
            "## Walk-forward folds",
            "",
            "| Fold | Start | End | Periods | Return | Sharpe | Max DD | Trades |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for fold in outcome.fold_metrics:
            lines.append(
                f"| {fold.get('index')} | {fold.get('start_date')} | {fold.get('end_date')} "
                f"| {fold.get('n_periods')} | {_pct(fold.get('total_return'))} "
                f"| {_num(fold.get('sharpe'))} | {_pct(fold.get('max_drawdown'))} "
                f"| {fold.get('trade_count')} |"
            )
        lines.append("")
    lines += [
        "## Notes",
        "",
        "- The verdict is a pure function of the metrics above against the "
        "protocol in `docs/research/eval-protocol.md` — no human tuning.",
        "- This card must not be used to modify trading or risk policy without "
        "Mike's explicit approval.",
        "",
    ]
    return "\n".join(lines)


def write_evaluation_card(root: Path, strategy_id: str, ts: datetime, markdown: str) -> Path:
    """Write the card to ``<root>/<strategy_id>/<ts>.md`` and return its path."""

    directory = root / strategy_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{ts.strftime('%Y%m%dT%H%M%SZ')}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def _num(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.3f}"
    return str(value)


def _pct(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value) * 100:.2f}%"
    return str(value)


__all__ = [
    "ARCHETYPE_BOTTLENECK_ROTATION",
    "ARCHETYPE_INFRA_GRAPH_PLAY",
    "DEFAULT_CARD_ROOT",
    "DEFAULT_TRIGGER",
    "PRODUCED_BY",
    "REQUIRED_DISCLAIMER",
    "SCHEMA_VERSION",
    "STREAM_STRATEGY_VERDICT",
    "CommitResult",
    "EvaluationError",
    "EvaluationOutcome",
    "EvaluationPipeline",
    "ReaderPort",
    "RegistryPort",
    "SpecHygieneError",
    "StrategyFactory",
    "render_evaluation_card",
    "write_evaluation_card",
]
