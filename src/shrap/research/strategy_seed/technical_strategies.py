"""Framework #3 seeds — the firm's first strategies that are what they claim to be.

Every strategy the firm has evaluated so far carried the mass-manufactured-fission
``world_changer_id``, including two that were moving-average crossovers on an
energy ETF with no relationship to fission whatsoever. They carried it because the
Evaluator's anchor gate killed anchor-less strategies before the backtest ran, so
a protocol probe had to claim a thesis to be measured at all. That defect is
recorded in ``probe_strategies.py`` and was fixed by ADR-0013's
archetype-conditional gates (PR #102).

This module is the first use of the fix. These records are ``technical-catalyst``
(Framework #3), they carry **no anchor**, and nothing about them is a claim about
the physical world. A short-horizon trend rule on a liquid index ETF is exactly
what it looks like.

WHAT THIS IS EXPECTED TO DO, stated before the run so the result cannot be read
to fit. It will very likely be killed on ``insufficient-trades``, and that is a
**data** limitation rather than a defect in the rule:

    5 years x ~252 sessions = ~1,260 daily bars.
    The 150-trade gate therefore demands a position flip every ~8 bars.

A daily-bar trend rule that flips every eight bars is not following a trend, it
is trading noise — which the probes already demonstrated empirically, not just
arithmetically: 20 / 43 / 145 trades produced Sharpes of 0.415 / **-0.157** /
0.745 on the same rule and instrument. Monotonic in count, sign-changing in
Sharpe.

So the conclusion this seed is designed to make unavoidable is: **the fast layer
needs intraday data, not different parameters.** Choosing windows here to scrape
past 150 would produce a promotion built on the same noise the probes exposed,
which is precisely the failure ``docs/research/eval-protocol.md`` §6 exists to
prevent. The parameters below were chosen to be a defensible short-horizon trend
filter and for no other reason.

What a kill here *does* buy, and why the card is worth running:

1. The first strategy the firm has evaluated whose archetype matches its content.
2. The first live exercise of the archetype-conditional gates (PR #102) —
   until now the anchor-less path has only ever run in tests.
3. A fresh ``research.strategy.verdict``, which is the only way to observe the
   Strategy Librarian's INFO convergence path (PR #100) and the Evaluator
   trigger (PR #103). Both are unit-tested and neither has been seen live.

Carries no numpy/pandas-bearing import so the ``shrap-strategy-seed`` CLI stays
light, matching ``first_strategy.py`` and ``probe_strategies.py``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, NamedTuple

from shrap.research.strategy_evaluator.cross_sectional import (
    MOMENTUM_PARAM_BOUNDS,
    REVERSAL_PARAM_BOUNDS,
)
from shrap.research.strategy_evaluator.pipeline import (
    ARCHETYPE_TECHNICAL_CATALYST,
    RULE_CROSS_SECTIONAL_MOMENTUM,
    RULE_CROSS_SECTIONAL_REVERSAL,
)
from shrap.research.strategy_evaluator.reference_strategy import (
    DEFAULT_TARGET_WEIGHT,
    PARAM_BOUNDS,
)
from shrap.research.strategy_registry import STATUS_HYPOTHESIS, StrategyRecord
from shrap.research.universe_curator.launch_list import LAUNCH_LIST

CODE_REF = "src/shrap/research/strategy_seed/technical_strategies.py"
SOURCE = "mike-seed"

# Framework #3 carries no world-changer anchor by design (ADR-0013 §1). An empty
# anchor is the honest value: the archetype policy means it is never consulted,
# and putting an ID here to be safe would reintroduce exactly the lie this
# module exists to stop telling.
ANCHOR: dict[str, Any] = {}

# Neutral sizing. Regime is a sizing modifier, never an entry gate — a
# `regime_gate` key in the spec is refused by the Evaluator outright.
REGIME_SIZING_MODIFIER: dict[str, float] = {
    "late-cycle-melt-up": 1.0,
    "crisis-recovery": 1.0,
    "stagflation": 1.0,
    "wartime": 1.0,
}

_PARAM_BOUNDS: dict[str, list[float]] = {name: [lo, hi] for name, (lo, hi) in PARAM_BOUNDS.items()}


class TechnicalSeed(NamedTuple):
    """One single-name Framework #3 strategy definition."""

    key: str
    strategy_id: str
    name: str
    ticker: str
    fast: int
    slow: int
    thesis: str


