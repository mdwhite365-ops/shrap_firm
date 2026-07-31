"""Model shadow-eval: settle a registry change with evidence instead of priors.

ADR-0009's Update Protocol has always required this — "the representative agent
prompt set run on both current and candidate models", scored on quality, format
adherence, latency and refusal rate, logged to `docs/research/calibration.md`
§(e). That section says it "does not yet exist — it will be created on the first
eval run", which is an accurate record of the situation: **no model eval has
ever been run.** Every model in the registry was chosen by reasoning, and one of
them (`qwen3.5:9b`) was chosen twice, the second time because the first tag did
not exist.

Three design commitments, each of which is the difference between an eval and a
demonstration:

**It runs the production prompt, or it measures nothing.** The task binding
imports `FILTER_SYSTEM_PROMPT`, `_item_prompt` and `parse_filter_response` from
the live filter rather than restating them. A candidate that scores well on a
paraphrase tells you about the paraphrase.

**It never writes a production verdict.** Results land in `research.model_eval_*`
and nowhere else. A shadow eval that mutated `filter_verdict_history` would put
experimental verdicts into the corpus the next eval samples from, and the
contamination would be invisible a month later. A test pins this.

**It samples the discriminating axis, not the corpus.** The filter corpus is
overwhelmingly negative — a uniform sample of 40 items is ~40 rejections, on
which every model agrees, and the report would show 100% agreement and mean
nothing. Sampling is stratified on the incumbent's own recorded verdict so the
positives are actually represented, and the strata are printed.

What it computes mechanically, without anyone's opinion:

- **schema adherence** — did the response parse into a valid verdict at all
- **self-consistency** — same item, same model, twice: same answer? A model that
  disagrees with itself cannot hold a gate, whatever its quality
- **latency** — p50/p95 per model
- **pairwise agreement** — where models differ, which localizes the reading

What it deliberately does not do: **decide.** Agreement is not correctness, and
nothing here knows which model was right. The report ends in a disagreement list
for Mike to adjudicate, which is the "scored by Mike" path ADR-0009 names. An
LLM judge is possible and is not in v1 — it would add a model choice, and the
whole point of this exercise is that model choices need evidence.
"""

from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import structlog

from shrap.llm import TierRegistry
from shrap.research.tech_watcher.filter import (
    FILTER_PROMPT_VERSION,
    FILTER_SYSTEM_PROMPT,
    FilterVerdict,
    UnfilteredItem,
    _item_prompt,
    parse_filter_response,
)

log = structlog.get_logger(__name__)

TASK_FILTER = "filter"

# Sampling strata. The filter corpus runs roughly 99% negative, so a uniform
# sample measures agreement on easy rejections and nothing else.
STRATUM_POSITIVE = "incumbent-relevant"
STRATUM_NEGATIVE = "incumbent-not-relevant"
STRATUM_UNSCORED = "never-scored"


@dataclass(frozen=True, slots=True)
class EvalItem:
    """One prompt to run against every candidate, plus what the incumbent said."""

    item_id: str
    stratum: str
    prompt: str
    system: str
    incumbent_relevant: bool | None
    incumbent_archetype: str | None
    display: str


@dataclass(frozen=True, slots=True)
class CallResult:
    """One model's answer to one item on one repeat."""

    model: str
    item_id: str
    repeat: int
    latency_ms: float
    raw: str
    parsed_ok: bool
    relevant: bool | None
    archetype: str | None
    reason: str
    error: str | None = None

    @property
    def answer_key(self) -> tuple[bool | None, str | None]:
        """The part of a verdict two runs must match on to count as consistent.

        ``reason`` is free text and will never match verbatim; the verdict is
        the boolean and the archetype, which is what downstream code reads.
        """

        return (self.relevant, self.archetype)


class CompletionClient(Protocol):
    async def complete(
        self,
        tier: str,
        prompt: str,
        system: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        think: bool | None = None,
    ) -> Any: ...


