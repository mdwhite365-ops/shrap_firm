"""The constrained transformation: one published claim in, one spec out.

The spec's own framing (``docs/agents/research/hypothesis-generator.md``): *"turn
a documented effect into a spec, state what would falsify it, and refuse if it
cannot name the source. That is a constrained transformation rather than an
invention."* Everything here exists to keep it constrained.

**The model does not write rules.** It picks a rule from the engine's registry
and names an effect. It cannot emit pseudocode, because pseudocode the engine
cannot execute is a proposal that dies on arrival while looking like progress.

**The model does not choose construction.** ``top_n``, ``gross_exposure`` and
``long_short`` are fixed at the family defaults and are not in the schema. Held
identical across every proposal, a comparison between two of them measures the
*effects*; free to vary, it measures two implementations, and the firm learns
nothing about either. This is the same discipline ``factor_strategies.py``
applies to the four hand-written seeds, enforced rather than remembered.

The one parameter the model does supply is ``lookback``, and only because the
paper states it. A formation window is a fact about the source, not a knob —
which is why the validator bounds-checks it and the identity key ignores it.

**Nothing here decides anything.** Parsing is total: any response that is not
usable becomes ``None`` and the item is refused. The judgments — is there a
prior, is this already held, is it in bounds — belong to ``validate.py``, where
they are deterministic and testable without a model.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from shrap.research.hypothesis_generator.expressible import (
    AVAILABLE_SERIES,
    EXPRESSIBLE_RULES,
    FACTOR_BEARING_RULES,
    FACTOR_DESCRIPTIONS,
)
from shrap.research.hypothesis_generator.literature import LiteratureItem

# Bump on any behaviour-relevant prompt change. Stamped onto every proposal's
# spec so a later review knows which prompt produced it — the same discipline
# the Tech Watcher filter learned the hard way (KI-007).
PROPOSER_PROMPT_VERSION = 1

# How much of an abstract the model sees. arXiv abstracts run ~1500 characters;
# the cap is a guard against a pathological item, not a summarisation step.
MAX_ABSTRACT_CHARS = 4000


def _rules_block() -> str:
    lines = [
        "  cross-sectional-momentum — rank the universe by trailing return over "
        "`lookback` sessions skipping the most recent `skip`, hold the top. "
        "Horizon must be 21-504 sessions.",
        "  cross-sectional-reversal — the same shape at a short horizon, holding "
        "the LOSERS. Horizon must be 2-21 sessions.",
        "  cross-sectional-factor — rank the universe by one named factor, hold "
        "the top. Horizon must be 20-504 sessions. Implemented factors:",
    ]
    lines.extend(f"      {name} — {desc}" for name, desc in sorted(FACTOR_DESCRIPTIONS.items()))
    return "\n".join(lines)


PROPOSER_SYSTEM_PROMPT = (
    "You are the Hypothesis Generator for a systematic trading research firm. You "
    "receive one published item (title and abstract) and decide whether it "
    "describes a testable cross-sectional equity market effect that the firm's "
    "backtest engine could implement.\n"
    "\n"
    "You are a TRANSLATOR, not an inventor. Your job is to say what the paper "
    "claims and how it would be built. You never invent an effect the paper does "
    "not describe, and you never soften a paper's construction to make it fit the "
    "engine.\n"
    "\n"
    "The engine reads exactly two daily series per stock: close and volume. It has "
    "no fundamentals, no shares outstanding, no intraday bars, no options, no "
    "short interest, no analyst or news data.\n"
    "\n"
    "The rules it can run:\n"
    f"{_rules_block()}\n"
    "\n"
    "HARD RULES.\n"
    "1. `prior` is mandatory. Name the authors, the year, and the claim in one "
    "sentence, taken from the item itself. If the item does not attribute its "
    "claim to identifiable authors, set `is_market_effect` to false.\n"
    "2. Name an implemented factor ONLY if the paper's effect is that exact "
    "effect. If it is a different effect — even a close cousin — invent a new "
    "kebab-case `effect_name` and describe the computation in `scorer_sketch`. "
    "Reporting a new effect the firm cannot yet run is a SUCCESS, not a failure. "
    "Forcing a paper onto the nearest implemented factor is the worst thing you "
    "can do, because the firm would then hold a strategy citing a paper it does "
    "not implement.\n"
    "3. `required_inputs` must list every data series the effect needs, using the "
    "words `close` and `volume` where those suffice and plain English otherwise "
    "(for example `shares outstanding`, `intraday prices`, `book value`). Be "
    "complete and be literal — this list is what decides whether the firm can "
    "test the effect at all.\n"
    "4. `lookback` is the formation window the PAPER uses, in trading sessions "
    "(a month is 21, a year is 252). Never a number you chose because it seemed "
    "reasonable. If the paper does not state one, use the convention for that "
    "effect and say so in `deviation`.\n"
    "5. `deviation` states how an implementation on close and volume alone would "
    "differ from the paper's construction, or the literal string `none`. Be "
    "specific. A dropped short leg, a close used where the paper used an "
    "intraday high, a universe of 50 large caps where the paper used all of "
    "CRSP — each is a deviation and each must be named.\n"
    "6. `kill_criteria` names the specific ways THIS effect is known to fail, not "
    "generic risk language. Two to four items.\n"
    "\n"
    "Most published items are not testable market effects. Methods papers, "
    "surveys, market microstructure theory, machine-learning architectures and "
    "asset-pricing econometrics are all interesting and all outside this brief. "
    "When the item does not describe a cross-sectional effect over listed "
    "equities that ranks names and holds a subset, set `is_market_effect` to "
    "false and say why in one sentence.\n"
    "\n"
    "Respond with ONLY a JSON object:\n"
    '{"is_market_effect": true|false, "reason": "<one sentence>", '
    '"effect_name": "<kebab-case>", '
    '"prior": {"authors": "<names>", "year": <int>, "claim": "<one sentence>"}, '
    '"rule": "<one of the rules above>", "factor": "<implemented factor or null>", '
    '"lookback": <int sessions>, "required_inputs": ["<series>", ...], '
    '"scorer_sketch": "<how the per-stock score is computed, 1-2 sentences>", '
    '"deviation": "<text or none>", "kill_criteria": ["<...>"], '
    '"thesis": "<one paragraph: the claim, the mechanism, and why it should '
    'persist>"}'
)


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


@dataclass(frozen=True, slots=True)
class Prior:
    """The citation. Without one there is no proposal — see ``validate.py``."""

    authors: str
    year: int
    claim: str

    def render(self) -> str:
        return f"{self.authors} ({self.year}): {self.claim}"


@dataclass(frozen=True, slots=True)
class RawProposal:
    """Exactly what the model is permitted to say. Unvalidated."""

    item_id: str
    is_market_effect: bool
    reason: str
    effect_name: str
    prior: Prior | None
    rule: str
    factor: str | None
    lookback: int | None
    required_inputs: tuple[str, ...]
    scorer_sketch: str
    deviation: str
    kill_criteria: tuple[str, ...]
    thesis: str
    model: str
    """Which model said it. Part of a verdict's identity (KI-007)."""


