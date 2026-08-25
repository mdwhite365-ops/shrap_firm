"""The q-fin gate: is this paper worth a proposer call?

The Hypothesis Generator's anchor is the literature (ADR-0013 gives
``technical-catalyst`` no world-changer node), and this is the leg that supplies
it. One question only — *does this item describe a testable cross-sectional
equity effect* — decided cheaply on the classification tier, before anything
spends a heavier call trying to turn it into a spec.

**Why the world-changer filter could not be reused.** Its prompt states, as a
hard rule, that an item "merely ABOUT a technology — a new method, model
architecture, benchmark, or simulation result — is NOT evidence, no matter how
impressive." That rule is correct for Framework #1, where the question is
whether a pattern is playing out in the real world. It is a description of
essentially every q-fin paper. Pointing the existing filter at q-fin would have
rejected the entire section and reported a healthy pass while doing it.

So: two filters over one ingest, routed on source. The pools are disjoint —
``filter.py`` excludes the q-fin source, this excludes everything else — and
each item carries the verdict of exactly the filter that was built to judge it.

**The bar is narrow on purpose.** This asks only whether an effect is *claimed*
and *empirical*, never whether it is true or whether the firm can run it. Truth
is what the backtest is for; runnability is the generator's capability check.
A filter that tried to pre-judge either would be a second opinion with less
information than the thing it was pre-empting.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from ulid import ULID

from shrap.llm.registry import TIER_LOCAL_CLASSIFICATION
from shrap.research.hypothesis_generator.literature import (
    STREAM_LITERATURE_INGESTED,
    LiteratureItem,
)
from shrap.research.tech_watcher.sources import SOURCE_ARXIV_QFIN

log = structlog.get_logger(__name__)

# Bump on any behaviour-relevant prompt change. Namespaced away from the
# world-changer filter's version (`FILTER_PROMPT_VERSION`): they score disjoint
# pools under unrelated prompts, and a shared counter would make one filter's
# revision look like a reason to re-score the other's items.
LITERATURE_PROMPT_VERSION = 2

FILTER_KIND = "literature"

# Consecutive model failures before the pass gives up. One timeout is noise and
# the item retries next pass; five in a row is a dead endpoint, a bad key, or a
# model that no longer exists, and grinding through ninety-five more calls to
# discover that buries the cause.
MAX_CONSECUTIVE_FAILURES = 5

PRODUCED_BY = "tech-watcher"
SCHEMA_VERSION = "1.0.0"

LITERATURE_SYSTEM_PROMPT = (
    "You are a filter for a systematic equity trading firm's research funnel. You "
    "receive the title and abstract of one quantitative-finance paper and decide "
    "one thing: does it describe a TESTABLE CROSS-SECTIONAL EQUITY EFFECT?\n"
    "\n"
    "Accept when the paper claims that some measurable property of listed stocks "
    "predicts their subsequent returns — a ranking signal, an anomaly, a factor, "
    "a documented premium, a reversal, a flow or volume effect. The claim must be "
    "empirical: the paper says this HAS held in data, not that it should hold "
    "under some model.\n"
    "\n"
    "Reject:\n"
    "- derivative pricing, term structure, and option-implied work\n"
    "- pure mathematical finance, stochastic control, and existence proofs\n"
    "- macroeconomics, monetary policy, and anything not about individual stocks\n"
    "- methods papers: a new estimator, architecture, benchmark or dataset, where "
    "the contribution is the technique rather than a claim about returns\n"
    "- market-design, regulation and policy commentary\n"
    "- surveys and replications that report no effect of their own\n"
    "\n"
    "JUDGE WHAT THE PAPER CONCLUDES, NOT WHAT IT EXAMINES. This is the one way "
    "this filter is known to fail. A paper whose finding is that an effect does "
    "NOT work — that popular signals fail, that an anomaly has decayed, that a "
    "published result does not replicate — is evidence AGAINST an effect, and "
    "must be rejected. Read the abstract's result sentence, not its setup. If "
    "the abstract sets up signals and then reports they lose money, that is a "
    "rejection however many predictors it names.\n"
    "\n"
    "Two calibration notes. A paper testing a KNOWN effect on new data still "
    "counts — a replication that CONFIRMS an effect and reports its magnitude is "
    "a claim about returns. And a machine-learning paper counts only if the claim "
    "is about a predictor rather than about the model: 'firms with high X "
    "outperform' is in, 'our network beats the benchmark on this dataset' is "
    "out.\n"
    "\n"
    "You are NOT judging whether the effect is real, whether it still works "
    "today, or whether anyone can implement it. Something downstream tests all "
    "three. You are deciding only whether this paper ASSERTS an effect worth "
    "testing.\n"
    "\n"
    "Most papers are rejected. When genuinely unsure, reject.\n"
    "\n"
    "Respond with ONLY a JSON object: "
    '{"testable_effect": true|false, "reason": "<one sentence naming the claimed '
    'predictor and what it predicts, or why it does not qualify>", '
    '"paper_finds_it_works": true|false}'
)

# Only the q-fin leg. `filter.py` holds the mirror of this exclusion, and the two
# must stay in step: an item in neither pool is never scored at all, and an item
# in both is scored twice under prompts that disagree by design.
SELECT_UNFILTERED_LITERATURE_SQL = """
SELECT item_id, source, kind, title, summary, url, external_ts, payload
FROM research.raw_source_items
WHERE filtered_at IS NULL
  AND source = $1