class ClientFactory(Protocol):
    """Builds a client bound to one specific model.

    Implemented by re-resolving the tier through a ``TierRegistry`` with the
    model overridden in its env, which is how the deployed agents already pick a
    model — so the eval exercises the same resolution, auth and error path
    rather than a parallel one.
    """

    def __call__(self, model: str) -> CompletionClient: ...


def registry_for_model(env: Mapping[str, str], tier: str, model: str) -> TierRegistry:
    """A registry identical to the deployment's, with one tier's model swapped."""

    key = f"SHRAP_LLM_{tier.upper().replace('-', '_')}_MODEL"
    provider_key = f"SHRAP_LLM_{tier.upper().replace('-', '_')}_PROVIDER"
    return TierRegistry({**env, key: model, provider_key: env.get(provider_key, "ollama")})


# ---------------------------------------------------------------------------
# sampling
# ---------------------------------------------------------------------------

SELECT_EVAL_CORPUS_SQL = """
SELECT
    r.item_id,
    r.source,
    r.kind,
    r.title,
    r.summary,
    (r.filter_result ->> 'relevant')::boolean AS incumbent_relevant,
    r.filter_result ->> 'archetype'          AS incumbent_archetype
FROM research.raw_source_items r
WHERE r.source <> ALL($1)
ORDER BY r.item_id
""".strip()


def _stratum_of(incumbent_relevant: bool | None) -> str:
    if incumbent_relevant is None:
        return STRATUM_UNSCORED
    return STRATUM_POSITIVE if incumbent_relevant else STRATUM_NEGATIVE


def build_eval_item(row: Mapping[str, Any]) -> EvalItem:
    item = UnfilteredItem(
        item_id=str(row["item_id"]),
        source=str(row["source"]),
        kind=None if row["kind"] is None else str(row["kind"]),
        title=str(row["title"]),
        summary=None if row["summary"] is None else str(row["summary"]),
    )
    incumbent = row["incumbent_relevant"]
    incumbent_relevant = None if incumbent is None else bool(incumbent)
    archetype = row["incumbent_archetype"]
    return EvalItem(
        item_id=item.item_id,
        stratum=_stratum_of(incumbent_relevant),
        prompt=_item_prompt(item),
        system=FILTER_SYSTEM_PROMPT,
        incumbent_relevant=incumbent_relevant,
        incumbent_archetype=None if archetype is None else str(archetype),
        display=f"[{item.source}] {item.title[:120]}",
    )


