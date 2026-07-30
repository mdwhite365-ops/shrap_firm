"""What the firm can actually test, and what it would have to build to test more.

Mike, 2026-07-30: *"id rather not put them in one at a time when there could
1000s to try."* This module is where that runs into the truth.

The engine's expressible space is small. ``PanelWindow`` exposes two series —
closes and volumes — and ``FACTOR_SCORERS`` implements four effects on top of
them. Every strategy the firm has ever evaluated lives inside that box. So a
proposer reading a thousand papers cannot produce a thousand strategies; it can
produce a handful of strategies and **a queue of the things it would need built
to produce the rest**.

That queue is this module's real output. A capability gap is not a failure to
propose — it is the proposer reporting, with citations, which missing scorer the
literature keeps asking for. Ranked by how many independent papers cite it, it is
a build order sourced from the field rather than from whoever is at the keyboard.

**The two ways an effect can be out of reach, and why they are different:**

``missing-scorer``
    Computable from closes and volumes; nobody has written the function.
    Cost: an afternoon. This is the queue worth working.

``missing-data``
    Needs something the firm does not store — fundamentals, shares outstanding,
    intraday bars, options, short interest. Cost: an ingestion pipeline, and
    often a paid feed. Recorded and set aside, not silently dropped: the count
    of these is the honest argument for buying data.

**Bias to out-of-reach.** An input the model names in words this module does not
recognise counts as missing. Guessing that "adjusted closing price" means
``close`` is safe; guessing that "realised variance from intraday returns" means
``close`` would produce a strategy that silently implements a different effect
from the one it cites. The funnel's standing bias is to drop, never to invent.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from shrap.research.strategy_evaluator.factors import ALL_FACTORS
from shrap.research.strategy_evaluator.pipeline import (
    RULE_CROSS_SECTIONAL_FACTOR,
    RULE_CROSS_SECTIONAL_MOMENTUM,
    RULE_CROSS_SECTIONAL_REVERSAL,
)

# The only series a strategy can read. Read off `PanelWindow`, which exposes
# `closes()` and `volumes()` and nothing else.
AVAILABLE_SERIES: frozenset[str] = frozenset({"close", "volume"})

# Rules the proposer may name. Deliberately narrower than the engine's full set:
# `reference-trend` trades a single ticker (it is the fixture's rule, not a
# cross-sectional hypothesis) and `cross-sectional-trend` has no documented
# effect behind it. A proposer that could name them would be proposing
# strategies nobody has a prior for, which is the freelancing this whole agent
# exists to stop.
EXPRESSIBLE_RULES: frozenset[str] = frozenset(
    {
        RULE_CROSS_SECTIONAL_FACTOR,
        RULE_CROSS_SECTIONAL_MOMENTUM,
        RULE_CROSS_SECTIONAL_REVERSAL,
    }
)

# Only `cross-sectional-factor` dispatches on a factor name; the other two ARE
# their effect. Keeping this explicit stops a proposal naming a factor on a rule
# that would ignore it.
FACTOR_BEARING_RULES: frozenset[str] = frozenset({RULE_CROSS_SECTIONAL_FACTOR})

IMPLEMENTED_FACTORS: frozenset[str] = ALL_FACTORS

# One line each, shown to the model so it can tell whether a paper's effect IS
# one of these or merely resembles one. Wording matters: these are the claims,
# not the code.
FACTOR_DESCRIPTIONS: Mapping[str, str] = {
    "low-volatility": "rank by trailing realised volatility of daily returns, hold the calmest",
    "high-proximity": "rank by current close as a fraction of the highest close in the window",
    "volume-shock": "rank by latest volume against the name's own trailing average volume",
    "time-series": "each name's own trailing return, absolute — hold every name above zero",
    "network-peripherality": (
        "rank by how weakly a name's market-adjusted returns correlate with the rest of "
        "the universe, hold the least connected"
    ),
}

# Near-misses that unambiguously mean one of the available series. Every entry is
# a wording difference, never a construction difference: `adjusted close` is a
# close, `realised variance from 5-minute returns` is not, and the second must
# fall through to `missing-data` rather than be quietly normalised.
_SERIES_SYNONYMS: Mapping[str, str] = {
    "close": "close",
    "closes": "close",
    "closing price": "close",
    "closing prices": "close",
    "daily close": "close",
    "daily closing price": "close",
    "adjusted close": "close",
    "price": "close",
    "prices": "close",
    "daily price": "close",
    "daily prices": "close",
    "return": "close",
    "returns": "close",
    "daily return": "close",
    "daily returns": "close",
    "past return": "close",
    "past returns": "close",
    "volume": "volume",
    "volumes": "volume",
    "daily volume": "volume",
    "trading volume": "volume",
    "share volume": "volume",
    "turnover in shares": "volume",
}

# What the proposer is asked to do about each outcome.
OUTCOME_EXPRESSIBLE = "expressible"
OUTCOME_MISSING_SCORER = "missing-scorer"
OUTCOME_MISSING_DATA = "missing-data"


def normalise_input(name: str) -> str | None:
    """Map an input the model named onto an available series, or ``None``.

    ``None`` means "not one of the two series the panel holds" — which is the
    answer for both genuinely exotic inputs and for wordings this table does not
    know. Both are correctly out of reach: the second is a gap in the table, and
    a gap in the table should stop a proposal rather than pass one.
    """

    key = " ".join(str(name).lower().replace("_", " ").replace("-", " ").split())
    return _SERIES_SYNONYMS.get(key)


def missing_inputs(required: Iterable[str]) -> tuple[str, ...]:
    """The named inputs the firm cannot supply, in the model's own words.

    Kept verbatim rather than normalised: the point of recording them is to say
    what data the firm would have to acquire, and "shares outstanding" is a
    procurement decision in a way that a canonical token would not convey.
    """

    out: list[str] = []
    for name in required:
        text = " ".join(str(name).split())
        if not text:
            continue
        if normalise_input(text) is None and text not in out:
            out.append(text)
    return tuple(out)


def classify(rule: str, factor: str | None, required: Iterable[str]) -> str:
    """Can the engine run this today, and if not, what is in the way?

    Data first. An effect needing intraday bars is out of reach whether or not
    its scorer exists, and reporting it as a missing scorer would put an
    unbuildable item at the top of a build queue.
    """

    if missing_inputs(required):
        return OUTCOME_MISSING_DATA
    if rule not in EXPRESSIBLE_RULES:
        return OUTCOME_MISSING_SCORER
    if rule in FACTOR_BEARING_RULES and (factor is None or factor not in IMPLEMENTED_FACTORS):
        return OUTCOME_MISSING_SCORER
    return OUTCOME_EXPRESSIBLE


def hypothesis_key(rule: str, factor: str | None) -> str:
    """The identity of a hypothesis, for the purpose of "have we tried this".

    **Parameters are deliberately not part of it.** A 120-day lookback on an
    effect the firm already holds at 252 days is not a new hypothesis, it is
    attempt 2 of an old one, and the multiple-testing gate (PR #148) exists
    precisely to price that. Nor is ``long_short``: a one-sided version of a
    two-sided effect is a deviation from the same prior, which is what the
    ``deviation`` field is for.

    The consequence is strong and intended — the proposer can mint at most one
    lineage root per key, ever. There is no per-night cap in this agent because
    it cannot flood anything: the space of keys is the space of implemented
    effects, and it is small.
    """

    return f"{rule}:{factor}" if factor else rule


@dataclass(frozen=True, slots=True)
class GapCitation:
    """The published item that asked for a capability the firm lacks."""

    item_id: str
    title: str
    url: str | None
    prior: str

    def as_json(self) -> dict[str, str | None]:
        return {"title": self.title, "url": self.url, "prior": self.prior}


@dataclass(frozen=True, slots=True)
class CapabilityGap:
    """One thing the firm would have to build to test a documented effect."""

    effect_name: str
    kind: str
    """``missing-scorer`` or ``missing-data`` — an afternoon or a feed."""

    missing: tuple[str, ...]
    """For ``missing-data``, the inputs it needs. Empty for a missing scorer."""

    sketch: str
    """How the score would be computed, in one or two sentences. This is what
    makes the gap actionable rather than a complaint."""

    citation: GapCitation

    @property
    def is_buildable(self) -> bool:
        return self.kind == OUTCOME_MISSING_SCORER


@dataclass(frozen=True, slots=True)
class RankedGap:
    """A gap aggregated across every paper that cited it."""

    effect_name: str
    kind: str
    citations: int
    sketch: str
    missing: tuple[str, ...]

    def render(self) -> str:
        need = f" needs: {', '.join(self.missing)}" if self.missing else ""
        return (
            f"  [{self.citations:>2}x] {self.effect_name} ({self.kind}){need}\n"
            f"        {self.sketch[:180]}"
        )


def rank_gaps(gaps: Sequence[CapabilityGap]) -> tuple[RankedGap, ...]:
    """Aggregate gaps into a build queue, most-cited first.

    Buildable gaps sort ahead of data gaps at equal citation counts. Both are
    real, but only one of them is work the firm can start this evening.
    """

    grouped: dict[str, list[CapabilityGap]] = {}
    for gap in gaps:
        grouped.setdefault(gap.effect_name, []).append(gap)
    ranked = [
        RankedGap(
            effect_name=name,
            kind=members[0].kind,
            citations=len({g.citation.item_id for g in members}),
            sketch=members[0].sketch,
            missing=members[0].missing,
        )
        for name, members in grouped.items()
    ]
    ranked.sort(key=lambda g: (-g.citations, g.kind != OUTCOME_MISSING_SCORER, g.effect_name))
    return tuple(ranked)


__all__ = [
    "AVAILABLE_SERIES",
    "EXPRESSIBLE_RULES",
    "FACTOR_DESCRIPTIONS",
    "IMPLEMENTED_FACTORS",
    "OUTCOME_EXPRESSIBLE",
    "OUTCOME_MISSING_DATA",
    "OUTCOME_MISSING_SCORER",
    "CapabilityGap",
    "GapCitation",
    "RankedGap",
    "classify",
    "hypothesis_key",
    "missing_inputs",
    "normalise_input",
    "rank_gaps",
]
