"""Bulk relevance filter (Tech Watcher spec Processing step 2).

Scores each unfiltered raw item against the world-changer archetype
vocabulary on the ``local-classification`` tier with thinking disabled —
this is a yes/no/which-archetype call over hundreds of items, not a
judgment turn. Items are marked ``filtered_at`` either way; relevant ones
carry the archetype key and reason in ``filter_result`` for the clustering
step. An unparseable model response counts as not-relevant and is logged —
the funnel's bias is to drop, never to invent. A response whose JSON is
wrapped in a markdown code fence is unwrapped first, not dropped: see
``_unwrap_code_fence`` for why that is a parser concern and not a model one.

Every verdict is also appended to ``research.filter_verdict_history``,
stamped with the prompt version (KI-007): a re-filter overwrites the item's
current ``filter_result`` but never the history, so cross-prompt-version
comparisons stay queryable after the fact.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from shrap.llm.registry import TIER_LOCAL_CLASSIFICATION
from shrap.research.tech_watcher.archetypes import ARCHETYPE_KEYS, archetype_filter_prompt_block
from shrap.research.tech_watcher.sources import (
    SOURCE_ARXIV_QFIN,
    SOURCE_DOE_NEWS,
    SOURCE_EDGAR,
    SOURCE_FED_REGISTER,
    SOURCE_USASPENDING,
)

log = structlog.get_logger(__name__)

# Bump on any behavior-relevant prompt change; stamped into filter_result so
# calibration reviews know which prompt scored each item.
#
# Deliberately NOT bumped for KI-026's document body. The prompt text is
# unchanged; what changed is the *content* an EDGAR item carries into it. Using
# the version to mean "re-score everything" would make it stop meaning "this is
# which prompt ran", and the calibration ledger reads it as the latter. The
# corpus-wide re-score after a backfill is `shrap-tech-watcher-refilter --force`,
# which says what it is doing.
FILTER_PROMPT_VERSION = 4

# How much of a stored document body reaches the prompt. Matches
# `edgar_text.DEFAULT_MAX_CHARS`, and is applied again here because the column
# is a store other passes may fill with a different budget — the prompt's cost
# must be bounded by the prompt builder, not by whoever wrote the row.
DOCUMENT_PROMPT_CHARS = 6_000

# Evidence class — the filter's evidentiary bar, keyed on who is asserting the
# fact. This is NOT the triangulation hardness rule in ``synthesis.py``, and the
# two must not be conflated: DOE newsroom is ``attested`` here (a federal agency
# stating its own program reached a milestone) while remaining *soft* for
# triangulation (agency press is promotional and the agency has an interest).
# One governs "did this happen"; the other governs "does it count as an
# independent leg."
EVIDENCE_ATTESTED = "attested"
EVIDENCE_CLAIM = "claim"

_ATTESTED_SOURCES = frozenset(
    {SOURCE_EDGAR, SOURCE_USASPENDING, SOURCE_FED_REGISTER, SOURCE_DOE_NEWS}
)


def evidence_class(source: str) -> str:
    """Who is asserting the fact, and therefore what bar applies.

    ``attested`` — an institution with accountability has stated it: an SEC
    filer, a federal spending record, a rulemaking, an agency reporting on its
    own program. The event is presumed to have occurred.

    ``claim`` — an author asserting an unvalidated result (arXiv). Skepticism
    about whether the thing is real is appropriate here and only here.
    """

    return EVIDENCE_ATTESTED if source in _ATTESTED_SOURCES else EVIDENCE_CLAIM


# v4 (2026-07-27). v3 rejected a DOE announcement that a *fourth* reactor in a
# federal pilot cohort reached criticality, on the grounds that it lacked
# "independent replication" — a bar v3 never set, drawn from the
# physical-realization archetype's signature signals and applied to an item
# whose own headline stated it was the fourth instance. Root causes, all three
# fixed below: one universal bar across every source, so arXiv skepticism was
# applied to a government fact; no instruction that an item may only be rejected
# after failing *every* archetype, so falling short of one archetype's bar ended
# the evaluation; and no handling of cumulative evidence, so "fourth" read as
# "a single anecdote". See DQ-006 and KI-009.
FILTER_SYSTEM_PROMPT = (
    "You are the Tech Watcher bulk filter for a research funnel. You receive one "
    "ingested item (an SEC filing headline, an arXiv abstract, a government "
    "contract award, or an agency press item), its evidence class, and the "
    "world-changer recognition grammar: archetype definitions, signature signals, "
    "and known impostors. Decide whether the item is EVIDENCE that an archetype's "
    "pattern is actually playing out in the real world.\n"
    "Evidence class — apply the matching bar:\n"
    "- attested: an institution with accountability has stated this (SEC filer, "
    "federal spending record, rulemaking, or an agency reporting on its own "
    "program). Presume the event happened. Do NOT demand independent "
    "replication, peer review, or proof of commercial viability as a condition "
    "of the event being real — those bars exist to test unverified claims, and "
    "nothing here is unverified. Your only question is whether the event bears "
    "on an archetype.\n"
    "- claim: an author asserts a result that nobody has validated. Here the "
    "skeptical bars apply in full — an unreplicated headline result is not "
    "evidence that a pattern is playing out.\n"
    "Hard rules:\n"
    "- Evidence means real-world adoption or economics: capacity, capex, pricing, "
    "deployment, revenue attribution, regulatory or clinical milestones. An item "
    "that is merely ABOUT a technology — a new method, model architecture, "
    "benchmark, or simulation result — is NOT evidence, no matter how impressive.\n"
    "- Cumulative evidence counts. If the item states it is the Nth instance, or "
    "reports a cohort or program with several completed instances, that IS "
    "repeatability evidence. Do not dismiss it as a single anecdote, and do not "
    "fault it for lacking replication when it is itself reporting replication.\n"
    "- Reject only after the item fails EVERY archetype. Falling short of one "
    "archetype's bar is not a rejection if the item meets another's — an "
    "attested manufacturing or deployment milestone can be cost-curve evidence "
    "even when it would be a weak physical-realization claim.\n"
    "- If the item matches a known impostor pattern for the archetype you would "
    "otherwise assign, it is not relevant.\n"
    "- Most items are not relevant. When genuinely unsure, say not relevant — but "
    "this tiebreaker never overrides the attested rule above.\n"
    "Respond with ONLY a JSON object: "
    '{"relevant": true|false, "archetype": "<key or null>", "reason": "<one sentence '
    'naming the archetype you tested and the bar you applied>"}. '
    "The archetype must be one of the provided keys or null."
)


@dataclass(frozen=True, slots=True)
class FilterVerdict:
    """One item's relevance verdict."""

    item_id: str
    relevant: bool
    archetype: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class UnfilteredItem:
    """The slice of a raw item the filter needs."""

    item_id: str
    source: str
    kind: str | None
    title: str
    summary: str | None
    document_text: str | None = None
    """The filing body, for sources whose feed summary is metadata (KI-026).

    Defaults to None so every non-EDGAR source and every row predating the
    backfill constructs unchanged. When present it REPLACES the summary in the
    prompt rather than joining it: the EDGAR summary is a file size and an
    accession number, and appending that to a document adds noise to the one
    part of the prompt the model is supposed to weigh.
    """


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


