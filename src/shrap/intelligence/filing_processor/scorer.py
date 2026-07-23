"""Per-item materiality scoring for 8-K sections (spec Processing steps 5-6).

Each declared item code gets one ``local-classification`` call, ``think:false``,
strict JSON. The item code seeds a prior in the prompt (1.01/2.01/2.02/3.01/
4.02/5.01 skew high; 5.03/9.01 skew low; 5.02/7.01/8.01 are genuinely mixed and
the text decides). Material items (materiality >= threshold) get one
``cloud-default`` re-read producing a tighter summary. Unparseable responses
score materiality 0 and are logged — the bias is to drop, never to invent. No
direction hints, no sentiment: materiality and category only (spec §Purpose).

Prompt/parsing live here so tests can target them without a DB or network; the
orchestration (poll → fetch → score → publish) lives in
:mod:`shrap.intelligence.filing_processor.service`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

# Bump on any behavior-relevant prompt change; stamped into every history row
# so calibration reviews know which prompt scored each item (KI-007).
FILING_PROMPT_VERSION = 1

MIN_MATERIALITY = 0
MAX_MATERIALITY = 3

# Longest item section handed to the model; 8-K item bodies are small, and the
# tail of a long exhibit adds tokens without changing materiality.
SECTION_CHAR_LIMIT = 6000

FILING_CATEGORIES: frozenset[str] = frozenset(
    {
        "material-agreement",
        "results",
        "officer-change",
        "control-change",
        "impairment",
        "delisting",
        "accountant-change",
        "other-events",
        "other",
    }
)

# Item-code priors (spec Processing step 5) — context for the model, never a
# hard override. Codes not listed carry no strong prior.
ITEM_CODE_PRIORS: dict[str, str] = {
    "1.01": "high — entry into a material definitive agreement",
    "2.01": "high — completion of an acquisition or disposition of assets",
    "2.02": "high — results of operations and financial condition (earnings)",
    "3.01": "high — notice of delisting or failure to satisfy a listing rule",
    "4.02": "high — non-reliance on previously issued financials (restatement)",
    "5.01": "high — change in control of the registrant",
    "5.03": "low — amendments to bylaws or a change in fiscal year",
    "9.01": "low — financial statements and exhibits (usually boilerplate)",
    "5.02": "mixed — departure or appointment of directors or officers",
    "7.01": "mixed — Regulation FD disclosure",
    "8.01": "mixed — other events, a catch-all from boilerplate to highly material",
}

FILING_SYSTEM_PROMPT = (
    "You are the Filing Processor for a systematic trading firm. You receive one "
    "item section from an SEC Form 8-K for one of the firm's tradeable symbols, "
    "identified by its declared 8-K item code. Classify just this item. You "
    "produce INPUTS, NOT OPINIONS: no direction hints, no trade suggestions, no "
    "sentiment — materiality and category only.\n"
    "Fields:\n"
    "- relevant: is this a genuine, substantive disclosure about the issuer, not "
    "a routine or boilerplate co-filed item?\n"
    "- symbols: the subset of the provided symbols this item is material to.\n"
    "- item_code: echo the declared 8-K item code you were given.\n"
    "- category: one of material-agreement, results, officer-change, "
    "control-change, impairment, delisting, accountant-change, other-events, "
    "other.\n"
    "- materiality: 0-3. 0 = boilerplate/administrative; 1 = minor but real "
    "company news; 2 = a market-moving event a holder would want to know "
    "same-day; 3 = a decisive, unambiguous shock.\n"
    "- summary: one factual sentence, no adjectives of opinion.\n"
    "The item code carries a prior, given in the prompt as context — weigh it, "
    "but the text decides and the prior never overrides what the item actually "
    "says. When unsure, score materiality lower. Respond with ONLY a JSON "
    'object: {"relevant": true|false, "symbols": ["..."], "item_code": "<code>", '
    '"category": "<one of the categories>", "materiality": 0-3, "summary": '
    '"<one sentence>"}.'
)


@dataclass(frozen=True, slots=True)
class FilingVerdict:
    """One 8-K item section's materiality verdict."""

    accession: str
    item_code: str
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


def build_prompt(
    company: str | None, item_code: str, section_text: str, symbols: tuple[str, ...]
) -> str:
    """Render the per-item scoring prompt with the item-code prior as context."""

    prior = ITEM_CODE_PRIORS.get(item_code, "no strong prior — the text decides")
    body = section_text[:SECTION_CHAR_LIMIT].strip()
    symbol_line = ", ".join(symbols) or "(none listed)"
    return (
        f"Company: {company or '(unknown filer)'}\n"
        f"Symbols: {symbol_line}\n"
        f"Declared 8-K item code: {item_code}\n"
        f"Item-code prior: {prior}\n"
        f"Item text:\n{body or '(none)'}"
    )


def _drop(accession: str, item_code: str, reason: str) -> FilingVerdict:
    """A verdict that stores but never publishes (materiality 0)."""

    return FilingVerdict(
        accession=accession,
        item_code=item_code,
        relevant=False,
        symbols=(),
        category="other",
        materiality=MIN_MATERIALITY,
        summary=reason,
    )


def parse_filing_response(
    accession: str, item_code: str, content: str, fallback_symbols: tuple[str, ...]
) -> FilingVerdict:
    """Parse the model's JSON verdict; anything unusable scores materiality 0.

    ``item_code`` is authoritative from our own extraction — the model is asked
    to echo it, but we never trust its echo over the declared code.
    """

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return _drop(accession, item_code, "unparseable filing response")
    if not isinstance(data, dict):
        return _drop(accession, item_code, "non-object filing response")

    raw_materiality = data.get("materiality")
    if isinstance(raw_materiality, bool) or not isinstance(raw_materiality, int):
        return _drop(accession, item_code, "missing or non-integer materiality")
    materiality = max(MIN_MATERIALITY, min(MAX_MATERIALITY, raw_materiality))

    category = data.get("category")
    if not isinstance(category, str) or category not in FILING_CATEGORIES:
        category = "other"

    relevant = data.get("relevant") is True

    raw_symbols = data.get("symbols")
    symbols: tuple[str, ...] = ()
    if isinstance(raw_symbols, list):
        symbols = tuple(s.strip().upper() for s in raw_symbols if isinstance(s, str) and s.strip())
    # Keep only symbols the filing actually carried — never invent coverage.
    if fallback_symbols:
        symbols = tuple(s for s in symbols if s in fallback_symbols)
    if not symbols:
        symbols = fallback_symbols

    raw_summary = data.get("summary")
    summary = raw_summary.strip()[:500] if isinstance(raw_summary, str) else ""

    return FilingVerdict(
        accession=accession,
        item_code=item_code,
        relevant=relevant,
        symbols=symbols,
        category=category,
        materiality=materiality,
        summary=summary or "(no summary)",
    )


def higher_verdict(a: FilingVerdict, b: FilingVerdict) -> FilingVerdict:
    """Return the higher-materiality verdict; ties prefer ``b`` (the re-read).

    The escalation call (``b``) is the tighter, more expensive summary, so on a
    materiality tie it wins for publishing (spec Processing step 6).
    """

    return a if a.materiality > b.materiality else b


__all__ = [
    "FILING_CATEGORIES",
    "FILING_PROMPT_VERSION",
    "FILING_SYSTEM_PROMPT",
    "ITEM_CODE_PRIORS",
    "MAX_MATERIALITY",
    "MIN_MATERIALITY",
    "SECTION_CHAR_LIMIT",
    "CompletionClient",
    "FilingVerdict",
    "build_prompt",
    "higher_verdict",
    "parse_filing_response",
]