ORDER BY external_ts DESC NULLS LAST, fetched_at
LIMIT $2
""".strip()

MARK_FILTERED_SQL = """
UPDATE research.raw_source_items
SET filtered_at = $2, filter_result = $3::jsonb
WHERE item_id = $1
""".strip()


class CompletionClient(Protocol):
    async def complete(
        self,
        tier: str,
        prompt: str,
        system: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        think: bool | None = None,
        task: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
    ) -> Any: ...


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


class LiteratureSink(Protocol):
    """Where accepted items land — ``research.literature_items``."""

    async def record(self, item: LiteratureItem, accepted_reason: str) -> None: ...


class EventSink(Protocol):
    async def publish(
        self,
        *,
        stream: str,
        produced_by: str,
        schema_version: str,
        payload: dict[str, Any],
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class LiteratureVerdict:
    """One paper's verdict."""

    item_id: str
    accepted: bool
    reason: str


@dataclass(frozen=True, slots=True)
class LiteratureReport:
    """One pass. Reports rejections as loudly as acceptances.

    A funnel that printed only what it kept could not be told apart from one
    silently rejecting everything, and those need opposite fixes — the lesson
    the world-changer re-filter learned when a prompt regression looked like a
    quiet feed (DQ-006).
    """

    verdicts: tuple[LiteratureVerdict, ...]

    @property
    def accepted(self) -> tuple[LiteratureVerdict, ...]:
        return tuple(v for v in self.verdicts if v.accepted)

    def render(self) -> str:
        if not self.verdicts:
            return "literature filter: no unscored q-fin items."
        lines = [
            f"literature filter (prompt v{LITERATURE_PROMPT_VERSION}): "
            f"{len(self.verdicts)} scored, {len(self.accepted)} accepted",
        ]
        for verdict in self.verdicts:
            marker = "ACCEPT" if verdict.accepted else "reject"
            lines.append(f"  {marker}  {verdict.item_id}  {verdict.reason[:140]}")
        return "\n".join(lines)


def _authors(payload: Any) -> tuple[str, ...]:
    """Author names off the ingested payload, defensively.

    asyncpg hands jsonb back as TEXT unless a codec is registered — the shape
    no test fixture produces and every production row does (PR #152). Items
    ingested before authors were captured simply have none, which is a real
    state: the proposer then has nothing to cite and refuses, correctly.
    """

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return ()
    if not isinstance(payload, Mapping):
        return ()
    names = payload.get("authors")
    if not isinstance(names, list):
        return ()
    return tuple(" ".join(str(n).split()) for n in names if str(n).strip())


def _item_prompt(title: str, summary: str | None) -> str:
    return f"Title: {title}\nAbstract: {(summary or '')[:4000] or '(no abstract)'}"