def build_prompt(item: LiteratureItem) -> str:
    abstract = item.abstract[:MAX_ABSTRACT_CHARS] or "(no abstract)"
    return (
        f"Item (source={item.source}, category={item.category or 'unknown'}):\n"
        f"Reference: {item.citation_hint}\n"
        f"Title: {item.title}\n"
        f"Abstract: {abstract}"
    )


def _clean(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _parse_prior(raw: object) -> Prior | None:
    if not isinstance(raw, Mapping):
        return None
    authors = _clean(raw.get("authors"), 200)
    claim = _clean(raw.get("claim"), 500)
    year_raw = raw.get("year")
    try:
        year = int(str(year_raw))
    except (TypeError, ValueError):
        return None
    if not authors or not claim:
        return None
    return Prior(authors=authors, year=year, claim=claim)


def _parse_strings(raw: object, limit: int, max_items: int) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for entry in raw[:max_items]:
        text = _clean(entry, limit)
        if text and text not in out:
            out.append(text)
    return tuple(out)


def parse_proposal(item: LiteratureItem, content: str, model: str) -> RawProposal | None:
    """Parse the model's JSON. Anything unusable is ``None``, never a guess.

    Total by construction. The generator refuses an item it cannot parse, which
    costs one wasted call and no correctness — where a lenient parser filling in
    defaults would produce a strategy nobody proposed.
    """

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    rule = _clean(data.get("rule"), 60)
    factor_raw = data.get("factor")
    factor = _clean(factor_raw, 60) or None
    # A factor named on a rule that does not dispatch on one would be recorded in
    # the spec and ignored by the engine — a spec that lies about what it runs.
    if rule not in FACTOR_BEARING_RULES:
        factor = None
    lookback_raw = data.get("lookback")
    try:
        lookback: int | None = int(str(lookback_raw))
    except (TypeError, ValueError):
        lookback = None

    return RawProposal(
        item_id=item.item_id,
        is_market_effect=data.get("is_market_effect") is True,
        reason=_clean(data.get("reason"), 500) or "no reason given",
        effect_name=_clean(data.get("effect_name"), 60).lower().replace(" ", "-"),
        prior=_parse_prior(data.get("prior")),
        rule=rule,
        factor=factor,
        lookback=lookback,
        required_inputs=_parse_strings(data.get("required_inputs"), 80, 12),
        scorer_sketch=_clean(data.get("scorer_sketch"), 600),
        deviation=_clean(data.get("deviation"), 1000) or "none",
        kill_criteria=_parse_strings(data.get("kill_criteria"), 400, 6),
        thesis=_clean(data.get("thesis"), 3000),
        model=model,
    )


async def propose(
    llm: CompletionClient,
    item: LiteratureItem,
    tier: str,
    temperature: float = 0.2,
) -> RawProposal | None:
    """One model call for one item."""

    result = await llm.complete(
        tier=tier,
        prompt=build_prompt(item),
        system=PROPOSER_SYSTEM_PROMPT,
        json_mode=True,
        temperature=temperature,
        think=True,
    )
    return parse_proposal(item, result.content, result.model)


def available_series_sentence() -> str:
    """Used in refusal text so a person reading a rejection knows the boundary."""

    return ", ".join(sorted(AVAILABLE_SERIES))


def known_rules() -> Sequence[str]:
    return sorted(EXPRESSIBLE_RULES)


__all__ = [
    "MAX_ABSTRACT_CHARS",
    "PROPOSER_PROMPT_VERSION",
    "PROPOSER_SYSTEM_PROMPT",
    "CompletionClient",
    "Prior",
    "RawProposal",
    "available_series_sentence",
    "build_prompt",
    "known_rules",
    "parse_proposal",
    "propose",
]