class MomentumSeed(NamedTuple):
    """One cross-sectional Framework #3 strategy over the whole universe."""

    key: str
    strategy_id: str
    name: str
    tickers: tuple[str, ...]
    lookback: int
    skip: int
    top_n: int
    thesis: str
    market_filter: bool = False
    """Stand the book down while the average name in the universe is falling."""

    long_short: bool = False
    """Short the bottom of the ranking as well as buying the top."""

    parent_strategy_id: str | None = None
    revision_reason: str | None = None
    derived_from_evaluation_id: str | None = None


TECHNICAL_SEEDS: tuple[TechnicalSeed, ...] = (
    TechnicalSeed(
        key="spy-trend-5-20",
        # A real ULID, generated once and pinned. `strategy_id` is TEXT with no
        # format validation, so a readable placeholder would work — but a string
        # called a ULID that is not one is a trap for anything that later parses
        # or timestamp-sorts them. Asserted against the Crockford alphabet by test.
        strategy_id="01KYNCX02WTPS9ZJ52QX8GD4PJ",
        name="SPY short-horizon trend (5/20)",
        # SPY over XLE deliberately. ADR-0013 notes Framework #3 "will eventually
        # pressure" the launch universe because microstructure strategies want
        # liquid, high-turnover names; SPY is the most liquid name on the list and
        # one of the six with a written profile (docs/universe/spy.md).
        ticker="SPY",
        fast=5,
        slow=20,
        thesis=(
            "Short-horizon trend persistence in a broad, highly liquid index ETF: when "
            "the 5-day mean crosses above the 20-day mean, near-term drift has more "
            "often continued than reversed. No world-changer anchor and no claim about "
            "the physical world — the thesis is entirely about price behaviour, which "
            "is what makes it Framework #3. Expected to be killed on trade count: a "
            "daily-bar rule cannot produce 150 trades in five years without flipping "
            "every ~8 bars, which is noise-trading rather than trend-following."
        ),
    ),
)

# The universe this trades. Taken from the Curator's launch list rather than
# hand-listed, so the strategy and the tradeable universe cannot drift apart —
# a name dropped from Tier 3 would otherwise sit in this spec silently and be
# refused at evaluation with nothing pointing at why.
_MOMENTUM_TICKERS: tuple[str, ...] = tuple(sorted(e.ticker for e in LAUNCH_LIST))

