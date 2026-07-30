"""The Hypothesis Generator: literature in, strategies and a build queue out.

One pass over pending literature items. Each one ends in exactly one of three
places, and the agent says which:

``proposed``
    A lineage root at ``hypothesis``, citing its prior, naming its deviation,
    carrying kill criteria. The Strategy Evaluator decides whether it has edge;
    this agent only decides whether it is a legitimate question to ask.

``capability-gap``
    The effect is real and cited but the engine cannot run it. Recorded against
    a durable queue rather than dropped. **This is the expected outcome for most
    items**, and it is the useful one: the firm's four implemented factors are
    all spoken for, so the literature's answer to "what should we try next" is
    almost always "something you have not built yet."

``refused``
    No citation, not a market effect, an already-held identity, a horizon
    outside its rule. Recorded with the reason, never retried.

**There is no per-night cap and none is needed.** The spec proposed three
(2 technical-catalyst, 10 firm-wide), on the reasoning that a cap larger than
the literature's supply is an instruction to invent. The structural bound is
stronger than any number: :func:`hypothesis_key` means the proposer can mint at
most one lineage root per implemented effect, ever, so it cannot flood the
Evaluator and cannot spend a lineage's promote budget on a search. The throttle
is the shape of the registry, not a counter that could be tuned.

**The order of the checks is load-bearing.** Citation before capability: an item
that cannot say who claimed what is worthless as a build-queue entry too, and
letting it through would fill the queue with suggestions nobody could trace.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

import structlog

from shrap.llm.registry import TIER_LOCAL_HEAVY
from shrap.research.hypothesis_generator.expressible import (
    OUTCOME_EXPRESSIBLE,
    OUTCOME_MISSING_DATA,
    CapabilityGap,
    GapCitation,
    classify,
    hypothesis_key,
    missing_inputs,
)
from shrap.research.hypothesis_generator.literature import (
    OUTCOME_CAPABILITY_GAP,
    OUTCOME_PROPOSED,
    OUTCOME_REFUSED,
    LiteratureItem,
    LiteratureStore,
)
from shrap.research.hypothesis_generator.proposer import (
    CompletionClient,
    RawProposal,
    propose,
)
from shrap.research.hypothesis_generator.record import build_record
from shrap.research.hypothesis_generator.store import GapStore, render_queue
from shrap.research.hypothesis_generator.validate import (
    REASON_UNPARSEABLE,
    Refusal,
    check_citable,
    check_spec,
)
from shrap.research.strategy_registry import StrategyRecord

log = structlog.get_logger(__name__)

ACTOR = "hypothesis-generator"

REASON_DUPLICATE_SPEC = "duplicate-spec-hash"
REASON_DUPLICATE_NAME = "duplicate-name"

# Every proposal is a lineage root, so the registration reason is fixed. It says
# what a person reading the transition log months later needs: which agent, and
# on the strength of what.
REGISTRATION_REASON = "proposed by the Hypothesis Generator from a cited published effect"


class StrategyRegistry(Protocol):
    """The slice of the registry this agent uses. It cannot promote or kill."""

    async def list_all(self) -> list[StrategyRecord]: ...

    async def register(
        self,
        record: StrategyRecord,
        *,
        reason: str,
        actor: str,
        trigger_kind: str = ...,
        trigger_ref: str | None = ...,
    ) -> bool: ...


def held_identities(records: Sequence[StrategyRecord]) -> dict[str, str]:
    """Map every hypothesis the firm already holds to the strategy that holds it.

    Read from each record's spec, never from its name — a strategy called
    "momentum" that specs a reversal rule holds the reversal identity, and
    trusting the name would let a mislabelled row wave a duplicate through.
    """

    out: dict[str, str] = {}
    for record in records:
        spec = record.spec if isinstance(record.spec, Mapping) else {}
        rule = spec.get("rule")
        if not isinstance(rule, str) or not rule:
            continue
        params = spec.get("params")
        factor_raw = params.get("factor") if isinstance(params, Mapping) else None
        factor = str(factor_raw) if isinstance(factor_raw, str) and factor_raw else None
        out.setdefault(hypothesis_key(rule, factor), record.strategy_id)
    return out


@dataclass(slots=True)
class _Corpus:
    """What the registry already holds, read once and kept current in-batch."""

    held: dict[str, str]
    names: set[str]
    hashes: set[str]


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    """What happened to one literature item."""

    item: LiteratureItem
    outcome: str
    detail: str
    strategy_id: str | None = None
    gap: CapabilityGap | None = None

    def render(self) -> str:
        marker = {
            OUTCOME_PROPOSED: "PROPOSED",
            OUTCOME_CAPABILITY_GAP: "GAP",
            OUTCOME_REFUSED: "refused",
        }.get(self.outcome, self.outcome)
        head = f"  {marker:9} {self.item.title[:72]}"
        return f"{head}\n            {self.detail[:200]}"


@dataclass(frozen=True, slots=True)
class GenerationReport:
    """One pass, in full. Reports refusals as prominently as proposals.

    A funnel that printed only its successes could not be told apart from a
    funnel that was silently refusing everything, and those need opposite fixes.
    """

    outcomes: tuple[ItemOutcome, ...]
    queue: str
    dry_run: bool

    def count(self, outcome: str) -> int:
        return sum(1 for o in self.outcomes if o.outcome == outcome)

    @property
    def proposed(self) -> tuple[ItemOutcome, ...]:
        return tuple(o for o in self.outcomes if o.outcome == OUTCOME_PROPOSED)

    def render(self) -> str:
        prefix = "[dry-run] " if self.dry_run else ""
        if not self.outcomes:
            return (
                f"{prefix}no pending literature items — nothing to propose from. "
                "Tech Watcher's q-fin ingest is what fills this queue."
            )
        reasons: dict[str, int] = {}
        for outcome in self.outcomes:
            if outcome.outcome == OUTCOME_REFUSED:
                key = outcome.detail.split(":", 1)[0]
                reasons[key] = reasons.get(key, 0) + 1
        lines = [
            f"{prefix}{len(self.outcomes)} literature item(s) read: "
            f"{self.count(OUTCOME_PROPOSED)} proposed, "
            f"{self.count(OUTCOME_CAPABILITY_GAP)} capability gap(s), "
            f"{self.count(OUTCOME_REFUSED)} refused.",
            "",
        ]
        lines.extend(o.render() for o in self.outcomes)
        if reasons:
            summary = ", ".join(f"{k}={v}" for k, v in sorted(reasons.items()))
            lines.extend(["", f"refusal reasons: {summary}"])
        lines.extend(["", self.queue])
        return "\n".join(lines)


class HypothesisGenerator:
    """Reads literature, writes strategies and capability gaps. Nothing else.

    It cannot promote, kill, size, or evaluate. The only durable effects are a
    row at ``hypothesis`` — a status that trades no money and that the Evaluator
    still has to clear — and a row on the build queue.
    """

    def __init__(
        self,
        *,
        llm: CompletionClient,
        registry: StrategyRegistry,
        literature: LiteratureStore,
        gaps: GapStore,
        tier: str = TIER_LOCAL_HEAVY,
        dry_run: bool = False,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._literature = literature
        self._gaps = gaps
        self._tier = tier
        self._dry_run = dry_run

    async def run(self, items: Sequence[LiteratureItem]) -> GenerationReport:
        existing = await self._registry.list_all()
        corpus = _Corpus(
            held=held_identities(existing),
            names={r.name for r in existing},
            hashes={r.spec_hash for r in existing},
        )
        outcomes: list[ItemOutcome] = []
        for item in items:
            outcome = await self._one(item, corpus)
            outcomes.append(outcome)
            if not self._dry_run:
                await self._literature.mark_processed(item.item_id, outcome.outcome, outcome.detail)
        queue = render_queue(await self._gaps.ranked())
        return GenerationReport(outcomes=tuple(outcomes), queue=queue, dry_run=self._dry_run)

    async def _one(self, item: LiteratureItem, corpus: _Corpus) -> ItemOutcome:
        raw = await propose(self._llm, item, self._tier)
        if raw is None:
            return self._refused(item, Refusal(REASON_UNPARSEABLE, "model response unusable"))

        refusal = check_citable(raw)
        if refusal is not None:
            return self._refused(item, refusal)

        verdict = classify(raw.rule, raw.factor, raw.required_inputs)
        if verdict != OUTCOME_EXPRESSIBLE:
            return await self._record_gap(item, raw, verdict)

        refusal = check_spec(raw, corpus.held)
        if refusal is not None:
            return self._refused(item, refusal)

        record = build_record(raw, item)
        # Both uniqueness checks are made HERE rather than left to the database.
        # `research.strategies` is unique on spec_hash and on (name, version), so
        # a collision raises out of the driver and takes the whole batch with it
        # — one duplicate would cost every item queued behind it.
        if record.spec_hash in corpus.hashes:
            return self._refused(
                item, Refusal(REASON_DUPLICATE_SPEC, "the registry already holds this exact spec")
            )
        if record.name in corpus.names:
            return self._refused(
                item,
                Refusal(REASON_DUPLICATE_NAME, f"a strategy is already called {record.name!r}"),
            )
        if not self._dry_run:
            await self._registry.register(
                record,
                reason=REGISTRATION_REASON,
                actor=ACTOR,
                trigger_kind="literature",
                trigger_ref=item.item_id,
            )
        # Claim the identity for the rest of this batch. Two papers describing
        # one effect in a single run would otherwise both pass, because the
        # registry was read once before either was written.
        corpus.held[hypothesis_key(raw.rule, raw.factor)] = record.strategy_id
        corpus.names.add(record.name)
        corpus.hashes.add(record.spec_hash)
        log.info(
            "hypothesis_generator.proposed",
            strategy_id=record.strategy_id,
            effect=raw.effect_name,
            rule=raw.rule,
            item_id=item.item_id,
            dry_run=self._dry_run,
        )
        prior = raw.prior.render() if raw.prior is not None else ""
        return ItemOutcome(
            item=item,
            outcome=OUTCOME_PROPOSED,
            detail=f"{record.name} [{record.strategy_id}] — {prior}",
            strategy_id=record.strategy_id,
        )

    async def _record_gap(
        self, item: LiteratureItem, raw: RawProposal, verdict: str
    ) -> ItemOutcome:
        missing = missing_inputs(raw.required_inputs) if verdict == OUTCOME_MISSING_DATA else ()
        gap = CapabilityGap(
            effect_name=raw.effect_name,
            kind=verdict,
            missing=missing,
            sketch=raw.scorer_sketch,
            citation=GapCitation(
                item_id=item.item_id,
                title=item.title,
                url=item.url,
                prior=raw.prior.render() if raw.prior is not None else "",
            ),
        )
        if not self._dry_run:
            await self._gaps.record(gap)
        need = f" (needs {', '.join(missing)})" if missing else ""
        return ItemOutcome(
            item=item,
            outcome=OUTCOME_CAPABILITY_GAP,
            detail=f"{verdict}: {raw.effect_name}{need} — {raw.scorer_sketch[:120]}",
            gap=gap,
        )

    def _refused(self, item: LiteratureItem, refusal: Refusal) -> ItemOutcome:
        log.info(
            "hypothesis_generator.refused",
            item_id=item.item_id,
            reason=refusal.reason,
            detail=refusal.detail,
        )
        return ItemOutcome(item=item, outcome=OUTCOME_REFUSED, detail=refusal.render())


__all__ = [
    "ACTOR",
    "REASON_DUPLICATE_NAME",
    "REASON_DUPLICATE_SPEC",
    "GenerationReport",
    "HypothesisGenerator",
    "ItemOutcome",
    "StrategyRegistry",
    "held_identities",
]
