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
    RULE_SIGNAL_SPEC,
)
from shrap.research.strategy_evaluator.signals import (
    ACCOUNTING_FEATURES,
    Node,
    parse,
)

# The only series a strategy can read. Read off `PanelWindow`, which exposes
# `closes()`, `volumes()` and `market_caps()` and nothing else.
#
# **`market cap` joined on 2026-09-20 and it is the first addition here.** It is
# the cheapest of KI-035's seven `missing-data` gaps — `volatility-rank-forecast`
# asks only for market capitalisation — and it is now `close x shares
# outstanding`, computed on the panel's own closes against share counts filed on
# or before each date. Coverage is 36 of the 40 non-ETF names; the four absent
# ones are multi-class issuers SEC publishes no point-in-time count for (#253),
# and they carry `nan` rather than a guess.
AVAILABLE_SERIES: frozenset[str] = frozenset({"close", "volume", "market cap"})

# Rules the proposer may name. Deliberately narrower than the engine's full set:
# `reference-trend` trades a single ticker (it is the fixture's rule, not a
# cross-sectional hypothesis) and `cross-sectional-trend` has no documented
# effect behind it. A proposer that could name them would be proposing
# strategies nobody has a prior for, which is the freelancing this whole agent
# exists to stop.
#
# **`signal-spec` joined on 2026-09-27 and it changes what a gap is.** Before it,
# an effect was expressible only if it WAS one of four hand-written scorers, so
# nearly every paper became a `missing-scorer` gap and waited for a human to
# write the function. A spec (#278) is a formula over a fixed feature library,
# validated by the same parser the Evaluator runs, so an effect that is a
# formula over those features is now a strategy the same evening. What stays a
# gap is what the library genuinely cannot compute.
EXPRESSIBLE_RULES: frozenset[str] = frozenset(
    {
        RULE_CROSS_SECTIONAL_FACTOR,
        RULE_CROSS_SECTIONAL_MOMENTUM,
        RULE_CROSS_SECTIONAL_REVERSAL,
        RULE_SIGNAL_SPEC,
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

# Filed accounting figures (#280), point in time on the SEC filing date. Keyed by
# the metric name a spec uses; the values are the wordings that unambiguously
# mean that figure. Deliberately absent: anything derived (`book-to-market`,
# `ROE`, `accruals`) — those are formulas a spec builds with `div`/`sub`, and
# naming the ratio as an input would hide the construction from the classifier —
# and anything the firm does not store (`EBITDA`, `free cash flow`, `dividends`,
# `analyst forecasts`, `insider holdings`), which stay `missing-data`.
FILED_FIGURES: Mapping[str, tuple[str, ...]] = {
    "revenue": ("revenue", "revenues", "sales", "net sales", "total revenue"),
    "gross_profit": ("gross profit", "gross profits"),
    "operating_income": ("operating income", "operating profit", "ebit"),
    "net_income": ("net income", "earnings", "net earnings", "net profit"),
    "operating_cash_flow": (
        "operating cash flow",
        "cash flow from operations",
        "cash from operations",
    ),
    "rd_expense": ("r&d", "r&d expense", "research and development", "r and d"),
    "total_assets": ("total assets", "assets", "book assets"),
    "stockholders_equity": (
        "book equity",
        "book value of equity",
        "book value",
        "stockholders equity",
        "stockholders' equity",
        "shareholders equity",
        "shareholders' equity",
        "common equity",
    ),
    "long_term_debt": ("long term debt", "long-term debt"),
}

# One line per spec feature, shown to the proposer. A test holds this in step
# with the library: a feature the prompt does not describe is one the model
# cannot choose, and a description of a removed feature is a spec that fails.
FEATURE_DESCRIPTIONS: Mapping[str, str] = {
    "return": "trailing return over `lookback` sessions, skipping the latest `skip` (default 0)",
    "abs_return": "absolute trailing return over `lookback` sessions",
    "volatility": "standard deviation of daily returns over `lookback` sessions",
    "volume_ratio": (
        "mean volume over the latest `recent` sessions divided by mean volume over `lookback`"
    ),
    "high_proximity": "close divided by the highest close over `lookback` sessions",
    "sma_ratio": "`fast`-session moving average of close divided by the `slow`-session one",
    "bollinger_z": "(close - `lookback` moving average) / `lookback` standard deviation",
    "rsi": "relative strength index over `lookback` sessions (default 14), 0-100",
    "macd": "MACD histogram (12/26/9) divided by price",
    "donchian": "position of close within the `lookback`-session high-low channel, 0-1",
    "market_cap": "close x filed shares outstanding",
    "fundamental": "the latest filed annual/instant value of `metric`",
    "fundamental_growth": "year-on-year growth of `metric`, both years as filed by then",
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
    # Market capitalisation. Every entry is a wording for the same quantity —
    # price times shares outstanding — and nothing here is a construction.
    #
    # Deliberately absent, and each for a reason: `float` and `free float market
    # cap` exclude insider and restricted holdings, which the firm does not
    # store; `enterprise value` adds debt and subtracts cash; `book value` and
    # `book-to-market` are accounting figures, not price times a share count.
    # None of them may be served a market cap. Book value is now reachable
    # another way — as the filed figure `stockholders_equity` (#280) — and
    # book-to-market as a spec formula over it; neither goes through here.
    "market cap": "market cap",
    "market caps": "market cap",
    "market capitalization": "market cap",
    "market capitalisation": "market cap",
    "market value": "market cap",
    "market value of equity": "market cap",
    "size": "market cap",
    "firm size": "market cap",
    "company size": "market cap",
}

# `normalise_input` keys, not display wordings: `_key` folds hyphens and case.
#
# Each metric's own name is a wording too. The prompt lists the metrics by name,
# so the model echoes them back — `rd expense` for `rd_expense` — and on the
# 374-paper q-fin run (2026-09-27) two papers were marked `missing-data` for
# naming a figure the firm stores, because only `r&d expense` was listed.
_FILED_FIGURE_SYNONYMS: Mapping[str, str] = {
    " ".join(w.lower().replace("-", " ").replace("_", " ").split()): metric
    for metric, wordings in FILED_FIGURES.items()
    for w in (metric, *wordings)
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
    return (
        _SERIES_SYNONYMS.get(key)
        or _INTRADAY_SERIES_SYNONYMS.get(key)
        or _FILED_FIGURE_SYNONYMS.get(key)
    )


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
    # Only a spec can read a filed figure; the four named scorers read prices.
    # A factor proposal citing book equity would be built on closes alone.
    if rule != RULE_SIGNAL_SPEC and any(_key(n) in _FILED_FIGURE_SYNONYMS for n in required):
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


def _shape(node: Node) -> str:
    if node.kind == "const":
        return "c"
    if node.kind == "feature":
        return f"{node.name}({node.metric})" if node.metric else node.name
    return f"{node.name}({','.join(_shape(c) for c in node.children)})"


def signal_shape(signal: object, select: str) -> str:
    """A spec's identity: its selection and the shape of its expression.

    **Numbers are not part of it** — lookbacks, windows and constants — for the
    reason ``hypothesis_key`` ignores ``lookback``: RSI(2) below 10 and RSI(3)
    below 20 are one hypothesis at two parameterisations, and letting the second
    in as a fresh root is a parameter search past the multiple-testing gate.
    Raises :class:`SignalSpecError` for an expression the parser refuses.
    """

    node = parse(signal)
    # Holding the top of `-x` is holding the bottom of `x`: one strategy with two
    # spellings. Folded here or it is a second door past `already-held` — the
    # live model wrote `top` of `neg(abs_return)` for an effect the firm would
    # hold as `bottom` of `abs_return` (2026-09-27). Stacked negations cancel.
    flips = {"top": "bottom", "bottom": "top"}
    while node.kind == "op" and node.name == "neg" and select in flips:
        node, select = node.children[0], flips[select]
    return f"{select}:{_shape(node)}"


# Spec shapes that ARE one of the named rules. A spec is a second door to every
# effect the firm already holds, and without these a model could propose
# momentum again by writing it as `top:return`. Registered alongside the named
# identity so either spelling finds the other.
NAMED_RULE_SHAPES: Mapping[str, str] = {
    "top:return": RULE_CROSS_SECTIONAL_MOMENTUM,
    "bottom:return": RULE_CROSS_SECTIONAL_REVERSAL,
    "positive:return": hypothesis_key(RULE_CROSS_SECTIONAL_FACTOR, "time-series"),
    "bottom:volatility": hypothesis_key(RULE_CROSS_SECTIONAL_FACTOR, "low-volatility"),
    "top:high_proximity": hypothesis_key(RULE_CROSS_SECTIONAL_FACTOR, "high-proximity"),
    "top:volume_ratio": hypothesis_key(RULE_CROSS_SECTIONAL_FACTOR, "volume-shock"),
}


def spec_identity(signal: object, select: str) -> tuple[str, ...]:
    """Every key a spec answers to: its own shape, and the named rule it equals."""

    shape = signal_shape(signal, select)
    keys = [hypothesis_key(RULE_SIGNAL_SPEC, shape)]
    if shape in NAMED_RULE_SHAPES:
        keys.append(NAMED_RULE_SHAPES[shape])
    return tuple(keys)


def spec_needs_companies(signal: object) -> bool:
    """True when the expression reads market cap or a filed figure.

    Such a spec can never hold a fund (``nan`` market cap, no filings), so it
    registers over companies and is benchmarked against them (#281).
    """

    def walk(node: Node) -> bool:
        if node.kind == "feature":
            return node.name == "market_cap" or node.name in ACCOUNTING_FEATURES
        return any(walk(c) for c in node.children)

    return walk(parse(signal))


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
    "FEATURE_DESCRIPTIONS",
    "FILED_FIGURES",
    "IMPLEMENTED_FACTORS",
    "NAMED_RULE_SHAPES",
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
    "signal_shape",
    "spec_identity",
    "spec_needs_companies",
]