def stratified_sample(items: Sequence[EvalItem], sample_size: int, seed: int) -> list[EvalItem]:
    """Sample the discriminating axis, not the corpus.

    Positives are taken first and in full up to half the budget — there are few
    of them and they carry all the signal. The remainder fills from negatives,
    then from never-scored items. Deterministic given ``seed``, so a rerun is
    the same eval and a disagreement can be revisited.
    """

    rng = random.Random(seed)
    by_stratum: dict[str, list[EvalItem]] = {
        STRATUM_POSITIVE: [],
        STRATUM_NEGATIVE: [],
        STRATUM_UNSCORED: [],
    }
    for item in items:
        by_stratum[item.stratum].append(item)
    for bucket in by_stratum.values():
        bucket.sort(key=lambda i: i.item_id)
        rng.shuffle(bucket)

    half = max(sample_size // 2, 1)
    picked = by_stratum[STRATUM_POSITIVE][:half]
    for stratum in (STRATUM_NEGATIVE, STRATUM_UNSCORED):
        if len(picked) >= sample_size:
            break
        picked.extend(by_stratum[stratum][: sample_size - len(picked)])
    return picked[:sample_size]


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvalPlan:
    """What a run will do, computable before a single call is made."""

    task: str
    tier: str
    models: tuple[str, ...]
    items: tuple[EvalItem, ...]
    repeats: int
    seed: int

    @property
    def call_budget(self) -> int:
        return len(self.models) * len(self.items) * self.repeats

    def stratum_counts(self) -> dict[str, int]:
        counts = {STRATUM_POSITIVE: 0, STRATUM_NEGATIVE: 0, STRATUM_UNSCORED: 0}
        for item in self.items:
            counts[item.stratum] += 1
        return counts

    def render(self) -> str:
        strata = ", ".join(f"{k}={v}" for k, v in self.stratum_counts().items())
        return (
            f"task={self.task} tier={self.tier} models={len(self.models)} "
            f"items={len(self.items)} repeats={self.repeats} seed={self.seed}\n"
            f"strata: {strata}\n"
            f"call budget: {self.call_budget} completions "
            f"({len(self.models)} models x {len(self.items)} items x {self.repeats} repeats)"
        )


async def run_one(
    client: CompletionClient, model: str, item: EvalItem, repeat: int, tier: str
) -> CallResult:
    """One completion, timed, parsed by the production parser. Never raises."""

    started = time.perf_counter()
    try:
        result = await client.complete(
            tier=tier,
            prompt=item.prompt,
            system=item.system,
            json_mode=True,
            think=False,
        )
    except Exception as e:
        return CallResult(
            model=model,
            item_id=item.item_id,
            repeat=repeat,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            raw="",
            parsed_ok=False,
            relevant=None,
            archetype=None,
            reason="",
            error=str(e)[:500],
        )

    latency = getattr(result, "latency_ms", None)
    if not isinstance(latency, int | float):
        latency = (time.perf_counter() - started) * 1000.0
    content = str(getattr(result, "content", ""))
    verdict: FilterVerdict = parse_filter_response(item.item_id, content)
    # The production parser is total — it converts junk into a not-relevant
    # verdict rather than raising — so "did it parse" has to be asked
    # separately, and this is the sentinel that parser uses for junk.
    parsed_ok = verdict.reason not in ("unparseable filter response", "non-object filter response")
    return CallResult(
        model=model,
        item_id=item.item_id,
        repeat=repeat,
        latency_ms=float(latency),
        raw=content[:2000],
        parsed_ok=parsed_ok,
        relevant=verdict.relevant,
        archetype=verdict.archetype,
        reason=verdict.reason,
    )


async def run_plan(plan: EvalPlan, factory: ClientFactory) -> list[CallResult]:
    """Execute the plan serially. Serial on purpose: the cap is shared.

    Ollama bills GPU-time against session and weekly caps that the research
    funnel also draws on, so a burst of parallel eval calls could stall
    production work. An eval is not urgent.
    """

    results: list[CallResult] = []
    for model in plan.models:
        client = factory(model)
        for repeat in range(plan.repeats):
            for item in plan.items:
                result = await run_one(client, model, item, repeat, plan.tier)
                results.append(result)
                if result.error:
                    log.warning(
                        "model_eval.call_failed",
                        model=model,
                        item=item.item_id,
                        error=result.error,
                    )
        log.info("model_eval.model_complete", model=model, calls=len(plan.items) * plan.repeats)
    return results


# ---------------------------------------------------------------------------
# failure diagnosis
# ---------------------------------------------------------------------------

FAILURE_EMPTY = "empty"
FAILURE_FENCED = "fenced-json"
FAILURE_MALFORMED = "malformed-json"
FAILURE_WRONG_SHAPE = "wrong-shape"
FAILURE_PROSE = "prose"

_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)
_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def diagnose_failure(raw: str) -> tuple[str, bool]:
    """Why one response failed to parse, and whether a client fix would rescue it.

    The distinction is the whole point. A model wrapping correct JSON in
    markdown fences fails today and would pass tomorrow for the price of one
    ``strip`` in the production parser — that is a defect in *our* client. A
    model answering in prose cannot do strict-JSON work at all, and no amount of
    subscription fixes it. Reporting a single "80% unparsed" conflates the two
    and would get a usable model rejected, or an unusable one retried forever.

    Returns ``(kind, recoverable)``. ``recoverable`` means: some JSON object was
    extractable from the response and it parses.
    """

    text = raw.strip()
    if not text:
        return FAILURE_EMPTY, False

    fenced = _FENCE_RE.search(text)
    if fenced is not None:
        try:
            data = json.loads(fenced.group(1))
        except json.JSONDecodeError:
            return FAILURE_MALFORMED, False
        return (FAILURE_FENCED, True) if isinstance(data, dict) else (FAILURE_WRONG_SHAPE, False)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        # Parsed as JSON but the production parser still rejected it, so the
        # top level is not an object.
        return FAILURE_WRONG_SHAPE, False

    embedded = _OBJECT_RE.search(text)
    if embedded is not None:
        try:
            data = json.loads(embedded.group(0))
        except json.JSONDecodeError:
            return FAILURE_MALFORMED, False
        return (FAILURE_PROSE, True) if isinstance(data, dict) else (FAILURE_WRONG_SHAPE, False)

    if text.startswith(("{", "[")):
        return FAILURE_MALFORMED, False
    return FAILURE_PROSE, False