# Sources this filter must not score. `arxiv-qfin` feeds the Hypothesis
# Generator and is judged by `literature_filter.py` under a different prompt
# with a different question.
#
# The exclusion is load-bearing rather than tidy. This prompt's hard rules state
# that an item "merely ABOUT a technology — a new method, model architecture,
# benchmark, or simulation result — is NOT evidence", which is a description of
# every quantitative-finance paper ever written. Without the exclusion the
# q-fin leg would be ingested, marked filtered, rejected in full, and the
# literature funnel would find nothing left to score — while every counter in
# this module reported a healthy pass.
EXCLUDED_SOURCES: frozenset[str] = frozenset({SOURCE_ARXIV_QFIN})

_EXCLUDED_SOURCE_LIST = sorted(EXCLUDED_SOURCES)

SELECT_UNFILTERED_SQL = """
SELECT item_id, source, kind, title, summary, document_text
FROM research.raw_source_items
WHERE filtered_at IS NULL
  AND NOT (source = ANY($2::text[]))
ORDER BY fetched_at
LIMIT $1
""".strip()

# Re-score items already filtered by a different (prompt, model) pair. Without
# this the backlog stays frozen at whatever verdict the configuration of the day
# produced, and a fix only ever reaches items that happen to arrive afterwards.
# The previous re-filter (v2, 2026-07-18) was run as ad-hoc SQL, which is how
# those verdicts were lost (KI-007); this path appends history like any other
# pass.
#
# Keying on prompt version ALONE was too narrow, found live 2026-07-27: after a
# v4 pass on the local model, swapping to a cloud model left every item stamped
# v4, so `prompt_version < 4` matched nothing and the re-filter reported
# "0 item(s) scored" — silently declining to test the very change being made.
# The model is part of the identity of a verdict, so a model change is a reason
# to re-score. `$4` forces a pass regardless, for when neither changed but the
# verdicts are suspect anyway.
SELECT_FOR_REFILTER_SQL = """
SELECT item_id, source, kind, title, summary, document_text,
       COALESCE((filter_result->>'relevant')::boolean, false) AS was_relevant,
       COALESCE((filter_result->>'prompt_version')::int, 0) AS scored_version,
       COALESCE(filter_result->>'model', '') AS scored_model
FROM research.raw_source_items
WHERE filtered_at IS NOT NULL
  AND NOT (source = ANY($6::text[]))
  AND (
      $4::boolean
      OR COALESCE((filter_result->>'prompt_version')::int, 0) < $1
      OR ($5::text <> '' AND COALESCE(filter_result->>'model', '') <> $5)
  )
  AND ($2::text IS NULL OR source = $2)
ORDER BY fetched_at DESC
LIMIT $3
""".strip()