MOMENTUM_SEEDS: tuple[MomentumSeed, ...] = (
    MomentumSeed(
        key="xs-momentum-126-21-10",
        strategy_id="01KYNH9VKXVQXJ48T4MF306PHE",
        name="Cross-sectional momentum (126/21, top 10)",
        tickers=_MOMENTUM_TICKERS,
        # Six-month formation, one-month skip, top decile of a 50-name universe.
        # These are the textbook construction rather than a search result: the
        # numbers were not tuned against this data, and tuning them would make
        # the out-of-sample claim meaningless.
        lookback=126,
        skip=21,
        top_n=10,
        thesis=(
            "Cross-sectional momentum: names that outperformed their peers over the "
            "preceding six months, excluding the most recent month, continue to "
            "outperform over the following weeks. The skip is not a tuning knob — "
            "short-horizon reversal runs opposite to momentum, so including the last "
            "month mixes two opposing signals. This is the first strategy the firm has "
            "seeded with a documented out-of-sample prior behind it rather than a rule "
            "chosen to have something to run: it is one of the most replicated effects "
            "in the equity literature, and also one of the most crowded, so prior "
            "evidence raises the odds of surviving evaluation without guaranteeing it. "
            "No world-changer anchor: the thesis is entirely about relative price "
            "behaviour."
        ),
    ),
    MomentumSeed(
        key="xs-momentum-126-21-10-standdown",
        strategy_id="01KYR151WA0K3SZ2ZHEK8TSHDN",
        name="Cross-sectional momentum (126/21, top 10) — stands down in a falling market",
        tickers=_MOMENTUM_TICKERS,
        # IDENTICAL to the parent. The universe, the formation window, the skip
        # and the decile are all unchanged, so the only difference between the
        # two evaluations is the market-state condition. Changing anything else
        # here would confound the comparison and make the revision unreadable.
        lookback=126,
        skip=21,
        top_n=10,
        market_filter=True,
        parent_strategy_id="01KYNH9VKXVQXJ48T4MF306PHE",
        # The real evaluation on the Dell, 2026-07-29. Informational — nothing
        # validates that this row exists, and on a fresh database it will not.
        # It is a reference to the evidence, not a foreign key.
        derived_from_evaluation_id="01KYQYKPHDRVYADBZH1VNCK55R",
        revision_reason=(
            "Kill criterion 3 fired. Fold 1 (2021-12 to 2022-11) returned -33.76% at "
            "sharpe -1.036 on 609 trades — the worst return AND the highest turnover "
            "of any fold, so the rule did not stand down in the drawdown, it churned. "
            "Diagnosis: the per-name filter (hold only positive momentum) is too weak "
            "because a CROSS-SECTIONAL ranking is relative — in 2022 energy and "
            "defense were genuinely positive, so it concentrated into them and was "
            "whipsawed by bear-market rallies. This revision attacks the risk side "
            "rather than the return side: hold nothing while the average name in the "
            "universe is falling over the same formation window. No new numeric "
            "parameter, so there is nothing here fitted to 2022."
        ),
        thesis=(
            "Cross-sectional momentum, with the addition that the book stands flat "
            "whenever the universe as a whole is falling over the formation window. "
            "The parent strategy was measured at sharpe 0.782 against a benchmark's "
            "0.772 — nearly all of its excess return was extra risk, not skill, and "
            "it lost a third of the book in the sample's only bear market. The "
            "falsifiable claim here is narrow and specific: momentum's crash risk is "
            "concentrated in periods when the whole cross-section is declining, so "
            "declining to trade those periods should raise the information ratio "
            "rather than merely lowering both return and volatility in proportion. "
            "If the information ratio does not improve, the crash was not avoidable "
            "this way and the added condition is dead weight — which is a real "
            "result, and the reason the two strategies are held side by side."
        ),
    ),
    MomentumSeed(
        key="xs-momentum-126-21-10-longshort",
        strategy_id="01KYR3P64C9Y144P3XVJZAR4GK",
        name="Cross-sectional momentum (126/21, top 10) — long winners, short losers",
        tickers=_MOMENTUM_TICKERS,
        # Identical to the parent. The short leg is the only difference.
        lookback=126,
        skip=21,
        top_n=10,
        long_short=True,
        parent_strategy_id="01KYNH9VKXVQXJ48T4MF306PHE",
        derived_from_evaluation_id="01KYR38ADPNB2QD7DJX6NNZS9W",
        revision_reason=(
            "The rule ran HALF the effect. Jegadeesh-Titman is long the winners and "
            "short the losers; this book was long-only, so it sat structurally ~100% "
            "long equity and competed against a 100%-long benchmark on stock "
            "selection alone. The per-fold information ratios show exactly that "
            "shape: +0.97 correlation with fold RETURN once the crash is excluded, "
            "beating the benchmark only in the three folds the market ran hard "
            "(+1.090, +0.692, +1.073), dead flat in the crash (-0.004), and losing "
            "in the two quiet years (-0.457, -0.241). A trend amplifier, not a "
            "factor. Restoring the short leg is a return to the documented "
            "construction rather than a new idea — the deviation was ours."
        ),
        thesis=(
            "Cross-sectional momentum in its textbook two-sided form: long the top "
            "decile by trailing six-month return excluding the most recent month, "
            "short the bottom decile, dollar-neutral. The falsifiable claim is that "
            "the long-only book's dependence on market direction — fold IR "
            "correlating +0.97 with fold return — is an artefact of running one leg, "
            "and that a two-sided book earns its information ratio from the SPREAD "
            "between winners and losers rather than from being invested. It should "
            "therefore beat the benchmark in more than three of six folds, "
            "particularly the quiet years where the long-only version lost. "
            "It also removes any need to detect a regime switch: when leadership "
            "rolls over, names migrate from the long leg to the short leg on their "
            "own. If the information ratio does not improve, the short leg costs "
            "more in borrow and turnover than the loser-continuation effect pays, "
            "which is a real and useful result. NOT TRADEABLE YET — the Strategy "
            "Runner treats a negative weight as flat and never opens a short, so "
            "this is a research question until that path exists."
        ),
    ),
)