def failure_breakdown(results: Sequence[CallResult]) -> dict[str, int]:
    """Counts by failure kind across the unparsed, non-errored calls."""

    counts: dict[str, int] = {}
    for r in results:
        if r.error or r.parsed_ok:
            continue
        kind, _ = diagnose_failure(r.raw)
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def recoverable_count(results: Sequence[CallResult]) -> int:
    return sum(1 for r in results if not r.error and not r.parsed_ok and diagnose_failure(r.raw)[1])


def distinct_errors(results: Sequence[CallResult], limit: int = 3) -> tuple[str, ...]:
    """Deduplicated error messages, first-seen order.

    A model erroring on every call reports as a row of zeroes and an errors
    count, which says nothing about whether the tag was wrong, the usage tier
    was outside the subscription, or the endpoint timed out. The message
    distinguishes all three and costs nothing to carry.
    """

    seen: list[str] = []
    for r in results:
        if r.error and r.error not in seen:
            seen.append(r.error)
        if len(seen) >= limit:
            break
    return tuple(seen)


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

# Below this, the judgement columns rest on too few answers to read as anything.
# Set at two-thirds rather than something tidier because the first real run
# produced 20% and 100% — the interesting range is "mostly worked but not
# entirely", and a model under two-thirds is not a candidate anyway.
SCHEMA_ADHERENCE_FLOOR = 0.67

# Fewer positives than this and agreement is a statement about rejections.
MIN_POSITIVES_TO_DISCRIMINATE = 3


@dataclass(frozen=True, slots=True)
class ModelMetrics:
    model: str
    calls: int
    errors: int
    schema_adherence: float
    # The denominator behind every judgement column: parsed, non-errored calls.
    # Carried explicitly because a rate over 4 answers and a rate over 20 read
    # identically as a percentage and mean very different things.
    judged_calls: int
    unparsed: int
    recoverable: int
    failure_modes: Mapping[str, int]
    # Distinct error messages, deduplicated. A model that errors on every call
    # is usually a wrong tag or a usage tier outside the subscription, and both
    # are one line of text away from obvious — but only if the text is shown.
    error_samples: tuple[str, ...]
    self_consistency: float | None
    latency_p50_ms: float
    latency_p95_ms: float
    relevant_rate: float | None
    agreement_with_incumbent: float | None


@dataclass(frozen=True, slots=True)
class Disagreement:
    """One item where the candidates did not agree. The only human input needed."""

    item_id: str
    display: str
    stratum: str
    incumbent_relevant: bool | None
    verdicts: tuple[tuple[str, bool | None, str | None, str], ...]


@dataclass(frozen=True, slots=True)
class EvalReport:
    plan: EvalPlan
    metrics: tuple[ModelMetrics, ...]
    pairwise_agreement: Mapping[tuple[str, str], float]
    disagreements: tuple[Disagreement, ...]
    started_at: datetime
    finished_at: datetime
    notes: tuple[str, ...] = field(default=())


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(round(q * (len(ordered) - 1)), len(ordered) - 1)
    return ordered[index]