MARK_FILTERED_SQL = """
UPDATE research.raw_source_items
SET filtered_at = $2, filter_result = $3::jsonb
WHERE item_id = $1
""".strip()

INSERT_VERDICT_HISTORY_SQL = """
INSERT INTO research.filter_verdict_history (
    item_id, prompt_version, relevant, archetype, reason, model, decided_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7)
""".strip()


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


def _item_prompt(item: UnfilteredItem) -> str:
    body = (item.document_text or "").strip()
    if body:
        # Labelled `Document` rather than `Summary` on purpose. The two are not
        # the same kind of evidence, and a model told "Summary:" ahead of six
        # thousand characters of filing text is being told something false about
        # what it is reading.
        content = f"Document:\n{body[:DOCUMENT_PROMPT_CHARS]}"
    else:
        summary = (item.summary or "")[:1500]
        content = f"Summary: {summary or '(none)'}"
    return (
        f"Recognition grammar:\n{archetype_filter_prompt_block()}\n\n"
        f"Item (source={item.source}, kind={item.kind or 'unknown'}, "
        f"evidence_class={evidence_class(item.source)}):\n"
        f"Title: {item.title}\n"
        f"{content}"
    )


_CODE_FENCE_RE = re.compile(r"\A```[^\n`]*\n(.*?)\n?```\Z", re.DOTALL)


def _unwrap_code_fence(content: str) -> str:
    """Strip a markdown code fence wrapping the entire response.

    Models under a strict-JSON contract routinely return correct JSON inside
    ```` ```json ```` fences, and rejecting those cost us real verdicts. In the
    2026-07-31 shadow eval ``glm-5.2`` failed 30 of 40 calls this way and every
    one carried extractable JSON — a defect in this parser that read as a
    deficiency in the model, and would have been recorded as one.

    Deliberately narrow: only a fence enclosing the *whole* response is
    unwrapped. There is no scan for an object embedded in prose, because a
    thinking model that reasons in braces before answering would hand a greedy
    match a span of its own scratch work, and scoring on that is worse than
    dropping the item. The funnel's bias is to drop, never to invent.
    """

    match = _CODE_FENCE_RE.match(content.strip())
    return match.group(1) if match is not None else content


def parse_filter_response(item_id: str, content: str) -> FilterVerdict:
    """Parse the model's JSON verdict; anything unusable is not-relevant."""

    try:
        data = json.loads(_unwrap_code_fence(content))
    except json.JSONDecodeError:
        return FilterVerdict(item_id, False, None, "unparseable filter response")
    if not isinstance(data, dict):
        return FilterVerdict(item_id, False, None, "non-object filter response")
    relevant = data.get("relevant") is True
    archetype = data.get("archetype")
    if not isinstance(archetype, str) or archetype not in ARCHETYPE_KEYS:
        archetype = None
    if relevant and archetype is None:
        # Relevant-but-no-recognized-archetype is not actionable evidence.
        relevant = False
    reason = data.get("reason")
    reason_text = reason.strip()[:500] if isinstance(reason, str) else ""
    return FilterVerdict(item_id, relevant, archetype, reason_text or "no reason given")