def parse_literature_response(item_id: str, content: str) -> LiteratureVerdict:
    """Parse the model's verdict. Anything unusable is a rejection.

    The funnel's standing bias: drop, never invent. An unparseable response that
    defaulted to accept would push an item the model never endorsed into the
    proposer, which is where it would acquire a citation it does not have.
    """

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return LiteratureVerdict(item_id, False, "unparseable filter response")
    if not isinstance(data, dict):
        return LiteratureVerdict(item_id, False, "non-object filter response")
    reason = data.get("reason")
    text = " ".join(str(reason).split())[:500] if isinstance(reason, str) else ""
    claims_effect = data.get("testable_effect") is True
    # A SECOND boolean rather than one, because the one failure this filter is
    # known to have is reading an abstract's setup instead of its result. v1
    # accepted "Retail Trader's Ruin: An Anatomy of Popular Signal Failure" on
    # the grounds that trend, oscillator and volume signals "are claimed to
    # predict future stock returns" — which the paper says in order to refute.
    #
    # Folding this into `testable_effect` would leave the model free to answer
    # about the setup and be right. Made explicit, it has to assert that the
    # paper FINDS the effect works, which is a different sentence to read.
    #
    # Missing counts as false. A silent accept puts a refuted claim into the
    # proposer; a silent reject shows up immediately as an empty funnel with
    # the reasons still queryable. The funnel's standing bias is to drop.
    finds_it_works = data.get("paper_finds_it_works") is True
    if claims_effect and not finds_it_works:
        return LiteratureVerdict(
            item_id=item_id,
            accepted=False,
            reason=f"paper does not report the effect working — {text or 'no reason given'}",
        )
    return LiteratureVerdict(
        item_id=item_id,
        accepted=claims_effect,
        reason=text or "no reason given",
    )


async def literature_pass(
    pool: AsyncPool,
    llm: CompletionClient,
    sink: LiteratureSink,
    events: EventSink | None = None,
    *,
    max_items: int = 100,
    tier: str = TIER_LOCAL_CLASSIFICATION,
) -> LiteratureReport:
    """Score one batch of unfiltered q-fin items.

    Each item is marked as it is scored, so a crash mid-batch resumes rather
    than restarting. **The row is written before the event is published**: the
    generator reads the table and the event is only a nudge, so a crash between
    the two costs a wakeup, not an item. The reverse order would announce
    literature that does not exist.
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(SELECT_UNFILTERED_LITERATURE_SQL, SOURCE_ARXIV_QFIN, max_items)

    verdicts: list[LiteratureVerdict] = []
    # One session per pass — the live filter and the re-filter each call this
    # once, so the id spans exactly one batch.
    session_id = str(ULID())
    consecutive_failures = 0
    for row in rows:
        item_id = str(row["item_id"])
        title = str(row["title"])
        summary = None if row["summary"] is None else str(row["summary"])
        try:
            result = await llm.complete(
                tier=tier,
                prompt=_item_prompt(title, summary),
                system=LITERATURE_SYSTEM_PROMPT,
                json_mode=True,
                think=False,
                task="filter-literature-item",
                metadata={"item_id": item_id, "prompt_version": LITERATURE_PROMPT_VERSION},
                session_id=session_id,
            )
        except Exception as e:
            # One timeout should not cost the rest of the batch an hour. The
            # item stays unmarked and is picked up next pass, so skipping loses
            # nothing; aborting the batch loses every item behind it.
            consecutive_failures += 1
            log.warning(
                "tech_watcher.literature_item_failed",
                item_id=item_id,
                error=str(e)[:300],
                consecutive=consecutive_failures,
            )
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                # A run of them is systemic — a dead endpoint, a bad key, a
                # model that no longer exists. Continuing would burn the batch
                # against a wall and bury the cause in a wall of warnings.
                log.error(
                    "tech_watcher.literature_aborted",
                    scored=len(verdicts),
                    consecutive=consecutive_failures,
                )
                break
            continue
        consecutive_failures = 0
        verdict = parse_literature_response(item_id, result.content)
        decided_at = datetime.now(UTC)

        if verdict.accepted:
            external_ts = row["external_ts"]
            await sink.record(
                LiteratureItem(
                    item_id=item_id,
                    source=str(row["source"]),
                    title=title,
                    abstract=summary or "",
                    url=None if row["url"] is None else str(row["url"]),
                    published_at=external_ts if isinstance(external_ts, datetime) else None,
                    category=None if row["kind"] is None else str(row["kind"]),
                    authors=_authors(row["payload"]),
                ),
                verdict.reason,
            )

        async with pool.acquire() as conn:
            await conn.execute(
                MARK_FILTERED_SQL,
                item_id,
                decided_at,
                json.dumps(
                    {
                        "kind": FILTER_KIND,
                        "testable_effect": verdict.accepted,
                        "reason": verdict.reason,
                        "model": result.model,
                        "prompt_version": LITERATURE_PROMPT_VERSION,
                    },
                    separators=(",", ":"),
                ),
            )

        if verdict.accepted:
            log.info("tech_watcher.literature_accepted", item_id=item_id, reason=verdict.reason)
            if events is not None:
                await events.publish(
                    stream=STREAM_LITERATURE_INGESTED,
                    produced_by=PRODUCED_BY,
                    schema_version=SCHEMA_VERSION,
                    payload={
                        "item_id": item_id,
                        "title": title[:300],
                        "url": row["url"],
                        "reason": verdict.reason,
                    },
                )
        verdicts.append(verdict)

    return LiteratureReport(verdicts=tuple(verdicts))


__all__ = [
    "FILTER_KIND",
    "LITERATURE_PROMPT_VERSION",
    "LITERATURE_SYSTEM_PROMPT",
    "LiteratureRefilterReport",
    "LiteratureRefilterVerdict",
    "LiteratureReport",
    "LiteratureVerdict",
    "literature_pass",
    "literature_refilter_pass",
    "parse_literature_response",
]


# --- re-filtering the backlog -------------------------------------------------
#
# KI-007's lesson, applied one funnel over. A prompt fix only reaches items that
# arrive after it ships; everything already scored keeps whatever verdict the
# configuration of the day produced. For a feed of a few dozen papers a day that
# means a fix effectively never lands on the backlog, and the corpus stays a
# mixture of verdicts from prompts that no longer exist.
#
# It bit immediately: filter v2 (2026-07-30) fixed a false accept — a paper whose
# finding is that the effect FAILS — with 100 items already scored under v1. The
# only recovery was deleting the rows so ingest would re-fetch them, which throws
# away the before/after comparison that says whether the fix did anything.

SELECT_FOR_LITERATURE_REFILTER_SQL = """
SELECT item_id, source, kind, title, summary, url, external_ts, payload,
       COALESCE((filter_result->>'testable_effect')::boolean, false) AS was_accepted,
       COALESCE((filter_result->>'prompt_version')::int, 0) AS scored_version,
       COALESCE(filter_result->>'model', '') AS scored_model