def _first_repeat(results: Sequence[CallResult]) -> dict[tuple[str, str], CallResult]:
    """Model+item → the repeat-0 result, which is the one metrics quote."""

    return {(r.model, r.item_id): r for r in results if r.repeat == 0}


def compute_metrics(plan: EvalPlan, results: Sequence[CallResult]) -> list[ModelMetrics]:
    metrics: list[ModelMetrics] = []
    incumbent = {i.item_id: i.incumbent_relevant for i in plan.items}

    for model in plan.models:
        mine = [r for r in results if r.model == model]
        if not mine:
            continue
        calls = len(mine)
        errors = sum(1 for r in mine if r.error)
        ok = [r for r in mine if not r.error]
        adherence = (sum(1 for r in ok if r.parsed_ok) / len(ok)) if ok else 0.0

        # Every judgement column below is computed over PARSED answers only.
        # The production parser turns junk into `relevant=False`, so counting
        # unparsed calls lets a model agree with a mostly-negative incumbent by
        # failing — which is exactly what the first real run reported (20%
        # schema adherence beside 90% "agreement") before this was fixed.
        judged = [r for r in ok if r.parsed_ok]

        consistency: float | None = None
        if plan.repeats > 1:
            by_item: dict[str, list[CallResult]] = {}
            for r in judged:
                by_item.setdefault(r.item_id, []).append(r)
            comparable = [v for v in by_item.values() if len(v) > 1]
            if comparable:
                consistent = sum(1 for v in comparable if len({r.answer_key for r in v}) == 1)
                consistency = consistent / len(comparable)

        latencies = [r.latency_ms for r in ok]
        first = [r for r in judged if r.repeat == 0]
        relevant_rate = (sum(1 for r in first if r.relevant) / len(first)) if first else None

        scored = [r for r in first if incumbent.get(r.item_id) is not None]
        agreement = (
            sum(1 for r in scored if r.relevant == incumbent[r.item_id]) / len(scored)
            if scored
            else None
        )

        metrics.append(
            ModelMetrics(
                model=model,
                calls=calls,
                errors=errors,
                schema_adherence=adherence,
                judged_calls=len(first),
                unparsed=sum(1 for r in ok if not r.parsed_ok),
                recoverable=recoverable_count(ok),
                failure_modes=failure_breakdown(ok),
                error_samples=distinct_errors(mine),
                self_consistency=consistency,
                latency_p50_ms=_percentile(latencies, 0.50),
                latency_p95_ms=_percentile(latencies, 0.95),
                relevant_rate=relevant_rate,
                agreement_with_incumbent=agreement,
            )
        )
    return metrics


def compute_pairwise(plan: EvalPlan, results: Sequence[CallResult]) -> dict[tuple[str, str], float]:
    first = _first_repeat(results)
    out: dict[tuple[str, str], float] = {}
    for i, a in enumerate(plan.models):
        for b in plan.models[i + 1 :]:
            both = [
                (first[(a, it.item_id)], first[(b, it.item_id)])
                for it in plan.items
                if (a, it.item_id) in first and (b, it.item_id) in first
            ]
            # Both sides must have actually answered. Two models "agreeing" on
            # an item neither could parse is not agreement about anything.
            usable = [
                (x, y)
                for x, y in both
                if not x.error and not y.error and x.parsed_ok and y.parsed_ok
            ]
            if not usable:
                continue
            agree = sum(1 for x, y in usable if x.relevant == y.relevant)
            out[(a, b)] = agree / len(usable)
    return out


