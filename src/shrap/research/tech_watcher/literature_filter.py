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
LITERATURE_PROMPT_VERSION = 1

FILTER_KIND = "literature"

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
    "Two calibration notes. A paper testing a KNOWN effect on new data still "
    "counts — a replication that reports the effect's magnitude is a claim about "
    "returns. And a machine-learning paper counts only if the claim is about a "
    "predictor rather than about the model: 'firms with high X outperform' is in, "
    "'our network beats the benchmark on this dataset' is out.\n"
    "\n"
    "You are NOT judging whether the effect is real, whether it still works, or "
    "whether it can be implemented. Something downstream tests all three. You are "
    "deciding only whether there is a claim worth testing.\n"
    "\n"
    "Most papers are rejected. When genuinely unsure, reject.\n"
    "\n"
    "Respond with ONLY a JSON object: "
    '{"testable_effect": true|false, "reason": "<one sentence naming the claimed '
    'predictor and what it predicts, or why it does not qualify>"}'
)

# Only the q-fin leg. `filter.py` holds the mirror of this exclusion, and the two
# must stay in step: an item in neither pool is never scored at all, and an item
# in both is scored twice under prompts that disagree by design.
SELECT_UNFILTERED_LITERATURE_SQL = """
SELECT item_id, source, kind, title, summary, url, external_ts
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
    return LiteratureVerdict(
        item_id=item_id,
        accepted=data.get("testable_effect") is True,
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
    for row in rows:
        item_id = str(row["item_id"])
        title = str(row["title"])
        summary = None if row["summary"] is None else str(row["summary"])
        result = await llm.complete(
            tier=tier,
            prompt=_item_prompt(title, summary),
            system=LITERATURE_SYSTEM_PROMPT,
            json_mode=True,
            think=False,
        )
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
    "LiteratureReport",
    "LiteratureVerdict",
    "literature_pass",
    "parse_literature_response",
]
