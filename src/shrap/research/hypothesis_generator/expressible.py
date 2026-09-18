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

# Wordings that unambiguously mean one of the available series AT A FINER GRAIN.
# Separate from `_SERIES_SYNONYMS` because they carry a second fact: the panel
# they need is intraday, and a strategy built from them at a daily grain is not
# the effect the paper described. `classify` enforces that — an intraday input
# without an intraday cadence stays out of reach rather than being built daily.
#
# THE ADMISSION RULE IS UNCHANGED, only its inputs. Every entry here is still a
# wording difference and never a construction difference. `intraday close` is a
# close; `realised variance from 5-minute returns` is a construction over closes
# and is NOT here, because the firm has no scorer that reads an intraday series
# and emits a daily one. Deliberately absent for the same reason: `intraday
# bars` and `intraday high` (the panel exposes closes and volumes, not highs or
# lows), `tick data`, `quote data`, `order flow` (a different feed entirely).
_INTRADAY_SERIES_SYNONYMS: Mapping[str, str] = {
    "intraday price": "close",
    "intraday prices": "close",
    "intraday close": "close",
    "intraday closes": "close",
    "intraday closing price": "close",
    "intraday closing prices": "close",
    "intraday return": "close",
    "intraday returns": "close",
    "intraday volume": "volume",
    "intraday volumes": "volume",
    "minute close": "close",
    "minute closes": "close",
    "minute price": "close",
    "minute prices": "close",
    "minute return": "close",
    "minute returns": "close",
    "minute volume": "volume",
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


def _key(name: str) -> str:
    """One spelling for a named input. ``5-minute`` and ``5 minute`` are one key.

    A leading interval is dropped, so ``5 minute returns`` and ``15 minute
    returns`` both reach ``minute returns``. Which interval a paper used is a
    parameter of the strategy, not a question about whether the firm holds the
    data — the backfill stores whatever grain it is asked for.
    """

    text = " ".join(str(name).lower().replace("_", " ").replace("-", " ").split())
    parts = text.split()
    if parts and parts[0].isdigit():
        parts = parts[1:]
    return " ".join(parts)


def normalise_input(name: str) -> str | None:
    """Map an input the model named onto an available series, or ``None``.

    ``None`` means "not one of the two series the panel holds" — which is the
    answer for both genuinely exotic inputs and for wordings this table does not
    know. Both are correctly out of reach: the second is a gap in the table, and
    a gap in the table should stop a proposal rather than pass one.

    An intraday wording resolves to the same series as its daily sibling. The
    *grain* it also implies is a separate question, asked by
    :func:`needs_intraday`, because the answer changes what must be true of the
    proposal rather than whether the series exists.
    """

    key = _key(name)
    return _SERIES_SYNONYMS.get(key) or _INTRADAY_SERIES_SYNONYMS.get(key)


def needs_intraday(required: Iterable[str]) -> bool:
    """True when any named input only makes sense on an intraday panel.

    Kept apart from :func:`normalise_input` so that "can the firm supply this
    series" and "at what grain" stay two questions. Before #234 and #236 the
    answer to the second was always "daily" and this function could not have
    existed; the gate it guards is the reason it now must.
    """

    return any(_key(name) in _INTRADAY_SERIES_SYNONYMS for name in required)


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


def classify(
    rule: str,
    factor: str | None,
    required: Iterable[str],
    cadence_minutes: int | None = None,
) -> str:
    """Can the engine run this today, and if not, what is in the way?

    Data first. An effect needing a series the firm does not hold is out of
    reach whether or not its scorer exists, and reporting it as a missing
    scorer would put an unbuildable item at the top of a build queue.

    **Intraday inputs became reachable and brought a new way to be wrong.**
    Until #234 the Runner held one bar reader and an intraday effect could not
    be run at all, so ``intraday returns`` was correctly ``missing-data``. It is
    now buildable — but only as an intraday *strategy*. A proposal naming
    intraday inputs while declaring no cadence would be built on daily bars: a
    six-month ranking where the paper described a fifteen-minute one, citing
    that paper, with every number well-formed. That is the exact failure the
    module docstring's standing bias exists to prevent, arriving through a door
    that did not exist when the bias was written. So the grain is required to be
    declared, and a proposal that does not declare it stays out of reach and is
    recorded as a gap rather than being quietly built at the wrong horizon.
    """

    required = list(required)
    if missing_inputs(required):
        return OUTCOME_MISSING_DATA
    if needs_intraday(required) and cadence_minutes is None:
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
    "needs_intraday",
    "normalise_input",
    "rank_gaps",
]