FROM research.raw_source_items
WHERE source = $1
  AND filtered_at IS NOT NULL
  AND (
      $4::boolean
      OR COALESCE((filter_result->>'prompt_version')::int, 0) < $2
      OR ($5::text <> '' AND COALESCE(filter_result->>'model', '') <> $5)
  )
ORDER BY external_ts DESC NULLS LAST, item_id
LIMIT $3
""".strip()


@dataclass(frozen=True, slots=True)
class LiteratureRefilterVerdict:
    """One paper's before/after under a newer prompt."""

    item_id: str
    title: str
    was_accepted: bool
    now_accepted: bool
    reason: str

    @property
    def changed(self) -> bool:
        return self.was_accepted != self.now_accepted


@dataclass(frozen=True, slots=True)
class LiteratureRefilterReport:
    """Outcome of a re-filter.

    Reports **every** verdict, not only the ones that moved — the lesson the
    world-changer re-filter learned. A run that changed nothing would otherwise
    print "0 changes" and stop, which cannot distinguish "the new prompt never
    reached the model" from "the model read it and disagreed anyway." Those need
    opposite fixes and the reasons are the only thing telling them apart.
    """

    scored: int
    prompt_version: int
    verdicts: tuple[LiteratureRefilterVerdict, ...]
    dry_run: bool

    @property
    def flips(self) -> tuple[LiteratureRefilterVerdict, ...]:
        return tuple(v for v in self.verdicts if v.changed)

    @property
    def dropped(self) -> tuple[LiteratureRefilterVerdict, ...]:
        """Previously-accepted papers the new prompt now rejects. For a filter
        whose known failure is accepting too much, this is the interesting set."""

        return tuple(v for v in self.flips if not v.now_accepted)

    @property
    def rescued(self) -> tuple[LiteratureRefilterVerdict, ...]:
        return tuple(v for v in self.flips if v.now_accepted)

    def render(self) -> str:
        if self.dry_run:
            # A dry run returns before calling the model, so `verdicts` is empty
            # and every count below it would be a zero derived from nothing —
            # printed in the exact shape of a measurement. This class's own
            # docstring says it exists to separate "the prompt never reached the
            # model" from "the model read it and disagreed", and reporting
            # "0 verdict change(s)" here is the first of those wearing the
            # second's clothes. Same defect as the world-changer re-filter's,
            # fixed in #183 and missed here because the two reports are separate
            # classes.
            return (
                f"[dry-run] literature re-filter under prompt v{self.prompt_version}: "
                f"{self.scored} item(s) eligible and NOT scored — the model was "
                "not called, so nothing is known about verdicts.\n"
                "  Re-run without --dry-run to score them. If this equals "
                "--limit, raise it to see the true eligible count."
            )
        lines = [
            f"literature re-filter under prompt v{self.prompt_version}: "
            f"{self.scored} scored, {len(self.flips)} verdict change(s)",
            f"  rescued: {len(self.rescued)}   dropped: {len(self.dropped)}   "
            f"accepted after this pass: {sum(1 for v in self.verdicts if v.now_accepted)}",
        ]
        for verdict in self.verdicts:
            if verdict.changed:
                marker = "RESCUED" if verdict.now_accepted else "DROPPED"
            else:
                marker = "kept" if verdict.now_accepted else "-"
            lines.append(f"  {marker:8} {verdict.title[:64]}\n           {verdict.reason[:150]}")
        return "\n".join(lines)