def collect_disagreements(
    plan: EvalPlan, results: Sequence[CallResult], limit: int = 25
) -> list[Disagreement]:
    """Items where candidates split. Positives first — they carry the signal."""

    first = _first_repeat(results)
    found: list[Disagreement] = []
    for item in plan.items:
        verdicts = [
            (
                m,
                first[(m, item.item_id)].relevant,
                first[(m, item.item_id)].archetype,
                first[(m, item.item_id)].reason,
            )
            for m in plan.models
            if (m, item.item_id) in first
            and not first[(m, item.item_id)].error
            and first[(m, item.item_id)].parsed_ok
        ]
        if len(verdicts) < 2:
            continue
        if len({v[1] for v in verdicts}) == 1:
            continue
        found.append(
            Disagreement(
                item_id=item.item_id,
                display=item.display,
                stratum=item.stratum,
                incumbent_relevant=item.incumbent_relevant,
                verdicts=tuple(verdicts),
            )
        )
    found.sort(key=lambda d: (d.stratum != STRATUM_POSITIVE, d.item_id))
    return found[:limit]


def build_report(
    plan: EvalPlan,
    results: Sequence[CallResult],
    started_at: datetime,
    finished_at: datetime,
) -> EvalReport:
    notes: list[str] = []
    metrics = compute_metrics(plan, results)
    if plan.repeats < 2:
        notes.append("self-consistency not measured (--repeats 1)")

    positives = plan.stratum_counts()[STRATUM_POSITIVE]
    if positives < MIN_POSITIVES_TO_DISCRIMINATE:
        notes.append(
            f"only {positives} incumbent-relevant item(s) in the sample — the agreement "
            "column is therefore a statement about rejections, which every model finds "
            "easy. Read it as a floor, not a ranking. (The corpus itself may be the "
            "limit: check the relevant-count before blaming the sample size.)"
        )

    for m in metrics:
        # A model that errored on everything has no schema to adhere to. Saying
        # it "parsed 0%" describes a response it never sent, and buries the
        # routing failure that actually happened under a quality-shaped note.
        if m.errors >= m.calls:
            continue
        if m.schema_adherence < SCHEMA_ADHERENCE_FLOOR:
            modes = ", ".join(f"{k}={v}" for k, v in sorted(m.failure_modes.items())) or "unknown"
            notes.append(
                f"`{m.model}` parsed only {_pct(m.schema_adherence)} of its answers — every "
                f"judgement column for it rests on {m.judged_calls} answer(s), not "
                f"{m.calls}. Failure modes: {modes}. "
                + (
                    f"{m.recoverable} of {m.unparsed} carried extractable JSON, so a parser "
                    "fix on our side would rescue them — that is our defect, not the model's."
                    if m.recoverable
                    else "None carried extractable JSON; this model cannot hold the "
                    "strict-JSON contract for this tier."
                )
            )
    for m in metrics:
        if m.errors == m.calls and m.calls:
            notes.append(
                f"`{m.model}` failed on every call ({m.errors}/{m.calls}) and produced no "
                "verdicts at all — this is a routing failure, not a quality result. Usually a "
                "model tag that does not exist or a usage tier outside the subscription. "
                + (f"First error: {m.error_samples[0]}" if m.error_samples else "")
            )
        elif m.errors:
            notes.append(
                f"`{m.model}` errored on {m.errors} of {m.calls} calls"
                + (f" — {m.error_samples[0]}" if m.error_samples else "")
            )
    return EvalReport(
        plan=plan,
        metrics=tuple(metrics),
        pairwise_agreement=compute_pairwise(plan, results),
        disagreements=tuple(collect_disagreements(plan, results)),
        started_at=started_at,
        finished_at=finished_at,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def render_markdown(report: EvalReport) -> str:
    """The ledger block for `docs/research/calibration.md` §(e)."""

    plan = report.plan
    lines: list[str] = []
    lines.append(f"### {report.started_at.date().isoformat()} — `{plan.tier}`, task `{plan.task}`")
    lines.append("")
    lines.append(
        f"**Sample:** {len(plan.items)} items, seed {plan.seed}, {plan.repeats} repeat(s), "
        f"{plan.call_budget} completions. Prompt version {FILTER_PROMPT_VERSION}."
    )
    strata = ", ".join(f"{k} {v}" for k, v in plan.stratum_counts().items())
    lines.append(f"**Strata:** {strata}")
    lines.append("")
    lines.append(
        "| model | schema | judged | self-consist | agrees w/ incumbent | says relevant "
        "| p50 ms | p95 ms | errors |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for m in report.metrics:
        # `judged` is the denominator every column to its right is computed on.
        # Without it, a rate over four answers and a rate over twenty look the
        # same, which is how the first run reported a confident-looking 90%.
        lines.append(
            f"| `{m.model}` | {_pct(m.schema_adherence)} | {m.judged_calls}/{m.calls} | "
            f"{_pct(m.self_consistency)} | {_pct(m.agreement_with_incumbent)} | "
            f"{_pct(m.relevant_rate)} | "
            f"{m.latency_p50_ms:.0f} | {m.latency_p95_ms:.0f} | {m.errors} |"
        )
    lines.append("")

    errored = [m for m in report.metrics if m.error_samples]
    if errored:
        lines.append("**Call errors, by model:**")
        lines.append("")
        for m in errored:
            for message in m.error_samples:
                lines.append(f"- `{m.model}` ({m.errors}/{m.calls}): `{message}`")
        lines.append("")

    failing = [m for m in report.metrics if m.unparsed]
    if failing:
        lines.append("**Unparsed answers, by cause:**")
        lines.append("")
        for m in failing:
            modes = ", ".join(f"{k} {v}" for k, v in sorted(m.failure_modes.items()))
            verdict = (
                f"{m.recoverable} recoverable (a parser fix on our side would take them)"
                if m.recoverable
                else "none recoverable"
            )
            lines.append(f"- `{m.model}`: {m.unparsed} unparsed — {modes}. {verdict}.")
        lines.append("")

    if report.pairwise_agreement:
        lines.append("**Pairwise agreement on relevance:**")
        lines.append("")
        for (a, b), v in sorted(report.pairwise_agreement.items()):
            lines.append(f"- `{a}` vs `{b}`: {_pct(v)}")
        lines.append("")

    lines.append(
        "**Agreement is not correctness.** Nothing above knows which model was right; "
        "the rows below are the ones a human has to read."
    )
    lines.append("")
    if report.disagreements:
        lines.append(f"**Disagreements ({len(report.disagreements)} shown):**")
        lines.append("")
        for d in report.disagreements:
            inc = "—" if d.incumbent_relevant is None else str(d.incumbent_relevant).lower()
            lines.append(f"- **{d.display}** *(incumbent: {inc}; stratum: {d.stratum})*")
            for model, relevant, archetype, reason in d.verdicts:
                lines.append(
                    f"  - `{model}` → relevant={str(relevant).lower()} "
                    f"archetype={archetype or 'null'} — {reason}"
                )
        lines.append("")
    else:
        lines.append("**Disagreements:** none in this sample.")
        lines.append("")

    for note in report.notes:
        lines.append(f"> ⚠ {note}")
    if report.notes:
        lines.append("")
    lines.append(
        "**Verdict:** _(Mike — adjudicate the disagreements above, then record the "
        "call here. A rejected candidate stays in this ledger with its reason.)_"
    )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "FAILURE_EMPTY",
    "FAILURE_FENCED",
    "FAILURE_MALFORMED",
    "FAILURE_PROSE",
    "FAILURE_WRONG_SHAPE",
    "MIN_POSITIVES_TO_DISCRIMINATE",
    "SCHEMA_ADHERENCE_FLOOR",
    "SELECT_EVAL_CORPUS_SQL",
    "STRATUM_NEGATIVE",
    "STRATUM_POSITIVE",
    "STRATUM_UNSCORED",
    "TASK_FILTER",
    "CallResult",
    "ClientFactory",
    "Disagreement",
    "EvalItem",
    "EvalPlan",
    "EvalReport",
    "ModelMetrics",
    "build_eval_item",
    "build_report",
    "collect_disagreements",
    "compute_metrics",
    "compute_pairwise",
    "diagnose_failure",
    "distinct_errors",
    "failure_breakdown",
    "recoverable_count",
    "registry_for_model",
    "render_markdown",
    "run_one",
    "run_plan",
    "stratified_sample",
]