# Falsifiers specific to a cross-sectional momentum book. The generic protocol
# gates are appended by `_momentum_kill_criteria`.
_MOMENTUM_KILL_CRITERIA: tuple[str, ...] = (
    "the momentum effect does not survive realistic costs at this turnover — a "
    "monthly top-decile rotation over 50 names trades a great deal, and the effect "
    "is small enough per name that friction is the likeliest way it dies",
    "the strategy does not beat equal-weight buy-and-hold of the same universe — "
    "an information ratio at or below zero means the rotation destroyed value "
    "against simply owning the names",
    "momentum crashes: the effect is known to invert sharply after drawdowns, so a "
    "single fold with a large negative return is evidence about the strategy rather "
    "than noise to be averaged away",
)


def momentum_kill_criteria() -> list[str]:
    """Rule-specific falsifiers plus the protocol's own gates."""

    return [*_MOMENTUM_KILL_CRITERIA, *_KILL_CRITERIA[1:]]


# The first of these is the whole card. This strategy exists to cover the two
# folds momentum lost, and an aggregate that looks respectable while losing
# those same two folds has falsified the hypothesis regardless of its headline
# number. Written before the run so the result cannot be read to fit.
_REVERSAL_KILL_CRITERIA: tuple[str, ...] = (
    "it does not beat the benchmark in the folds momentum lost. Momentum's "
    "measured fold information ratios (2026-07-29) were 2023 -0.457 and 2026 "
    "-0.241, both in modestly-positive years; those are the two this rule is "
    "for. Winning elsewhere and losing there means it is a second copy of the "
    "same bet, not a complement, whatever its aggregate says",
    "its fold information ratios correlate POSITIVELY with momentum's. A "
    "complementary strategy earns when the other does not; a positive "
    "correlation means the firm has doubled one exposure while believing it "
    "diversified",
    "short-horizon reversal does not survive costs at this turnover — a 5-day "
    "formation rotated across 50 names trades far harder than the 126/21 rule, "
    "and reversal profits are small per name. This is the likeliest way it "
    "dies, and the reason the honest test is net of the cost model rather than "
    "gross",
    "the effect is a liquidity premium the firm cannot actually harvest: "
    "reversal profits concentrate in the names hardest to trade, and the "
    "evaluator's ADV filter may be admitting fills that would not exist",
)


def reversal_kill_criteria() -> list[str]:
    """Rule-specific falsifiers plus the protocol's own gates."""

    return [*_REVERSAL_KILL_CRITERIA, *_KILL_CRITERIA[1:]]