async def literature_refilter_pass(
    pool: AsyncPool,
    llm: CompletionClient,
    sink: LiteratureSink,
    *,
    max_items: int = 300,
    tier: str = TIER_LOCAL_CLASSIFICATION,
    current_model: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> LiteratureRefilterReport:
    """Re-score items whose verdict came from an older prompt *or* a different model.

    A verdict's identity is the (prompt version, model) pair — the correction the
    world-changer re-filter needed on 2026-07-27, when a model swap under an
    unchanged prompt selected nothing and the pass silently declined to test the
    change being made.

    **A drop does not delete the literature row.** An item the new prompt rejects
    keeps its `research.literature_items` row if the generator has already acted
    on it, because deleting it would erase the record of what the firm read and
    decided — and the strategy or capability gap it produced would then cite a
    paper nothing remembers accepting. The raw item's verdict is corrected; the
    history is not rewritten.
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            SELECT_FOR_LITERATURE_REFILTER_SQL,
            SOURCE_ARXIV_QFIN,
            LITERATURE_PROMPT_VERSION,
            max_items,
            force,
            current_model or "",
        )

    if dry_run:
        return LiteratureRefilterReport(
            scored=len(rows),
            prompt_version=LITERATURE_PROMPT_VERSION,
            verdicts=(),
            dry_run=True,
        )

    verdicts: list[LiteratureRefilterVerdict] = []
    # One session per pass — the live filter and the re-filter each call this
    # once, so the id spans exactly one batch.
    session_id = str(ULID())
    consecutive_failures = 0
    for row in rows:
        item_id = str(row["item_id"])
        title = str(row["title"])
        summary = None if row["summary"] is None else str(row["summary"])
        try:
            result = await llm.complete(
                tier=tier,
                prompt=_item_prompt(title, summary),
                system=LITERATURE_SYSTEM_PROMPT,
                json_mode=True,
                think=False,
                task="filter-literature-item",
                metadata={
                    "item_id": item_id,
                    "prompt_version": LITERATURE_PROMPT_VERSION,
                    "refilter": True,
                },
                session_id=session_id,
            )
        except Exception as e:
            consecutive_failures += 1
            log.warning(
                "tech_watcher.literature_refilter_item_failed",
                item_id=item_id,
                error=str(e)[:300],
            )
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.error("tech_watcher.literature_refilter_aborted", scored=len(verdicts))
                break
            continue
        consecutive_failures = 0
        verdict = parse_literature_response(item_id, result.content)
        decided_at = datetime.now(UTC)

        if verdict.accepted:
            external_ts = row["external_ts"]
            await sink.record(
                LiteratureItem(
                    item_id=item_id,
                    source=str(row["source"]),
                    title=title,
                    abstract=summary or "",
                    url=None if row["url"] is None else str(row["url"]),
                    published_at=external_ts if isinstance(external_ts, datetime) else None,
                    category=None if row["kind"] is None else str(row["kind"]),
                    authors=_authors(row["payload"]),
                ),
                verdict.reason,
            )

        async with pool.acquire() as conn:
            await conn.execute(
                MARK_FILTERED_SQL,
                item_id,
                decided_at,
                json.dumps(
                    {
                        "kind": FILTER_KIND,
                        "testable_effect": verdict.accepted,
                        "reason": verdict.reason,
                        "model": result.model,
                        "prompt_version": LITERATURE_PROMPT_VERSION,
                    },
                    separators=(",", ":"),
                ),
            )
        verdicts.append(
            LiteratureRefilterVerdict(
                item_id=item_id,
                title=title,
                was_accepted=bool(row["was_accepted"]),
                now_accepted=verdict.accepted,
                reason=verdict.reason,
            )
        )

    return LiteratureRefilterReport(
        scored=len(verdicts),
        prompt_version=LITERATURE_PROMPT_VERSION,
        verdicts=tuple(verdicts),
        dry_run=False,
    )
