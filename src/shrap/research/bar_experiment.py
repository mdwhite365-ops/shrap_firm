"""Archetype bar experiment — is the taxonomy misapplied, or just strict?

The card spec is ``docs/research/archetype-bar-experiment.md`` (merged as #173);
this is its harness. The premise, measured rather than argued: **prompt v4 has
admitted nothing across 2,472 verdicts**, and five models spanning four usage
tiers and four families returned 0% relevant on the same corpus with zero
disagreements. The filter is not model-limited, so what is left is the
taxonomy — and that is Mike's ruling, not an implementation detail.

The hypothesis under test: **the archetype bars are aggregate-level predicates
being evaluated against item-level evidence.** "Unit cost declining on a
learning-curve slope consistent across producers" is a statement about a series;
no single 10-Q can satisfy it. Under that reading every rejection is correct and
the *question* is wrong.

Three bars, same corpus, same model, same scaffolding. Only the question moves:

- **A — incumbent.** Literally the production path: this module imports
  ``FILTER_SYSTEM_PROMPT``, ``_item_prompt`` and ``parse_filter_response`` from
  the live filter rather than restating them. A control that is a paraphrase
  tells you about the paraphrase.
- **B — evidence contribution.** Same archetypes, same signals, same impostors,
  same output contract. The question becomes *does this item carry a fact that
  would count as evidence toward one of these signals* rather than *does it
  demonstrate the transition*. The aggregate judgement moves to clustering.
- **C — signal-level tagging.** No per-item archetype verdict at all. Ask which
  individual signal, from the flat catalogue across every archetype, the item
  speaks to — and what fact it carries. Archetype promotion becomes an
  aggregation downstream.

**What this refuses to do.** It does not lower a bar. The impostor lists are the
accumulated reason this taxonomy exists, and loosening them against 1,900 EDGAR
items admits junk at scale, which is worse than admitting nothing (vision
principle 2). Every bar carries the same impostor lists and the same
evidence-class rules; only the question changes.

**An admit rate is not a score.** A bar that admits everything wins on volume
and is worthless. The deliverable is the *admitted-item list* per bar — title,
source, label, and the model's stated reason — because whether 40 admits beat 2
is a judgement made by reading them. :func:`render_markdown` prints the list and
the report says so.

**Isolation, for the same reason the shadow eval has it.** Results land in
``research.bar_experiment_*`` and nowhere else. This never writes
``filter_result``, never marks ``filtered_at``, never appends to
``filter_verdict_history``. An experiment that fed candidate verdicts back into
the corpus would corrupt every later measurement invisibly, and a test pins it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

from shrap.research.tech_watcher.archetypes import ARCHETYPES, archetype_filter_prompt_block
from shrap.research.tech_watcher.filter import (
    FILTER_PROMPT_VERSION,
    FILTER_SYSTEM_PROMPT,
    UnfilteredItem,
    _item_prompt,
    _unwrap_code_fence,
    evidence_class,
    parse_filter_response,
)

log = structlog.get_logger(__name__)

BAR_INCUMBENT = "A-incumbent"
BAR_EVIDENCE = "B-evidence-contribution"
BAR_SIGNAL = "C-signal-tagging"

# The two items any model has ever admitted, both under prompt v3 and both by
# qwen3.5:9b-q4_K_M — the local model this project replaced *because it could
# not perform the task*. They are not a correctness oracle: the original
# verdicts are not trustworthy. They are the experiment's one natural control,
# because a bar that admits nothing is failing differently from one that admits
# these, and the distinction is free to measure.
CONTROL_ITEM_IDS: tuple[str, ...] = ("arxiv:2607.20349v1", "arxiv:2607.20083v1")

HARD_SOURCES: frozenset[str] = frozenset(
    {"sec-edgar", "usaspending", "federal-register", "doe-newsroom"}
)


# ---------------------------------------------------------------------------
# the signal catalogue (Bar C)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignalRef:
    """One signature signal, addressable on its own."""

    signal_id: str
    archetype: str
    text: str


def signal_catalogue() -> tuple[SignalRef, ...]:
    """Every archetype's signals, flattened and given stable ids.

    Derived from :data:`ARCHETYPES` rather than restated, so a change to the
    taxonomy doc's mirror reaches Bar C without anyone remembering to edit two
    lists. Ids are ``<archetype>:<index>`` — readable in a report and stable as
    long as the signal order is.
    """

    refs: list[SignalRef] = []
    for archetype in ARCHETYPES:
        for index, text in enumerate(archetype.signals):
            refs.append(
                SignalRef(
                    signal_id=f"{archetype.key}:{index}",
                    archetype=archetype.key,
                    text=text,
                )
            )
    return tuple(refs)


def signal_prompt_block() -> str:
    """The flat signal list Bar C selects from, with impostors kept in view."""

    lines = ["Signals (each is one thing that, if evidenced, moves an archetype):"]
    for ref in signal_catalogue():
        lines.append(f"  {ref.signal_id} — {ref.text}")
    lines.append("")
    lines.append("Known impostors — an item matching one of these tags NO signal:")
    for archetype in ARCHETYPES:
        for impostor in archetype.impostors:
            lines.append(f"  - ({archetype.key}) {impostor}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# verdicts
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BarVerdict:
    """One bar's reading of one item, in a shape all three bars share."""

    item_id: str
    admitted: bool
    label: str | None
    reason: str
    parsed_ok: bool = True

    @property
    def archetype(self) -> str | None:
        """The archetype this verdict implicates, whatever the bar's shape.

        Bar C labels a signal (``cost-curve:1``); A and B label an archetype
        directly. Collapsing here is what makes cross-bar agreement comparable
        at all — without it, C could never agree with anything.
        """

        if self.label is None:
            return None
        return self.label.split(":", 1)[0]