def _momentum_spec(seed: MomentumSeed) -> dict[str, Any]:
    return {
        "rule": RULE_CROSS_SECTIONAL_MOMENTUM,
        "params": {
            "lookback": seed.lookback,
            "skip": seed.skip,
            "top_n": seed.top_n,
            "gross_exposure": 1.0,
            # Boolean, so `_validate_param_bounds` requires no [lo, hi] — and
            # there is nothing to bound, which is the design property that keeps
            # this a revision rather than a parameter sweep.
            "market_filter": seed.market_filter,
            "long_short": seed.long_short,
        },
        "param_bounds": {k: list(v) for k, v in MOMENTUM_PARAM_BOUNDS.items()},
    }


def compute_momentum_spec_hash(seed: MomentumSeed) -> str:
    """Dedup key, same shape as every other seed family."""

    material = json.dumps(
        {
            "name": seed.name,
            "archetype": ARCHETYPE_TECHNICAL_CATALYST,
            "anchor": ANCHOR,
            "tickers": {"long": list(seed.tickers), "short": []},
            "spec": _momentum_spec(seed),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def momentum_record(seed: MomentumSeed) -> StrategyRecord:
    """Build one cross-sectional momentum seed at ``hypothesis``."""

    return StrategyRecord(
        strategy_id=seed.strategy_id,
        name=seed.name,
        version=1,
        archetype=ARCHETYPE_TECHNICAL_CATALYST,
        status=STATUS_HYPOTHESIS,
        source=SOURCE,
        thesis=seed.thesis,
        anchor=dict(ANCHOR),
        tickers={"long": list(seed.tickers), "short": []},
        spec=_momentum_spec(seed),
        spec_hash=compute_momentum_spec_hash(seed),
        regime_sizing_modifier=dict(REGIME_SIZING_MODIFIER),
        kill_criteria=momentum_kill_criteria(),
        code_ref=CODE_REF,
        created_at=None,
        updated_at=None,
        parent_strategy_id=seed.parent_strategy_id,
        revision_reason=seed.revision_reason,
        derived_from_evaluation_id=seed.derived_from_evaluation_id,
    )


MOMENTUM_SEEDS_BY_KEY: dict[str, MomentumSeed] = {s.key: s for s in MOMENTUM_SEEDS}


class ReversalSeed(NamedTuple):
    """One short-horizon cross-sectional reversal strategy over the universe."""

    key: str
    strategy_id: str
    name: str
    tickers: tuple[str, ...]
    lookback: int
    skip: int
    top_n: int
    thesis: str
    long_short: bool = False
    parent_strategy_id: str | None = None
    revision_reason: str | None = None
    derived_from_evaluation_id: str | None = None


# Two roots, not a parent and a revision. They express the same documented
# effect at two different fidelities, and neither is derived from the other's
# result — calling one a revision of the other would put a construction choice
# into the lineage as though it were a response to evidence.
REVERSAL_SEEDS: tuple[ReversalSeed, ...] = (
    ReversalSeed(
        key="xs-reversal-5-1-10-longshort",
        strategy_id="01KYRECMH8WZ2WZYB4ZE217E37",
        name="Cross-sectional reversal (5/1, top 10) — long/short",
        tickers=_MOMENTUM_TICKERS,
        # Same universe as the momentum seeds, deliberately. The comparison
        # between the two rules is the point of the card, and a different
        # universe would confound it with a selection difference.
        lookback=5,
        skip=1,
        top_n=10,
        long_short=True,
        thesis=(
            "Short-horizon cross-sectional reversal: names that underperformed their "
            "peers over the last week tend to bounce back over the following days. "
            "The documented prior is Lehmann (1990) and Lo & MacKinlay (1990) — "
            "short-term contrarian profits in the cross-section — and this is the "
            "textbook long/short construction, long the losers and short the winners, "
            "dollar-neutral. The one-day skip is the standard defence against buying "
            "a bid-ask bounce rather than a real dislocation, not a tuned value. "
            "This strategy exists to cover a MEASURED gap: the firm's momentum rule "
            "lost the 2023 and 2026 folds (IR -0.457 and -0.241), both quiet, "
            "modestly-positive years where it churned to lag a basket that sat still. "
            "It was level with the benchmark in the 2022 crash (-0.004), so the gap "
            "is not downside protection. Reversal is the documented counterpart that "
            "earns in exactly those conditions. "
            "NOT TRADEABLE YET: the Strategy Runner treats a negative weight as flat "
            "and never opens a short, so a promoted long/short strategy would trade "
            "only its long leg at half the intended book. Do not assign this one an "
            "account until the Runner is short-capable. "
            "No world-changer anchor: the thesis is entirely about relative price "
            "behaviour."
        ),
    ),
    ReversalSeed(
        key="xs-reversal-5-1-10-longonly",
        strategy_id="01KYRECMH8WZ2WZYB4ZE217E38",
        name="Cross-sectional reversal (5/1, top 10) — long only",
        tickers=_MOMENTUM_TICKERS,
        lookback=5,
        skip=1,
        top_n=10,
        long_short=False,
        thesis=(
            "The same short-horizon reversal rule with the short leg removed, so that "
            "it can actually be traded on the paper path. "
            "THIS IS A DELIBERATE DEVIATION FROM THE DOCUMENTED CONSTRUCTION and is "
            "recorded as one. Lehmann (1990) and Lo & MacKinlay (1990) measure a "
            "long/short contrarian portfolio; dropping a leg is exactly the error the "
            "momentum rule made, where a one-sided book turned a factor bet into a "
            "trend amplifier whose fold information ratio correlated +0.97 with fold "
            "return. The same distortion should be expected here, in the opposite "
            "direction, and the honest reading of any result is against the long/short "
            "sibling rather than on its own. "
            "It is seeded anyway because the Strategy Runner cannot open a short, so "
            "this is the only version the firm can put on an account today. Whether "
            "the short leg pays is answerable by comparing the two evaluations, and "
            "that answer decides whether making the Runner short-capable is worth "
            "building. "
            "No world-changer anchor: the thesis is entirely about relative price "
            "behaviour."
        ),
    ),
)

REVERSAL_SEEDS_BY_KEY: dict[str, ReversalSeed] = {s.key: s for s in REVERSAL_SEEDS}


def _reversal_spec(seed: ReversalSeed) -> dict[str, Any]:
    return {
        "rule": RULE_CROSS_SECTIONAL_REVERSAL,
        "params": {
            "lookback": seed.lookback,
            "skip": seed.skip,
            "top_n": seed.top_n,
            "gross_exposure": 1.0,
            "long_short": seed.long_short,
        },
        "param_bounds": {k: list(v) for k, v in REVERSAL_PARAM_BOUNDS.items()},
    }


def compute_reversal_spec_hash(seed: ReversalSeed) -> str:
    """Dedup key, same shape as every other seed family."""

    material = json.dumps(
        {
            "name": seed.name,
            "archetype": ARCHETYPE_TECHNICAL_CATALYST,
            "anchor": ANCHOR,
            "tickers": {"long": list(seed.tickers), "short": []},
            "spec": _reversal_spec(seed),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def reversal_record(seed: ReversalSeed) -> StrategyRecord:
    """Build one cross-sectional reversal seed at ``hypothesis``."""

    return StrategyRecord(
        strategy_id=seed.strategy_id,
        name=seed.name,
        version=1,
        archetype=ARCHETYPE_TECHNICAL_CATALYST,
        status=STATUS_HYPOTHESIS,
        source=SOURCE,
        thesis=seed.thesis,
        anchor=dict(ANCHOR),
        tickers={"long": list(seed.tickers), "short": []},
        spec=_reversal_spec(seed),
        spec_hash=compute_reversal_spec_hash(seed),
        regime_sizing_modifier=dict(REGIME_SIZING_MODIFIER),
        kill_criteria=reversal_kill_criteria(),
        code_ref=CODE_REF,
        created_at=None,
        updated_at=None,
        parent_strategy_id=seed.parent_strategy_id,
        revision_reason=seed.revision_reason,
        derived_from_evaluation_id=seed.derived_from_evaluation_id,
    )


# Falsifiers. Note what is absent: no world-changer criterion, because there is
# no world-changer. The first is the one specific to this rule; the rest are the
# protocol's own gates, restated so the record is self-describing.
_KILL_CRITERIA: tuple[str, ...] = (
    "short-horizon trend persistence in SPY does not survive realistic costs — "
    "the effect is small per trade and dies to friction before it dies to being wrong",
    "fewer than 150 trades over the walk-forward window — too few to evaluate",
    "out-of-sample Sharpe at or below zero — no edge to measure",
    "edge does not survive the realistic-friction stress test",
    "out-of-sample Sharpe below the promote floor",
)


def _params(fast: int, slow: int) -> dict[str, Any]:
    """Exactly the keys ReferenceTrendStrategy.from_spec consumes."""

    return {
        "fast": fast,
        "slow": slow,
        "target_weight": DEFAULT_TARGET_WEIGHT,
        "long_only": True,
    }


def _spec(fast: int, slow: int) -> dict[str, Any]:
    return {
        "params": _params(fast, slow),
        "param_bounds": {k: list(v) for k, v in _PARAM_BOUNDS.items()},
    }


def _tickers(ticker: str) -> dict[str, list[str]]:
    return {"long": [ticker], "short": []}


def compute_spec_hash(seed: TechnicalSeed) -> str:
    """Deterministic dedup key over the seed's identifying material.

    Same shape as the other seed modules so every strategy in the registry
    hashes the same way. The differing ``archetype`` and empty ``anchor`` are
    what make these hashes distinct from the structural seeds' even if a
    parameter pair were ever reused.
    """

    material = json.dumps(
        {
            "name": seed.name,
            "archetype": ARCHETYPE_TECHNICAL_CATALYST,
            "anchor": ANCHOR,
            "tickers": _tickers(seed.ticker),
            "spec": _spec(seed.fast, seed.slow),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def technical_record(seed: TechnicalSeed) -> StrategyRecord:
    """Build one Framework #3 seed as a :class:`StrategyRecord` at ``hypothesis``."""

    return StrategyRecord(
        strategy_id=seed.strategy_id,
        name=seed.name,
        version=1,
        archetype=ARCHETYPE_TECHNICAL_CATALYST,
        status=STATUS_HYPOTHESIS,
        source=SOURCE,
        thesis=seed.thesis,
        anchor=dict(ANCHOR),
        tickers=_tickers(seed.ticker),
        spec=_spec(seed.fast, seed.slow),
        spec_hash=compute_spec_hash(seed),
        regime_sizing_modifier=dict(REGIME_SIZING_MODIFIER),
        kill_criteria=list(_KILL_CRITERIA),
        code_ref=CODE_REF,
        created_at=None,
        updated_at=None,
    )


TECHNICAL_SEEDS_BY_KEY: dict[str, TechnicalSeed] = {s.key: s for s in TECHNICAL_SEEDS}

__all__ = [
    "ANCHOR",
    "CODE_REF",
    "MOMENTUM_SEEDS",
    "MOMENTUM_SEEDS_BY_KEY",
    "REVERSAL_SEEDS",
    "REVERSAL_SEEDS_BY_KEY",
    "TECHNICAL_SEEDS",
    "TECHNICAL_SEEDS_BY_KEY",
    "MomentumSeed",
    "ReversalSeed",
    "TechnicalSeed",
    "compute_momentum_spec_hash",
    "compute_spec_hash",
    "momentum_kill_criteria",
    "momentum_record",
    "technical_record",
]
