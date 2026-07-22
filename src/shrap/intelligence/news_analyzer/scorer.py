"""Materiality scoring for news items (spec Processing steps 2-3).

Each item gets one ``local-classification`` call, ``think:false``, strict
JSON. Material items (materiality >= threshold) get one ``cloud-default``
re-read producing a tighter summary. Unparseable responses score materiality
0 and are logged — the bias is to drop, never to invent. No direction hints,
no sentiment: materiality and category only (spec §Purpose).

Prompt/parsing live here so tests can target them without a DB or network;
the orchestration (fetch → score → escalate → publish) lives in
:mod:`shrap.intelligence.news_analyzer.service`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

# Bump on any behavior-relevant prompt change; stamped into every history row
# so calibration reviews know which prompt scored each item (KI-007).
NEWS_PROMPT_VERSION = 1

MIN_MATERIALITY = 0
MAX_MATERIALITY = 3

NEWS_CATEGORIES: frozenset[str] = frozenset(
    {
        "earnings",
        "guidance",
        "ma",
        "litigation",
        "regulatory",
        "product",
        "management",
        "macro",
        "other",
    }
)

NEWS_SYSTEM_PROMPT = (
    "You are the News Analyzer for a systematic trading firm. You receive one "
    "news item (headline plus a short summary) for one or more of the firm's "
    "tradeable symbols. Classify it. You produce INPUTS, NOT OPINIONS: no "
    "direction hints, no trade suggestions, no sentiment — materiality and "
    "category only.\n"
    "Fields:\n"
    "- relevant: is this genuinely about the listed symbol(s) as issuers "
    "(not a passing mention or an unrelated same-ticker entity)?\n"
    "- symbols: the subset of the provided symbols this item is material to.\n"
    "- category: one of earnings, guidance, ma, litigation, regulatory, "
    "product, management, macro, other.\n"
    "- materiality: 0-3. 0 = noise/recap/promotional; 1 = minor but real "
    "company news; 2 = a market-moving event a holder would want to know "
    "same-day (earnings surprise, guidance change, M&A, major litigation or "
    "regulatory action); 3 = a decisive, unambiguous shock.\n"
    "- summary: one factual sentence, no adjectives of opinion.\n"
    "When unsure, score materiality lower. Respond with ONLY a JSON object: "
    '{"relevant": true|false, "symbols": ["..."], "category": "<one of the '
    'categories>", "materiality": 0-3, "summary": "<one sentence>"}.'
)


@dataclass(frozen=True, slots=True)
class MaterialityVerdict:
    """One item's materiality verdict."""

    item_id: str
    relevant: bool
    symbols: tuple[str, ...]
    category: str
    materiality: int
    summary: str


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


def build_prompt(headline: str, summary: str | None, symbols: tuple[str, ...]) -> str:
    """Render the per-item scoring prompt."""

    body = (summary or "")[:1500]
    symbol_line = ", ".join(symbols) or "(none listed)"
    return f"Symbols: {symbol_line}\nHeadline: {headline}\nSummary: {body or '(none)'}"


def _drop(item_id: str, reason: str) -> MaterialityVerdict:
    """A verdict that stores but never publishes (materiality 0)."""

    return MaterialityVerdict(
        item_id=item_id,
        relevant=False,
        symbols=(),
        category="other",
        materiality=MIN_MATERIALITY,
        summary=reason,
    )


def parse_news_response(
    item_id: str, content: str, fallback_symbols: tuple[str, ...]
) -> MaterialityVerdict:
    """Parse the model's JSON verdict; anything unusable scores materiality 0."""

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return _drop(item_id, "unparseable news response")
    if not isinstance(data, dict):
        return _drop(item_id, "non-object news response")

    raw_materiality = data.get("materiality")
    if isinstance(raw_materiality, bool) or not isinstance(raw_materiality, int):
        return _drop(item_id, "missing or non-integer materiality")
    materiality = max(MIN_MATERIALITY, min(MAX_MATERIALITY, raw_materiality))

    category = data.get("category")
    if not isinstance(category, str) or category not in NEWS_CATEGORIES:
        category = "other"

    relevant = data.get("relevant") is True

    raw_symbols = data.get("symbols")
    symbols: tuple[str, ...] = ()
    if isinstance(raw_symbols, list):
        symbols = tuple(s.strip().upper() for s in raw_symbols if isinstance(s, str) and s.strip())
    # Keep only symbols the item actually carried — never invent coverage.
    if fallback_symbols:
        symbols = tuple(s for s in symbols if s in fallback_symbols)
    if not symbols:
        symbols = fallback_symbols

    raw_summary = data.get("summary")
    summary = raw_summary.strip()[:500] if isinstance(raw_summary, str) else ""

    return MaterialityVerdict(
        item_id=item_id,
        relevant=relevant,
        symbols=symbols,
        category=category,
        materiality=materiality,
        summary=summary or "(no summary)",
    )


def higher_verdict(a: MaterialityVerdict, b: MaterialityVerdict) -> MaterialityVerdict:
    """Return the higher-materiality verdict; ties prefer ``b`` (the re-read).

    The escalation call (``b``) is the tighter, more expensive summary, so on
    a materiality tie it wins for publishing (spec Processing step 3).
    """

    return a if a.materiality > b.materiality else b


__all__ = [
    "MAX_MATERIALITY",
    "MIN_MATERIALITY",
    "NEWS_CATEGORIES",
    "NEWS_PROMPT_VERSION",
    "NEWS_SYSTEM_PROMPT",
    "CompletionClient",
    "MaterialityVerdict",
    "build_prompt",
    "higher_verdict",
    "parse_news_response",
]