async def filter_pass(
    pool: AsyncPool,
    llm: CompletionClient,
    max_items: int = 300,
    tier: str = TIER_LOCAL_CLASSIFICATION,
) -> list[FilterVerdict]:
    """Filter one batch of unprocessed items; returns all verdicts.

    Each item is marked ``filtered_at`` individually as it is scored, so a
    crash mid-batch resumes where it left off. An LLM call failure stops the
    pass (systemic — likely Ollama down) leaving remaining items unmarked
    for the next pass.
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(SELECT_UNFILTERED_SQL, max_items, _EXCLUDED_SOURCE_LIST)
    items = [_row_to_item(row) for row in rows]
    return await _score_items(pool, llm, items, tier)


def _row_to_item(row: Mapping[str, Any]) -> UnfilteredItem:
    return UnfilteredItem(
        item_id=str(row["item_id"]),
        source=str(row["source"]),
        kind=None if row["kind"] is None else str(row["kind"]),
        title=str(row["title"]),
        summary=None if row["summary"] is None else str(row["summary"]),
        document_text=(None if row.get("document_text") is None else str(row["document_text"])),
    )


async def _score_items(
    pool: AsyncPool,
    llm: CompletionClient,
    items: Sequence[UnfilteredItem],
    tier: str,
) -> list[FilterVerdict]:
    """Score and persist each item. Shared by the live pass and the re-filter."""

    verdicts: list[FilterVerdict] = []
    for item in items:
        result = await llm.complete(
            tier=tier,
            prompt=_item_prompt(item),
            system=FILTER_SYSTEM_PROMPT,
            json_mode=True,
            think=False,
        )
        verdict = parse_filter_response(item.item_id, result.content)
        decided_at = datetime.now(UTC)
        async with pool.acquire() as conn:
            # History row first (KI-007): a crash between the two leaves the
            # item unfiltered — it gets re-scored, and the extra history row
            # is harmless. The reverse order would lose the verdict.
            await conn.execute(
                INSERT_VERDICT_HISTORY_SQL,
                item.item_id,
                FILTER_PROMPT_VERSION,
                verdict.relevant,
                verdict.archetype,
                verdict.reason,
                result.model,
                decided_at,
            )
            await conn.execute(
                MARK_FILTERED_SQL,
                item.item_id,
                decided_at,
                json.dumps(
                    {
                        "relevant": verdict.relevant,
                        "archetype": verdict.archetype,
                        "reason": verdict.reason,
                        "model": result.model,
                        "prompt_version": FILTER_PROMPT_VERSION,
                    },
                    separators=(",", ":"),
                ),
            )
        verdicts.append(verdict)
        if verdict.relevant:
            log.info(
                "tech_watcher.item_relevant",
                item_id=item.item_id,
                archetype=verdict.archetype,
                reason=verdict.reason,
            )
    return verdicts


@dataclass(frozen=True, slots=True)
class RefilterVerdict:
    """One item's before/after when re-scored under a newer prompt."""

    item_id: str
    source: str
    title: str
    was_relevant: bool
    now_relevant: bool
    archetype: str | None
    reason: str

    @property
    def changed(self) -> bool:
        return self.was_relevant != self.now_relevant


