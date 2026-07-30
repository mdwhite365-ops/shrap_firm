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

Gates are **archetype-conditional**, not universal (ADR-0013). Which of them
apply is declared once in :data:`ARCHETYPE_POLICIES` and nowhere else, because
the previous arrangement — gates written as global that were in fact Framework
#1 constructs — made a whole class of strategy unevaluable without anything in
the code saying so.

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
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import structlog
from ulid import ULID

from shrap.events import EventPublisher
from shrap.research.strategy_evaluator.cross_sectional import (
    CrossSectionalMomentumStrategy,
    CrossSectionalReversalStrategy,
    CrossSectionalTrendStrategy,
)
from shrap.research.strategy_evaluator.engine import (
    PROTOCOL_VERSION,
    EvalConfig,
    InsufficientDataError,
    walk_forward,
)
from shrap.research.strategy_evaluator.factors import CrossSectionalFactorStrategy
from shrap.research.strategy_evaluator.reference_strategy import ReferenceTrendStrategy
from shrap.research.strategy_evaluator.strategy import (
    BarSample,
    PanelCoverage,
    PricePanel,
    StrategySignal,
)
from shrap.research.strategy_evaluator.verdict import (
    REASON_INSUFFICIENT_DATA,
    VERDICT_HOLD,
    VERDICT_KILL,
    VERDICT_PROMOTE,
    Verdict,
    map_verdict,
    required_information_ratio,
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

ARCHETYPE_INFRA_GRAPH_PLAY = "infra-graph-play"
ARCHETYPE_BOTTLENECK_ROTATION = "bottleneck-rotation"
ARCHETYPE_TECHNICAL_CATALYST = "technical-catalyst"

# World-changer anchor is referenced by one of these keys in the strategy's
# anchor JSONB (seam convention — see the protocol doc).
ANCHOR_WORLD_CHANGER_KEYS = ("world_changer_id", "candidate_id")
WORLD_CHANGER_LIVE_STATUS = "promoted"
TIER_ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class ArchetypePolicy:
    """Which Evaluator gates apply to one hypothesis archetype.

    Before ADR-0013 the gates were written as universal and were in fact
    Framework #1 constructs: every strategy was required to be
    ``infra-graph-play`` *and* to carry a live world-changer anchor. A
    ``technical-catalyst`` strategy is anchor-less by design, so under the old
    code it was refused outright by spec hygiene — not even reaching the anchor
    check, let alone the backtest. This table makes the categorisation explicit
    and per-archetype instead of implicit and global.
    """

    archetype: str
    requires_anchor: bool
    deferred_reason: str | None = None
    """Non-``None`` means refuse: the archetype is recognised but not yet
    evaluable because a dependency it needs does not exist."""


ARCHETYPE_POLICIES: dict[str, ArchetypePolicy] = {
    ARCHETYPE_INFRA_GRAPH_PLAY: ArchetypePolicy(
        archetype=ARCHETYPE_INFRA_GRAPH_PLAY,
        # Framework #1. The thesis IS the world-changer, so an anchor that is no
        # longer promoted means the strategy's reason to exist has been falsified.
        requires_anchor=True,
    ),
    ARCHETYPE_TECHNICAL_CATALYST: ArchetypePolicy(
        archetype=ARCHETYPE_TECHNICAL_CATALYST,
        # Framework #3 (ADR-0013). Price/flow structure is the whole thesis;
        # there is no world-changer to anchor to and inventing one to satisfy
        # the gate is the failure this policy exists to prevent.
        requires_anchor=False,
    ),
    ARCHETYPE_BOTTLENECK_ROTATION: ArchetypePolicy(
        archetype=ARCHETYPE_BOTTLENECK_ROTATION,
        requires_anchor=True,
        deferred_reason=(
            "research.bottlenecks has no rows until Bottleneck Scout exists "
            "(resequencing ruling 2026-07-23)"
        ),
    ),
}

DEFAULT_CARD_ROOT = Path("docs/strategies/evaluations")

# The record → signal-code binding. A record names its rule in `spec["rule"]`;
# absent, it means the single-ticker reference crossover, which is what every
# strategy written before this registry existed assumed. This is still the seam
# the deferred strategy-authoring system replaces; tests inject their own signal
# through it. The archetype selects gates, never code — a moving average
# crossover is as legitimate an expression of `technical-catalyst` as of
# `infra-graph-play`.
RULE_REFERENCE_TREND = "reference-trend"
RULE_CROSS_SECTIONAL_TREND = "cross-sectional-trend"
RULE_CROSS_SECTIONAL_MOMENTUM = "cross-sectional-momentum"
RULE_CROSS_SECTIONAL_FACTOR = "cross-sectional-factor"
RULE_CROSS_SECTIONAL_REVERSAL = "cross-sectional-reversal"

# Rules that consume exactly one ticker. Declared rather than inferred.
#
# The original rationale no longer holds and is recorded here so it is not
# reinstated: `PricePanel` used to intersect session dates, so an extra ticker
# on a single-name rule *shortened* the usable history while contributing no
# trades — a backtest that looked shorter and no worse, with nothing to see.
# The panel is now ragged (union-aligned), so extra tickers no longer truncate
# anything.
#
# The declaration still earns its place: the benchmark is equal-weight
# buy-and-hold of every ticker in the panel, so a stray ticker on a single-name
# rule silently changes what that rule is measured *against* — and the
# information ratio is the promote gate. Same class of silent failure, moved
# from the dataset to the comparison.
SINGLE_TICKER_RULES: frozenset[str] = frozenset({RULE_REFERENCE_TREND})

# Rules that are implemented and tested but NOT yet evaluable, mirroring the
# archetype `deferred_reason` pattern. Refusing is fail-closed and writes
# nothing; the strategy stays at `hypothesis` until the dependency lands.
#
# Why these are deferred: the verdict's promote gate is an ABSOLUTE Sharpe
# floor, which cannot separate strategy skill from market exposure. Measured on
# synthetic random-walk data with a ~7.5%/yr drift and no timing skill at all,
# naive equal-weight buy-and-hold scores Sharpe 1.03 (1 name) to 1.16 (50
# names) — clearing the 1.0 promote floor purely by being invested. At zero
# drift the same portfolios score 0.33-0.45, which is the tell: the term doing
# the work is drift, not skill.
#
# This affects every long-only strategy, but it becomes near-certain with
# breadth, because a cross-sectional rule is reliably invested in something. In
# one run the timing rule scored 2.28 against buy-and-hold's 3.22 on the same
# panel — it DESTROYED value and would still have promoted.
#
# The fix is benchmark-relative evaluation: active return against equal-weight
# buy-and-hold of the same universe, not absolute Sharpe. Until that exists,
# enabling these rules would build a machine that promotes market beta.
DEFERRED_RULES: dict[str, str] = {
    # Emptied 2026-07-28 when benchmark-relative evaluation landed. The
    # cross-sectional rules were deferred because the promote gate was an
    # absolute Sharpe floor that a diversified long-only portfolio cleared on
    # market drift alone; `map_verdict` now also gates on the information ratio
    # against equal-weight buy-and-hold of the strategy's own panel, so the
    # reason for the deferral no longer holds.
    #
    # Kept as an empty table rather than deleted: it is the mechanism for
    # shipping a rule that is written and tested but not yet safe to evaluate,
    # and that situation will recur.
}

StrategyFactory = Callable[[StrategyRecord, list[str]], StrategySignal]


def _rule_name(spec: object) -> str:
    if isinstance(spec, Mapping):
        rule = spec.get("rule")
        if isinstance(rule, str) and rule.strip():
            return rule.strip()
    return RULE_REFERENCE_TREND


def _default_strategy_factory(record: StrategyRecord, tickers: list[str]) -> StrategySignal:
    rule = _rule_name(record.spec)
    params = _params(record.spec)
    if rule == RULE_CROSS_SECTIONAL_TREND:
        return CrossSectionalTrendStrategy.from_spec(params)
    if rule == RULE_CROSS_SECTIONAL_MOMENTUM:
        return CrossSectionalMomentumStrategy.from_spec(params)
    if rule == RULE_CROSS_SECTIONAL_FACTOR:
        return CrossSectionalFactorStrategy.from_spec(params)
    if rule == RULE_CROSS_SECTIONAL_REVERSAL:
        return CrossSectionalReversalStrategy.from_spec(params)
    if rule != RULE_REFERENCE_TREND:
        known = ", ".join(sorted({RULE_REFERENCE_TREND, *_CROSS_SECTIONAL_RULES}))
        raise SpecHygieneError(f"spec names unknown rule {rule!r}; known rules are {known}")
    if len(tickers) > 1:
        raise SpecHygieneError(
            f"rule {RULE_REFERENCE_TREND!r} trades one ticker but the strategy declares "
            f"{len(tickers)} ({', '.join(tickers)}); use a cross-sectional rule or "
            f"declare a single ticker"
        )
    return ReferenceTrendStrategy.from_spec(tickers[0], params)


_CROSS_SECTIONAL_RULES: frozenset[str] = frozenset(
    {
        RULE_CROSS_SECTIONAL_TREND,
        RULE_CROSS_SECTIONAL_MOMENTUM,
        RULE_CROSS_SECTIONAL_REVERSAL,
    }
)


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

    async def attempts(self, strategy_id: str) -> int: ...

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

    async def latest_information_ratio(
        self, strategy_id: str, protocol_version: str
    ) -> float | None: ...


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
        anchor_required: bool,
        anchor_fresh: bool,
        total_trades: int,
        from_stage: str,
        to_stage: str | None,
        aggregate_metrics: dict[str, Any],
        fold_metrics: list[dict[str, Any]],
        stress_metrics: dict[str, Any],
        active_metrics: dict[str, Any],
        config: dict[str, Any],
        card_path: str | None,
        trigger: str,
        created_at: datetime,
        consistency_metrics: dict[str, Any] | None = None,
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
    anchor_required: bool
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
    active_metrics: dict[str, Any]
    config: dict[str, Any]
    trigger: str
    ts: datetime
    card_markdown: str
    consistency_metrics: dict[str, Any] = field(default_factory=dict)
    """Cross-fold consistency. Empty when the engine did not run."""

    attempts: int = 1
    """How many strategies this lineage has burned, including the original.

    The multiple-testing denominator. Recorded on the outcome so that a
    promotion can always be read against the size of the search that produced
    it — a 1 sitting on a long lineage means the registry could not answer and
    the adjustment was skipped, which has to be visible rather than silent."""

    coverage: PanelCoverage | None = None
    """Panel extent and what truncated it. ``None`` when no panel was built —
    a spec refusal or a dead anchor stops before the dataset, and reporting
    ``bars=0`` there would read as an empty backfill rather than a step that
    never ran."""

    def summary(self) -> str:
        # `anchor=` is in the one-liner because on a --dry-run it is the field
        # you must read before dropping the flag: a dead anchor produces a
        # verdict that looks like a real kill but never ran the engine. The
        # previous summary omitted it, so the check the runbook asked for could
        # only be made by opening the card.
        return (
            f"{self.verdict} ({self.reason}): {self.strategy_id} "
            f"[{self.from_stage} -> {self.to_stage or self.from_stage}] "
            f"anchor={self.anchor_state} engine_ran={self.engine_ran} "
            f"trades={self.total_trades} sharpe={self.base_sharpe:.3f} "
            f"stress_sharpe={self.stress_sharpe:.3f} ir={self.reported_ir} "
            f"{self.reported_coverage} {self.reported_consistency} "
            f"{self.reported_attempts} protocol={self.protocol_version}"
        )

    @property
    def reported_ir(self) -> str:
        """Information ratio vs the benchmark, or ``n/a`` when it was not computed.

        The single number the promote gate turns on, and it was computed and then
        not shown. Reading the first real verdict — sharpe 0.797, hold on the
        sharpe floor — the interesting question was *did it beat equal-weight
        buy-and-hold*, and the only way to answer it was to reason backwards from
        gate ordering: ``no-active-edge`` fires before ``below-sharpe-floor``, so
        surviving to the latter proves IR > 0. That is a correct inference and a
        ridiculous way to read a number the run already had.

        ``n/a`` rather than ``0.000`` when absent: a missing benchmark and a
        benchmark the strategy exactly matched are different facts.
        """

        raw = self.active_metrics.get("information_ratio")
        if raw is None:
            return "n/a"
        return f"{float(raw):.3f}"

    @property
    def reported_consistency(self) -> str:
        """``folds=3/6`` — how many year-sets the strategy actually beat the
        benchmark in.

        The aggregate pools every fold into one number and discards the rest. On
        the first real evaluation that concealed a spread of 2.69 in fold Sharpe
        behind an aggregate of 0.782 — an edge indistinguishable from zero
        across periods, reported as a single respectable-looking figure.
        """

        raw = self.consistency_metrics
        if not raw:
            return "folds=n/a"
        return f"folds={raw.get('folds_with_active_edge')}/{raw.get('n_folds')}"

    @property
    def reported_attempts(self) -> str:
        """``attempt=3/ir>=0.75`` — which try this is, and the bar it must clear.

        Both halves matter. The attempt number alone does not say what it cost,
        and the adjusted floor alone does not say why it moved.
        """

        floor = self.config.get("information_ratio_floor")
        if floor is None:
            return f"attempt={self.attempts}"
        required = required_information_ratio(float(floor), self.attempts)
        return f"attempt={self.attempts}/ir>={required:.2f}"

    @property
    def reported_coverage(self) -> str:
        """``bars=506/1258 binds=ETHA`` — how much history the test actually had.

        Every gate in the protocol is a statement about a sample, and the sample
        size was the one number the verdict never carried. The momentum runbook
        tells the operator to backfill from 2018 specifically to buy folds the
        warmup would otherwise eat; whether that worked is not observable from
        anything the run emits.
        """

        if self.coverage is None:
            return "bars=n/a"
        return self.coverage.summary()

    @property
    def anchor_state(self) -> str:
        """``live`` / ``not-live`` / ``not-required`` — never a bare boolean."""

        if not self.anchor_required:
            return "not-required"
        return "live" if self.anchor_fresh else "not-live"


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


def _policy_for(archetype: str) -> ArchetypePolicy:
    """Resolve an archetype's gate policy, or refuse.

    Fail-closed on an unknown archetype: a strategy whose gates we have not
    decided on is not evaluable, and guessing a policy for it is exactly the
    silent miscategorisation ADR-0013 was written to stop.
    """

    policy = ARCHETYPE_POLICIES.get(archetype)
    if policy is None:
        known = ", ".join(sorted(ARCHETYPE_POLICIES))
        raise SpecHygieneError(
            f"archetype {archetype!r} has no declared evaluation policy; "
            f"known archetypes are {known}"
        )
    if policy.deferred_reason is not None:
        raise SpecHygieneError(f"{archetype} is not evaluable yet: {policy.deferred_reason}")
    return policy


def _anchor_world_changer_id(anchor: Mapping[str, Any] | None) -> str | None:
    if not anchor:
        return None
    for key in ANCHOR_WORLD_CHANGER_KEYS:
        value = anchor.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _empty_active() -> dict[str, Any]:
    """Active metrics when the engine never ran. Explicitly unmeasured."""

    return {
        "information_ratio": 0.0,
        "active_total_return": 0.0,
        "benchmark_sharpe": 0.0,
        "benchmark_total_return": 0.0,
        "n_periods": 0,
    }


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

        policy = _policy_for(record.archetype)
        tickers = self._check_spec_hygiene(record)
        await self._check_tickers_tradeable(tickers)

        anchor_status: str | None = None
        anchor_fresh = False
        if policy.requires_anchor:
            anchor_id = _anchor_world_changer_id(record.anchor)
            anchor_status = (
                await self._reader.world_changer_status(anchor_id) if anchor_id else None
            )
            anchor_fresh = anchor_status == WORLD_CHANGER_LIVE_STATUS
        # Otherwise the anchor is not consulted at all: no query, and no claim of
        # freshness we did not measure. `anchor_fresh=False` alongside
        # `anchor_required=False` is the honest pair — "not applicable", not
        # "checked and dead". Any anchor such a record happens to declare is
        # ignored rather than quietly re-imposed as a gate.

        ts = self._clock()
        if policy.requires_anchor and not anchor_fresh:
            verdict = map_verdict(
                anchor_required=True,
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
                anchor_required=True,
                anchor_fresh=False,
                anchor_status=anchor_status,
                engine_ran=False,
                total_trades=0,
                base_sharpe=0.0,
                stress_sharpe=0.0,
                aggregate_metrics=_empty_metrics(),
                fold_metrics=[],
                stress_metrics=_empty_metrics(),
                active_metrics=_empty_active(),
                trigger=trigger,
                ts=ts,
            )

        panel, coverage = await self._build_panel(tickers, ts.date())
        strategy = self._strategy_factory(record, tickers)
        try:
            result = walk_forward(panel, strategy, self._config)
        except InsufficientDataError:
            verdict = Verdict(VERDICT_HOLD, REASON_INSUFFICIENT_DATA)
            return self._build_outcome(
                record=record,
                verdict=verdict,
                anchor_required=policy.requires_anchor,
                anchor_fresh=anchor_fresh,
                anchor_status=anchor_status,
                engine_ran=False,
                total_trades=0,
                base_sharpe=0.0,
                stress_sharpe=0.0,
                aggregate_metrics=_empty_metrics(),
                fold_metrics=[],
                stress_metrics=_empty_metrics(),
                active_metrics=_empty_active(),
                coverage=coverage,
                trigger=trigger,
                ts=ts,
            )

        # Only compared against the parent's most recent measurement AT THIS
        # PROTOCOL. None means never measured comparably — no evaluation, or none
        # since the protocol changed — and the gate then does not fire, because
        # "cannot compare" is not "did not improve".
        parent_ir: float | None = None
        if record.parent_strategy_id is not None:
            parent_ir = await self._reader.latest_information_ratio(
                record.parent_strategy_id, PROTOCOL_VERSION
            )
        # How many strategies this idea has burned, including the original.
        # A search that keeps varying one hypothesis until something clears the
        # floor has found the best of N draws, not edge — this is the number
        # that lets the gate tell those apart. Counted since PR #141 and read
        # for the first time here.
        #
        # Failure is not fatal: a registry that cannot answer leaves the count
        # at 1, which is the UNPENALISED value. That is the wrong direction for
        # a risk control, and it is chosen deliberately — the alternative is a
        # registry hiccup silently blocking every promotion in the firm, which
        # is the more damaging failure and the harder one to diagnose. The
        # evaluation card records the count it used, so a 1 on a long lineage is
        # visible rather than silent.
        attempts = 1
        try:
            attempts = await self._registry.attempts(strategy_id)
        except Exception:
            log.warning(
                "strategy_evaluator.attempts_unavailable",
                strategy_id=strategy_id,
                note="multiple-testing adjustment skipped; treating as first attempt",
                exc_info=True,
            )
        verdict = map_verdict(
            anchor_required=policy.requires_anchor,
            anchor_fresh=anchor_fresh,
            total_trades=result.aggregate.trade_count,
            base_sharpe=result.aggregate.sharpe,
            stress_sharpe=result.stress.sharpe,
            min_trades=self._config.min_trades,
            sharpe_floor=self._config.sharpe_floor,
            information_ratio=result.active.information_ratio,
            information_ratio_floor=self._config.information_ratio_floor,
            parent_information_ratio=parent_ir,
            attempts=attempts,
        )
        return self._build_outcome(
            record=record,
            verdict=verdict,
            anchor_required=policy.requires_anchor,
            anchor_fresh=anchor_fresh,
            anchor_status=anchor_status,
            engine_ran=True,
            total_trades=result.aggregate.trade_count,
            base_sharpe=result.aggregate.sharpe,
            stress_sharpe=result.stress.sharpe,
            aggregate_metrics=result.aggregate.as_dict(),
            fold_metrics=[f.as_dict() for f in result.folds],
            stress_metrics=result.stress.as_dict(),
            active_metrics=result.active.as_dict(),
            consistency_metrics=result.consistency.as_dict(),
            attempts=attempts,
            coverage=coverage,
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
            anchor_required=outcome.anchor_required,
            anchor_fresh=outcome.anchor_fresh,
            total_trades=outcome.total_trades,
            from_stage=outcome.from_stage,
            to_stage=outcome.to_stage,
            aggregate_metrics=outcome.aggregate_metrics,
            fold_metrics=outcome.fold_metrics,
            stress_metrics=outcome.stress_metrics,
            active_metrics=outcome.active_metrics,
            consistency_metrics=outcome.consistency_metrics,
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
        # Re-resolved here rather than passed in so hygiene is self-contained:
        # it must refuse a bad archetype on its own, whoever calls it. The
        # lookup is a pure dict read, so the second call in `evaluate` is free.
        _policy_for(record.archetype)
        rule = _rule_name(record.spec)
        deferred = DEFERRED_RULES.get(rule)
        if deferred is not None:
            raise SpecHygieneError(f"rule {rule!r} is not evaluable yet: {deferred}")
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

    async def _build_panel(
        self, tickers: Sequence[str], today: date
    ) -> tuple[PricePanel, PanelCoverage]:
        start = _panel_start(self._config.window_years, today)
        bars_by_ticker: dict[str, list[BarSample]] = {}
        for ticker in tickers:
            bars = await self._reader.read_bars(ticker, start, today, self._config.adjustment)
            bars_by_ticker[ticker] = bars
        # Measured from the same dict the panel aligns, so coverage can never
        # describe a different fetch than the one that produced the verdict.
        return PricePanel.from_bars(bars_by_ticker), PanelCoverage.from_bars(bars_by_ticker)

    def _build_outcome(
        self,
        *,
        record: StrategyRecord,
        verdict: Verdict,
        anchor_required: bool,
        anchor_fresh: bool,
        anchor_status: str | None,
        engine_ran: bool,
        total_trades: int,
        base_sharpe: float,
        stress_sharpe: float,
        aggregate_metrics: dict[str, Any],
        fold_metrics: list[dict[str, Any]],
        stress_metrics: dict[str, Any],
        active_metrics: dict[str, Any],
        trigger: str,
        ts: datetime,
        coverage: PanelCoverage | None = None,
        consistency_metrics: dict[str, Any] | None = None,
        attempts: int = 1,
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
            anchor_required=anchor_required,
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
            active_metrics=active_metrics,
            config=config,
            trigger=trigger,
            ts=ts,
            card_markdown="",
            coverage=coverage,
            consistency_metrics=consistency_metrics or {},
            attempts=attempts,
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
    # `replace` rather than re-listing all 26 fields: the hand-written version
    # silently dropped any field added after it was written, and the value that
    # would have gone missing here is the one describing how much data the
    # verdict rests on.
    return replace(outcome, card_markdown=card)


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


# The floor passed to the bar reader when no lookback cap is set. The reader's
# SQL takes a mandatory start, so "all available history" needs a date rather
# than a None — and one earlier than any daily equity bar the store will ever
# hold is the honest way to say it. Deliberately not `date.min`: a year-1 date
# in a query plan or a log line reads as a bug, and this reads as a floor.
DATA_FLOOR = date(1970, 1, 1)


def _panel_start(window_years: int | None, today: date) -> date:
    """The earliest date to request bars from.

    ``window_years=None`` means all available history — the default since Mike's
    2026-07-29 ruling. A number caps the lookback, which is what to pass when a
    run should deliberately see only a recent window.
    """

    if window_years is None:
        return DATA_FLOOR
    return today - timedelta(days=window_years * 365)


def _consistency_lines(outcome: EvaluationOutcome) -> list[str]:
    """Whether the edge showed up across year-sets or in a couple of them.

    Placed under the fold table because it is that table's summary — and because
    the aggregate above it is the number that hides this one.
    """

    raw = outcome.consistency_metrics
    if not raw:
        return []
    beat = raw.get("folds_with_active_edge")
    total = raw.get("n_folds")
    consistency = raw.get("consistency")
    lines = [
        "### Consistency across year-sets",
        "",
        f"- Beat the benchmark in **{beat} of {total}** folds",
        f"- Worst fold IR: {_num(raw.get('worst_fold_ir'))}",
        f"- Fold IR mean {_num(raw.get('fold_ir_mean'))}, "
        f"spread {_num(raw.get('fold_ir_stdev'))} "
        f"→ **consistency {_num(consistency)}**",
    ]
    if isinstance(consistency, (int, float)) and 0.0 < float(consistency) < 1.0:
        lines.append(
            "- **Below 1.0: the variation between year-sets exceeds the average "
            "edge.** An aggregate computed over such folds is a pooled number "
            "standing in for periods that disagree with each other, and it will "
            "look steadier than the strategy is."
        )
    lines.append("")
    return lines


def _coverage_lines(outcome: EvaluationOutcome) -> list[str]:
    """The 'how much data was this' section of the card.

    Placed directly under the header, above the metrics, because it qualifies
    every number below it: a Sharpe over two years and a Sharpe over eight are
    not the same measurement, and the card gave no way to tell them apart.
    """

    coverage = outcome.coverage
    if coverage is None:
        return []
    span = "no dates"
    if coverage.first_date is not None and coverage.last_date is not None:
        span = f"{coverage.first_date.isoformat()} to {coverage.last_date.isoformat()}"
    lines = [
        "## Panel coverage",
        "",
        f"- Bars tested: **{coverage.n_bars}** ({span})",
        f"- Tickers: {len(coverage.per_ticker)}",
    ]
    # How wide the cross-section was at each end. This matters more since the
    # lookback stopped being capped at five years: the panel now starts at the
    # earliest bar ANY member has, so one ticker with a deeper backfill can drag
    # the start back to a stretch where almost nothing else had listed. A
    # two-name cross-section is a two-name benchmark, and the benchmark is the
    # promote gate — so if it happens, the card has to say so.
    at_start = sum(1 for c in coverage.per_ticker if c.first_date == coverage.first_date)
    at_end = sum(1 for c in coverage.per_ticker if c.last_date == coverage.last_date)
    lines.append(f"- Universe: **{at_start}** names at the first bar, **{at_end}** at the last")
    thinnest = coverage.thinnest
    if thinnest is not None:
        first = thinnest.first_date.isoformat() if thinnest.first_date else "never"
        lines += [
            f"- **The universe was complete for {coverage.fully_covered} of "
            f"{coverage.n_bars} bars.** The panel spans every date any member "
            f"traded, so names that listed later are simply absent before they "
            f"existed — the early folds ranked a smaller cross-section than the "
            f"late ones. Thinnest here is `{thinnest.ticker}` "
            f"({thinnest.n_bars} bars, first {first}, absent {thinnest.missing}).",
            "",
            "| Ticker | Bars | Absent | First |",
            "|---|---|---|---|",
        ]
        ranked = sorted(coverage.per_ticker, key=lambda c: (-c.missing, c.ticker))
        for entry in ranked[:5]:
            if entry.missing == 0:
                continue
            first_seen = entry.first_date.isoformat() if entry.first_date else "never"
            lines.append(f"| {entry.ticker} | {entry.n_bars} | {entry.missing} | {first_seen} |")
    lines.append("")
    return lines


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
        f"- **Anchor:** {_anchor_line(outcome)}",
        f"- **Trigger:** {outcome.trigger}",
        f"- **Evaluated at:** {outcome.ts.isoformat()}",
        "",
        f"> {REQUIRED_DISCLAIMER}",
        "",
    ]
    lines += _coverage_lines(outcome)
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
            "## Versus equal-weight buy-and-hold of the same names",
            "",
            f"- Information ratio: {_num(outcome.active_metrics.get('information_ratio'))} "
            "(active return / tracking error — the gate that separates skill from "
            "market exposure)",
            f"- Active total return: {_pct(outcome.active_metrics.get('active_total_return'))}",
            f"- Benchmark Sharpe: {_num(outcome.active_metrics.get('benchmark_sharpe'))}",
            "- Benchmark total return: "
            + _pct(outcome.active_metrics.get("benchmark_total_return")),
            "",
            "> Absolute Sharpe cannot tell being invested apart from being skilful: "
            "naive buy-and-hold scores 1.03-1.16 on drifting data with no timing rule "
            "at all. The information ratio above is what the promote gate uses.",
            "",
            "## Realistic-friction stress (+50% costs, +1 day execution lag)",
            "",
            f"- Sharpe (annualized): {_num(stress.get('sharpe'))} (must stay positive to promote)",
            f"- Total return: {_pct(stress.get('total_return'))}",
            "",
            "## Walk-forward folds",
            "",
            "Each fold is a separate year-set. **IR** is the promote gate applied "
            "to that period alone: absolute return says little, because +9% in a "
            "year the basket did +30% is a loss.",
            "",
            "| Fold | Start | End | Periods | Return | Sharpe | IR | Max DD | Trades |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for fold in outcome.fold_metrics:
            lines.append(
                f"| {fold.get('index')} | {fold.get('start_date')} | {fold.get('end_date')} "
                f"| {fold.get('n_periods')} | {_pct(fold.get('total_return'))} "
                f"| {_num(fold.get('sharpe'))} | {_num(fold.get('information_ratio'))} "
                f"| {_pct(fold.get('max_drawdown'))} "
                f"| {fold.get('trade_count')} |"
            )
        lines.append("")
        lines += _consistency_lines(outcome)
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


def _anchor_line(outcome: EvaluationOutcome) -> str:
    """The card's anchor line, which must not imply a check that never ran.

    ``anchor_fresh=False`` means two different things depending on whether the
    archetype requires an anchor at all, and a card that rendered both as
    "not live" would report a dead thesis for a strategy that never had one.
    """

    if not outcome.anchor_required:
        return "not required (archetype carries no world-changer anchor)"
    return f"{outcome.anchor_state} (world_changer status: {outcome.anchor_status or 'n/a'})"


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
    "ARCHETYPE_POLICIES",
    "ARCHETYPE_TECHNICAL_CATALYST",
    "DEFAULT_CARD_ROOT",
    "DEFAULT_TRIGGER",
    "PRODUCED_BY",
    "REQUIRED_DISCLAIMER",
    "RULE_CROSS_SECTIONAL_MOMENTUM",
    "RULE_CROSS_SECTIONAL_TREND",
    "RULE_REFERENCE_TREND",
    "SCHEMA_VERSION",
    "SINGLE_TICKER_RULES",
    "STREAM_STRATEGY_VERDICT",
    "ArchetypePolicy",
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
