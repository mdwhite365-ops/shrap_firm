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

from shrap.research.strategy_evaluator.pipeline import ARCHETYPE_TECHNICAL_CATALYST
from shrap.research.strategy_evaluator.reference_strategy import (
    DEFAULT_TARGET_WEIGHT,
    PARAM_BOUNDS,
)
from shrap.research.strategy_registry import STATUS_HYPOTHESIS, StrategyRecord

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
    """One Framework #3 strategy definition."""

    key: str
    strategy_id: str
    name: str
    ticker: str
    fast: int
    slow: int
    thesis: str


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
    "TECHNICAL_SEEDS",
    "TECHNICAL_SEEDS_BY_KEY",
    "TechnicalSeed",
    "compute_spec_hash",
    "technical_record",
]