@dataclass(frozen=True, slots=True)
class RefilterReport:
    """Outcome of a re-filter.

    Reports **every** verdict, not only the ones that moved. An earlier version
    printed flips alone, so a run that changed nothing said "0 verdict changes"
    and stopped — which cannot distinguish "the new prompt never reached the
    model" from "the model read it and disagreed anyway." Those need opposite
    fixes, and the reasons are the only thing that tells them apart.
    """

    scored: int
    prompt_version: int
    verdicts: tuple[RefilterVerdict, ...]
    dry_run: bool

    @property
    def flips(self) -> tuple[RefilterVerdict, ...]:
        return tuple(v for v in self.verdicts if v.changed)

    @property
    def rescued(self) -> tuple[RefilterVerdict, ...]:
        """False negatives the new prompt recovered."""
        return tuple(v for v in self.flips if v.now_relevant)

    @property
    def dropped(self) -> tuple[RefilterVerdict, ...]:
        """Previously-kept items the new prompt now rejects."""
        return tuple(v for v in self.flips if not v.now_relevant)

    @property
    def kept(self) -> tuple[RefilterVerdict, ...]:
        """Everything the new prompt scored relevant, moved or not — the set
        that will actually reach clustering."""
        return tuple(v for v in self.verdicts if v.now_relevant)

    def render(self) -> str:
        if self.dry_run:
            # A dry run returns before calling the model, so every count below
            # would be derived from an empty tuple. Printing "0 verdict changes"
            # reads as "the new model agrees with the old one" when nothing was
            # asked — and it was read that way on 2026-07-31, one message after
            # the same class of defect had been fixed twice elsewhere. A dry run
            # knows exactly one thing: how many items are eligible.
            return (
                f"[dry-run] re-filter under prompt v{self.prompt_version}: "
                f"{self.scored} item(s) eligible and NOT scored — the model was "
                "not called, so nothing is known about verdicts.\n"
                "  Re-run without --dry-run to score them. If this equals "
                "--max-items, raise it to see the true eligible count."
            )
        lines = [
            f"re-filter under prompt v{self.prompt_version}: "
            f"{self.scored} item(s) scored, {len(self.flips)} verdict change(s)",
            f"  rescued (false -> true): {len(self.rescued)}   "
            f"dropped (true -> false): {len(self.dropped)}   "
            f"relevant after this pass: {len(self.kept)}",
        ]
        for verdict in self.verdicts:
            if verdict.changed:
                marker = "RESCUED" if verdict.now_relevant else "DROPPED"
            else:
                marker = "kept" if verdict.now_relevant else "-"
            lines.append(
                f"  {marker:8} [{verdict.source}] {verdict.title[:60]}\n"
                f"           archetype={verdict.archetype} — {verdict.reason[:160]}"
            )
        return "\n".join(lines)


async def refilter_pass(
    pool: AsyncPool,
    llm: CompletionClient,
    *,
    max_items: int = 300,
    source: str | None = None,
    tier: str = TIER_LOCAL_CLASSIFICATION,
    current_model: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> RefilterReport:
    """Re-score items whose verdict came from a different prompt *or* model.

    A verdict's identity is the (prompt version, model) pair, so passing
    ``current_model`` makes a model swap a reason to re-score — without it, a
    model change under an unchanged prompt selects nothing and the pass
    silently declines to test the change. ``force`` re-scores the selection
    regardless of either.

    Safe by construction: ``filter_verdict_history`` is append-only, so prior
    verdicts survive and the before/after comparison stays queryable (KI-007 —
    the v2 re-filter was ad-hoc SQL and lost exactly this).

    ``dry_run`` selects and reports the candidate set without calling the model
    or writing anything, so the size of a re-filter is knowable before it runs.
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            SELECT_FOR_REFILTER_SQL,
            FILTER_PROMPT_VERSION,
            source,
            max_items,
            force,
            current_model or "",
            _EXCLUDED_SOURCE_LIST,
        )

    prior = {
        str(row["item_id"]): (bool(row["was_relevant"]), str(row["title"]), str(row["source"]))
        for row in rows
    }
    if dry_run:
        return RefilterReport(
            scored=len(rows), prompt_version=FILTER_PROMPT_VERSION, verdicts=(), dry_run=True
        )

    items = [_row_to_item(row) for row in rows]
    scored = await _score_items(pool, llm, items, tier)

    rows_out: list[RefilterVerdict] = []
    for verdict in scored:
        was_relevant, title, item_source = prior[verdict.item_id]
        rows_out.append(
            RefilterVerdict(
                item_id=verdict.item_id,
                source=item_source,
                title=title,
                was_relevant=was_relevant,
                now_relevant=verdict.relevant,
                archetype=verdict.archetype,
                reason=verdict.reason,
            )
        )
    return RefilterReport(
        scored=len(scored),
        prompt_version=FILTER_PROMPT_VERSION,
        verdicts=tuple(rows_out),
        dry_run=False,
    )