def _reason_of(data: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
    return "no reason given"


def parse_evidence_response(item_id: str, content: str) -> BarVerdict:
    """Bar B shares the incumbent's output contract, so it shares its parser."""

    verdict = parse_filter_response(item_id, content)
    parsed_ok = verdict.reason not in {
        "unparseable filter response",
        "non-object filter response",
    }
    return BarVerdict(
        item_id=item_id,
        admitted=verdict.relevant,
        label=verdict.archetype,
        reason=verdict.reason,
        parsed_ok=parsed_ok,
    )


def parse_signal_response(item_id: str, content: str) -> BarVerdict:
    """Bar C tags a signal id, so it needs its own contract and its own parser.

    Unknown or malformed signal ids are *not* admitted. A model inventing a
    plausible-looking id would otherwise manufacture evidence, which is the
    precise failure the impostor lists exist to prevent.
    """

    valid = {ref.signal_id for ref in signal_catalogue()}
    try:
        data = json.loads(_unwrap_code_fence(content))
    except json.JSONDecodeError:
        return BarVerdict(item_id, False, None, "unparseable signal response", parsed_ok=False)
    if not isinstance(data, dict):
        return BarVerdict(item_id, False, None, "non-object signal response", parsed_ok=False)

    signal = data.get("signal")
    reason = _reason_of(data, "fact", "reason")
    if not isinstance(signal, str) or signal not in valid:
        return BarVerdict(item_id, False, None, reason)
    return BarVerdict(item_id, True, signal, reason)


# ---------------------------------------------------------------------------
# the bars
# ---------------------------------------------------------------------------

EVIDENCE_SYSTEM_PROMPT = (
    "You are the Tech Watcher bulk filter for a research funnel. You receive one "
    "ingested item (an SEC filing headline, an arXiv abstract, a government "
    "contract award, or an agency press item), its evidence class, and the "
    "world-changer recognition grammar: archetype definitions, signature signals, "
    "and known impostors.\n"
    "YOUR QUESTION IS NARROWER THAN IT LOOKS. Do NOT ask whether this item "
    "demonstrates that an archetype's transition has happened. No single filing, "
    "abstract or award ever could — those signals describe trends across many "
    "documents and years. Ask instead: does this item carry a specific, checkable "
    "FACT that would count as evidence toward one of the signature signals, if it "
    "were later combined with other items? One data point on a curve is evidence; "
    "it is simply not the whole curve.\n"
    "Evidence class — apply the matching bar:\n"
    "- attested: an institution with accountability has stated this (SEC filer, "
    "federal spending record, rulemaking, or an agency reporting on its own "
    "program). Presume the event happened. Do NOT demand independent "
    "replication, peer review, or proof of commercial viability.\n"
    "- claim: an author asserts a result nobody has validated. The skeptical bars "
    "apply in full.\n"
    "Hard rules — unchanged from the strict filter, because the bar is not what "
    "is being relaxed here:\n"
    "- The fact must be about real-world adoption or economics: capacity, capex, "
    "pricing, unit cost, deployment, revenue attribution, regulatory or clinical "
    "milestones. An item merely ABOUT a technology — a method, model "
    "architecture, benchmark, or simulation — carries no such fact.\n"
    "- If the item matches a known impostor pattern, it is not evidence.\n"
    "- A document with no substantive content — a filing cover page, an index, a "
    "routine administrative notice — carries no fact. Its existence is not "
    "evidence of anything.\n"
    "- When genuinely unsure, say not relevant.\n"
    "Respond with ONLY a JSON object: "
    '{"relevant": true|false, "archetype": "<key or null>", "reason": "<one sentence '
    'naming the fact the item carries and the signal it would contribute to>"}. '
    "The archetype must be one of the provided keys or null."
)

SIGNAL_SYSTEM_PROMPT = (
    "You are tagging ingested items for a research funnel. You receive one item "
    "(an SEC filing headline, an arXiv abstract, a government contract award, or "
    "an agency press item), its evidence class, and a flat list of SIGNALS.\n"
    "Your job is NOT to decide whether a world-changing transition is underway. "
    "That judgement is made later, by aggregating many tagged items. Your job is "
    "one question: does this item carry a specific, checkable fact that speaks to "
    "exactly one of the signals below?\n"
    "Evidence class — apply the matching bar:\n"
    "- attested: an institution with accountability has stated this. Presume the "
    "event happened; do not demand replication or proof of viability.\n"
    "- claim: an unvalidated assertion. The skeptical bars apply in full.\n"
    "Hard rules:\n"
    "- The fact must be about real-world adoption or economics: capacity, capex, "
    "pricing, unit cost, deployment, revenue attribution, regulatory or clinical "
    "milestones. A method, architecture, benchmark or simulation is not a fact "
    "about the world.\n"
    "- If the item matches a known impostor pattern, tag no signal.\n"
    "- A document with no substantive content — a cover page, an index, a routine "
    "administrative notice — tags no signal. Its existence is not a fact.\n"
    "- Use ONLY signal ids from the list. Never invent one. If none fits, use "
    "null — that is the expected answer for most items.\n"
    'Respond with ONLY a JSON object: {"signal": "<signal id or null>", '
    '"fact": "<the specific fact the item carries, or why none>"}.'
)


class PromptBuilder(Protocol):
    def __call__(self, item: UnfilteredItem) -> str: ...


class VerdictParser(Protocol):
    def __call__(self, item_id: str, content: str) -> BarVerdict: ...


@dataclass(frozen=True, slots=True)
class Bar:
    """One formulation of the filter's question."""

    key: str
    description: str
    system_prompt: str
    build_prompt: PromptBuilder
    parse: VerdictParser


def _incumbent_parse(item_id: str, content: str) -> BarVerdict:
    return parse_evidence_response(item_id, content)


def _signal_item_prompt(item: UnfilteredItem) -> str:
    summary = (item.summary or "")[:1500]
    return (
        f"{signal_prompt_block()}\n\n"
        f"Item (source={item.source}, kind={item.kind or 'unknown'}, "
        f"evidence_class={evidence_class(item.source)}):\n"
        f"Title: {item.title}\n"
        f"Summary: {summary or '(none)'}"
    )


def _evidence_item_prompt(item: UnfilteredItem) -> str:
    summary = (item.summary or "")[:1500]
    return (
        f"Recognition grammar:\n{archetype_filter_prompt_block()}\n\n"
        f"Item (source={item.source}, kind={item.kind or 'unknown'}, "
        f"evidence_class={evidence_class(item.source)}):\n"
        f"Title: {item.title}\n"
        f"Summary: {summary or '(none)'}"
    )


def all_bars() -> tuple[Bar, ...]:
    return (
        Bar(
            key=BAR_INCUMBENT,
            description=(f"production prompt v{FILTER_PROMPT_VERSION}, unmodified — the control"),
            system_prompt=FILTER_SYSTEM_PROMPT,
            build_prompt=_item_prompt,
            parse=_incumbent_parse,
        ),
        Bar(
            key=BAR_EVIDENCE,
            description="same grammar; asks what fact the item contributes, not what it proves",
            system_prompt=EVIDENCE_SYSTEM_PROMPT,
            build_prompt=_evidence_item_prompt,
            parse=parse_evidence_response,
        ),
        Bar(
            key=BAR_SIGNAL,
            description="no archetype verdict; tags one signal from the flat catalogue",
            system_prompt=SIGNAL_SYSTEM_PROMPT,
            build_prompt=_signal_item_prompt,
            parse=parse_signal_response,
        ),
    )


def bars_by_key(keys: Sequence[str] | None = None) -> tuple[Bar, ...]:
    available = {bar.key: bar for bar in all_bars()}
    if not keys:
        return all_bars()
    missing = [key for key in keys if key not in available]
    if missing:
        raise ValueError(f"unknown bar(s) {missing}; available: {sorted(available)}")
    return tuple(available[key] for key in keys)


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------


class CompletionClient(Protocol):
    async def complete(
        self,
        tier: str,
        prompt: str,
        *,
        system: str,
        json_mode: bool,
        think: bool,
        task: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class BarCall:
    """One bar's call on one item."""

    bar: str
    item: UnfilteredItem
    verdict: BarVerdict | None
    latency_ms: float
    error: str | None = None
    raw: str = ""


async def run_bar(
    bar: Bar,
    client: CompletionClient,
    items: Sequence[UnfilteredItem],
    tier: str,
) -> list[BarCall]:
    """Score every item under one bar. A failed call is recorded, not raised.

    An exception here would lose the whole pass's work for one bad item; the
    error travels in the result so the report can name a routing failure rather
    than render it as a row of zeroes — the lesson from the model eval's first
    two runs.
    """

    calls: list[BarCall] = []
    for item in items:
        started = time.perf_counter()
        try:
            result = await client.complete(
                tier,
                bar.build_prompt(item),
                system=bar.system_prompt,
                json_mode=True,
                think=False,
                task="evaluate-archetype-bar",
                metadata={"bar": bar.key, "item_id": item.item_id},
            )
        except Exception as exc:  # recorded in the result, never swallowed
            calls.append(
                BarCall(
                    bar=bar.key,
                    item=item,
                    verdict=None,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    error=f"{type(exc).__name__}: {exc}"[:300],
                )
            )
            continue
        latency_ms = (time.perf_counter() - started) * 1000
        raw = str(getattr(result, "content", ""))
        calls.append(
            BarCall(
                bar=bar.key,
                item=item,
                verdict=bar.parse(item.item_id, raw),
                latency_ms=latency_ms,
                raw=raw,
            )
        )
    return calls


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BarSummary:
    """What one bar did, in the shape the ruling needs."""

    bar: str
    description: str
    scored: int
    parse_failures: int
    errors: int
    admitted: tuple[BarCall, ...]
    admitted_by_source: Mapping[str, int]
    control_admitted: tuple[str, ...]
    control_rejected: tuple[str, ...]
    scored_by_source: Mapping[str, int] = field(default_factory=dict)

    @property
    def admit_rate(self) -> float:
        judged = self.scored - self.errors
        return len(self.admitted) / judged if judged else 0.0

    @property
    def hard_source_admits(self) -> int:
        return sum(count for src, count in self.admitted_by_source.items() if src in HARD_SOURCES)

    @property
    def hard_source_scored(self) -> int:
        """How many hard-leg items this bar actually saw.

        Without this the hard-leg admit count is unreadable: zero admits out of
        zero scored is not a result, and a limited run ordered by ``item_id``
        sees only arXiv. The 2026-07-31 calibration reported ``hard-leg 0``
        having never been shown a hard-leg item.
        """

        return sum(count for src, count in self.scored_by_source.items() if src in HARD_SOURCES)

    @property
    def controls_unseen(self) -> tuple[str, ...]:
        """Control items this bar never scored, as distinct from ones it rejected."""

        seen = set(self.control_admitted) | set(self.control_rejected)
        return tuple(item_id for item_id in CONTROL_ITEM_IDS if item_id not in seen)


def summarize(bar: Bar, calls: Sequence[BarCall]) -> BarSummary:
    admitted = tuple(c for c in calls if c.verdict is not None and c.verdict.admitted)
    by_source: dict[str, int] = {}
    for call in admitted:
        by_source[call.item.source] = by_source.get(call.item.source, 0) + 1
    scored_by_source: dict[str, int] = {}
    for call in calls:
        scored_by_source[call.item.source] = scored_by_source.get(call.item.source, 0) + 1
    admitted_ids = {c.item.item_id for c in admitted}
    return BarSummary(
        bar=bar.key,
        description=bar.description,
        scored=len(calls),
        parse_failures=sum(
            1
            for c in calls
            if c.error is None and c.verdict is not None and not c.verdict.parsed_ok
        ),
        errors=sum(1 for c in calls if c.error is not None),
        admitted=admitted,
        admitted_by_source=by_source,
        scored_by_source=scored_by_source,
        control_admitted=tuple(item_id for item_id in CONTROL_ITEM_IDS if item_id in admitted_ids),
        control_rejected=tuple(
            c.item.item_id
            for c in calls
            if c.item.item_id in CONTROL_ITEM_IDS and c.item.item_id not in admitted_ids
        ),
    )


def stratified_limit(
    items: Sequence[UnfilteredItem], limit: int, *, always_include: Sequence[str] = CONTROL_ITEM_IDS
) -> list[UnfilteredItem]:
    """Take ``limit`` items proportionally across sources, preserving corpus shape.

    A plain head-of-list slice is worse than useless here: the corpus is ordered
    by ``item_id``, ``arxiv:`` sorts first, and the 2026-07-31 calibration
    therefore scored 600 arXiv items and reported ``hard-leg 0`` without ever
    having been shown a hard-leg item. The number looked like a finding and was
    an artifact of the ordering.

    Sources with few items keep all of them (``doe-newsroom`` has 18 in total —
    proportional allocation would round it to nothing, and it is the source
    carrying DQ-006's named false negative). The control items are always
    included, because a run that silently omits its own control cannot report
    on it.
    """

    if limit >= len(items):
        return list(items)

    by_source: dict[str, list[UnfilteredItem]] = {}
    for item in items:
        by_source.setdefault(item.source, []).append(item)

    forced = [item for item in items if item.item_id in set(always_include)]
    budget = max(limit - len(forced), 0)
    total = len(items)

    picked: list[UnfilteredItem] = []
    for _source, source_items in sorted(by_source.items()):
        share = round(budget * len(source_items) / total)
        take = min(len(source_items), max(share, 1))
        picked.extend(source_items[:take])

    chosen: dict[str, UnfilteredItem] = {item.item_id: item for item in forced}
    for item in picked:
        if len(chosen) >= limit and item.item_id not in chosen:
            continue
        chosen.setdefault(item.item_id, item)
    return [item for item in items if item.item_id in chosen]


def cross_bar_agreement(summaries: Sequence[BarSummary]) -> dict[tuple[str, str], int]:
    """How many items each pair of bars both admitted.

    Not a quality measure — it localizes where a reformulation changed the
    reading rather than only the volume.
    """

    admitted_sets = {s.bar: {c.item.item_id for c in s.admitted} for s in summaries}
    out: dict[tuple[str, str], int] = {}
    keys = sorted(admitted_sets)
    for i, left in enumerate(keys):
        for right in keys[i + 1 :]:
            out[(left, right)] = len(admitted_sets[left] & admitted_sets[right])
    return out


@dataclass(frozen=True, slots=True)
class ExperimentReport:
    corpus_size: int
    tier: str
    model: str
    summaries: tuple[BarSummary, ...]
    started_at: str
    finished_at: str
    admitted_sample_limit: int = 25
    _agreement: dict[tuple[str, str], int] = field(default_factory=dict)


def render_markdown(report: ExperimentReport) -> str:
    lines: list[str] = []
    lines.append(f"### {report.finished_at[:10]} — archetype bar experiment")
    lines.append("")
    lines.append(
        f"**Corpus:** {report.corpus_size} items · **tier** `{report.tier}` · "
        f"**model** `{report.model}` · prompt v{FILTER_PROMPT_VERSION} for the control."
    )
    lines.append("")
    lines.append("| bar | admitted | rate | hard-leg admits / scored | parse fails | errors |")
    lines.append("|---|---|---|---|---|---|")
    for summary in report.summaries:
        lines.append(
            f"| `{summary.bar}` | {len(summary.admitted)} | {summary.admit_rate:.1%} | "
            f"{summary.hard_source_admits} / {summary.hard_source_scored} | "
            f"{summary.parse_failures} | {summary.errors} |"
        )
    lines.append("")
    lines.append(
        "**The hard-leg column is the one KI-009 needs.** arXiv-only clusters fail "
        "triangulation on both conditions at once, so a bar that admits only arXiv "
        "has not unblocked the funnel however good its rate looks. It is reported as "
        "*admits / scored* because zero admits out of zero scored is not a result — "
        "the 2026-07-31 calibration reported `hard-leg 0` having never been shown a "
        "hard-leg item."
    )
    lines.append("")
    starved = [s for s in report.summaries if s.hard_source_scored == 0]
    if starved:
        lines.append(
            "> ⚠ **This run scored no hard-leg items at all** "
            f"({', '.join('`' + s.bar + '`' for s in starved)}), so it says nothing about "
            "KI-009. Re-run without `--limit`, or with a build that samples "
            "proportionally across sources."
        )
        lines.append("")

    for summary in report.summaries:
        lines.append(f"#### `{summary.bar}` — {summary.description}")
        lines.append("")
        if not summary.admitted:
            lines.append("*Admitted nothing.*")
            lines.append("")
            continue
        by_source = ", ".join(
            f"{src} {count}" for src, count in sorted(summary.admitted_by_source.items())
        )
        lines.append(f"By source: {by_source}")
        lines.append("")
        lines.append("| item | source | label | reason |")
        lines.append("|---|---|---|---|")
        for call in summary.admitted[: report.admitted_sample_limit]:
            assert call.verdict is not None
            title = call.item.title.replace("|", "\\|")[:90]
            reason = call.verdict.reason.replace("|", "\\|")[:160]
            lines.append(f"| {title} | {call.item.source} | `{call.verdict.label}` | {reason} |")
        if len(summary.admitted) > report.admitted_sample_limit:
            lines.append("")
            lines.append(
                f"*({len(summary.admitted) - report.admitted_sample_limit} more admitted; "
                "the full set is in `research.bar_experiment_results`.)*"
            )
        lines.append("")

    agreement = report._agreement or cross_bar_agreement(report.summaries)
    if agreement:
        lines.append("**Items admitted by more than one bar:**")
        lines.append("")
        for (left, right), count in sorted(agreement.items()):
            lines.append(f"- `{left}` ∩ `{right}`: {count}")
        lines.append("")

    lines.append("**The two control items** (the only items any model has ever admitted, ")
    lines.append("both under v3 and both by a model replaced for being wrong):")
    lines.append("")
    for summary in report.summaries:
        parts: list[str] = []
        if summary.control_admitted:
            parts.append(f"admitted {', '.join(summary.control_admitted)}")
        if summary.control_rejected:
            parts.append(f"rejected {', '.join(summary.control_rejected)}")
        if summary.controls_unseen:
            # Distinct from a rejection, and the difference matters: a run that
            # never scored its control cannot report on it, and "neither" would
            # read as a verdict.
            parts.append(f"**never scored** {', '.join(summary.controls_unseen)}")
        lines.append(f"- `{summary.bar}`: {'; '.join(parts) if parts else 'none'}")
    lines.append("")
    lines.append(
        "> **An admit rate is not a score.** A bar that admits everything wins this "
        "table and is worthless. The question this run puts to Mike is whether the "
        "admitted items above are evidence or junk — that is a judgement made by "
        "reading them, and nothing here can make it."
    )
    lines.append("")
    lines.append("**Ruling:** _(Mike — record the call here, and which bar the taxonomy adopts.)_")
    return "\n".join(lines)


__all__ = [
    "BAR_EVIDENCE",
    "BAR_INCUMBENT",
    "BAR_SIGNAL",
    "CONTROL_ITEM_IDS",
    "HARD_SOURCES",
    "Bar",
    "BarCall",
    "BarSummary",
    "BarVerdict",
    "ExperimentReport",
    "SignalRef",
    "all_bars",
    "bars_by_key",
    "cross_bar_agreement",
    "parse_evidence_response",
    "parse_signal_response",
    "render_markdown",
    "run_bar",
    "signal_catalogue",
    "signal_prompt_block",
    "summarize",
]
