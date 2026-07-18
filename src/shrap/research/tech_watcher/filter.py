"""Bulk relevance filter (Tech Watcher spec Processing step 2).

Scores each unfiltered raw item against the world-changer archetype
vocabulary on the ``local-classification`` tier with thinking disabled —
this is a yes/no/which-archetype call over hundreds of items, not a
judgment turn. Items are marked ``filtered_at`` either way; relevant ones
carry the archetype key and reason in ``filter_result`` for the clustering
step. An unparseable model response counts as not-relevant and is logged —
the funnel's bias is to drop, never to invent.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from shrap.llm.registry import TIER_LOCAL_CLASSIFICATION
from shrap.research.tech_watcher.archetypes import ARCHETYPE_KEYS, archetype_prompt_block

log = structlog.get_logger(__name__)

FILTER_SYSTEM_PROMPT = (
    "You are the Tech Watcher bulk filter for a research funnel. You receive one "
    "ingested item (an SEC filing headline or an arXiv abstract) and the allowed "
    "world-changer archetype vocabulary. Decide whether the item is plausible "
    "EVIDENCE for any archetype — a real-world signal that the pattern may be "
    "playing out. Most items are not relevant; when unsure, say not relevant. "
    "Respond with ONLY a JSON object: "
    '{"relevant": true|false, "archetype": "<key or null>", "reason": "<one sentence>"}. '
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


SELECT_UNFILTERED_SQL = """
SELECT item_id, source, kind, title, summary
FROM research.raw_source_items
WHERE filtered_at IS NULL
ORDER BY fetched_at
LIMIT $1
""".strip()

MARK_FILTERED_SQL = """
UPDATE research.raw_source_items
SET filtered_at = $2, filter_result = $3::jsonb
WHERE item_id = $1
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
    summary = (item.summary or "")[:1500]
    return (
        f"Archetype vocabulary:\n{archetype_prompt_block()}\n\n"
        f"Item (source={item.source}, kind={item.kind or 'unknown'}):\n"
        f"Title: {item.title}\n"
        f"Summary: {summary or '(none)'}"
    )


def parse_filter_response(item_id: str, content: str) -> FilterVerdict:
    """Parse the model's JSON verdict; anything unusable is not-relevant."""

    try:
        data = json.loads(content)
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
        rows = await conn.fetch(SELECT_UNFILTERED_SQL, max_items)
    items = [
        UnfilteredItem(
            item_id=str(row["item_id"]),
            source=str(row["source"]),
            kind=None if row["kind"] is None else str(row["kind"]),
            title=str(row["title"]),
            summary=None if row["summary"] is None else str(row["summary"]),
        )
        for row in rows
    ]
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
        async with pool.acquire() as conn:
            await conn.execute(
                MARK_FILTERED_SQL,
                item.item_id,
                datetime.now(UTC),
                json.dumps(
                    {
                        "relevant": verdict.relevant,
                        "archetype": verdict.archetype,
                        "reason": verdict.reason,
                        "model": result.model,
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
