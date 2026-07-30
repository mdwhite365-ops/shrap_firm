"""Seeds for the documented factor effects.

Separate from ``technical_strategies.py`` deliberately: these are a *family* of
independent hypotheses, and keeping them in their own module makes it hard to
accidentally write one as a revision of another. Each is a lineage root, so each
is attempt 1 and none inherits another's multiple-testing penalty (PR #148).

That property is the point. Four unrelated effects tested once each is four
honest experiments; four variants of one effect is a search over one hypothesis,
and the gate treats those differently on purpose.

``network-peripherality`` is the first seed here the firm chose for itself: it
reached the registry as a `missing-scorer` capability gap, cited to an arXiv
paper the q-fin leg ingested, rather than from Mike picking a result out of the
canon (2026-07-30).

Every seed states, before the run, what would falsify it. The kill criteria are
not boilerplate — each names the specific way that specific effect is known to
die, so a result cannot be read to fit after the fact.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, NamedTuple

from shrap.research.strategy_evaluator.factors import (
    DEFAULT_FACTOR_LOOKBACKS,
    FACTOR_HIGH_PROXIMITY,
    FACTOR_LOW_VOLATILITY,
    FACTOR_NETWORK_PERIPHERALITY,
    FACTOR_PARAM_BOUNDS,
    FACTOR_TIME_SERIES,
    FACTOR_VOLUME_SHOCK,
)
from shrap.research.strategy_evaluator.pipeline import (
    ARCHETYPE_TECHNICAL_CATALYST,
    RULE_CROSS_SECTIONAL_FACTOR,
)
from shrap.research.strategy_registry import STATUS_HYPOTHESIS, StrategyRecord
from shrap.research.strategy_seed.technical_strategies import (
    _MOMENTUM_TICKERS,
    ANCHOR,
    REGIME_SIZING_MODIFIER,
    SOURCE,
)

CODE_REF = "src/shrap/research/strategy_seed/factor_strategies.py"

# Protocol-level falsifiers shared by every factor seed. Each seed adds its own
# effect-specific ones on top.
#
# Public (renamed from `_COMMON_KILL_CRITERIA`, 2026-07-30) because the
# Hypothesis Generator staples the same three onto every proposal it writes.
# These are the ways ANY strategy fails the firm's evaluation protocol rather
# than ways a particular effect fails, so they are not a proposer's to omit —
# and a second copy of the text would drift from this one the first time the
# protocol changed.
COMMON_KILL_CRITERIA: tuple[str, ...] = (
    "it does not beat equal-weight buy-and-hold of the same universe — an "
    "information ratio at or below zero means the selection destroyed value "
    "against simply owning the names",
    "the edge does not survive the friction stress (+50% costs, +1 day lag)",
    "it beats the benchmark in fewer than half the walk-forward folds, which is "
    "an edge indistinguishable from zero across periods however good the "
    "aggregate looks",
)


class FactorSeed(NamedTuple):
    """One documented factor strategy over the launch universe."""

    key: str
    strategy_id: str
    name: str
    factor: str
    thesis: str
    kill_criteria: tuple[str, ...]
    lookback: int | None = None
    top_n: int = 10
    long_short: bool = False

    @property
    def effective_lookback(self) -> int:
        if self.lookback is not None:
            return self.lookback
        return DEFAULT_FACTOR_LOOKBACKS[self.factor]


FACTOR_SEEDS: tuple[FactorSeed, ...] = (
    FactorSeed(
        key="low-volatility-252-10",
        strategy_id="01KYRG32PAW0V0D8RVRBHAJ9H8",
        name="Low-volatility anomaly (252d, bottom 10 by vol)",
        factor=FACTOR_LOW_VOLATILITY,
        thesis=(
            "Low-volatility stocks earn higher risk-adjusted returns than high-volatility "
            "ones. Documented by Ang, Hodrick, Xing & Zhang (2006) and Baker, Bradley & "
            "Wurgler (2011); it runs directly against CAPM, which predicts the opposite, "
            "and has survived four decades of attempts to explain it away. The usual "
            "explanation is a leverage constraint: investors who cannot borrow bid up "
            "high-beta names instead, leaving the quiet ones cheap. "
            "This is the effect most likely of the four to clear the SHARPE floor "
            "specifically, because it targets the denominator rather than the numerator — "
            "a lower-volatility book can clear a risk-adjusted bar without a higher "
            "return. If nothing in the firm has ever cleared that floor, this is the seed "
            "that tests whether the floor is reachable at all. "
            "No world-changer anchor: the thesis is entirely about price behaviour."
        ),
        kill_criteria=(
            "low realised volatility in the formation window does not persist into the "
            "holding window — the whole effect rests on volatility being autocorrelated, "
            "and if it is not, the rule is sorting on noise",
            "the selected names are a sector bet rather than a volatility bet. Utilities "
            "and staples dominate low-vol screens, so a result here may be one sector "
            "wearing a factor's name — check what it actually held before believing it",
            *COMMON_KILL_CRITERIA,
        ),
    ),
    FactorSeed(
        key="high-proximity-252-10",
        strategy_id="01KYRG32PAW0V0D8RVRBHAJ9H9",
        name="52-week-high proximity (252d, top 10)",
        factor=FACTOR_HIGH_PROXIMITY,
        thesis=(
            "Nearness to the 52-week high predicts continued outperformance. George & "
            "Hwang (2004) show it predicts BETTER than past return itself, which is what "
            "makes it a distinct effect rather than momentum in a hat: the anchoring story "
            "is that traders treat the 52-week high as a psychological ceiling and "
            "under-react when a name breaks through it. "
            "The firm has already measured cross-sectional momentum (IR +0.392), so this "
            "is a direct test of George & Hwang's central claim on the firm's own data — "
            "if proximity beats momentum here, that is a replication; if it does not, that "
            "is evidence about this universe rather than about the paper. "
            "Uses the highest CLOSE rather than the highest intraday high, because the "
            "panel carries no intraday highs. Standard construction, recorded as a choice. "
            "No world-changer anchor: the thesis is entirely about price behaviour."
        ),
        kill_criteria=(
            "it is cross-sectional momentum in disguise — if its fold information ratios "
            "correlate above ~0.9 with the momentum strategy's, the firm has not found a "
            "second effect, it has found the same one measured differently",
            "the effect is concentrated in small, illiquid names and does not survive the "
            "launch universe's liquidity, which is 50 large-cap names",
            *COMMON_KILL_CRITERIA,
        ),
    ),
    FactorSeed(
        key="volume-shock-50-10",
        strategy_id="01KYRG32PAW0V0D8RVRBHAJ9HA",
        name="High-volume return premium (50d baseline, top 10)",
        factor=FACTOR_VOLUME_SHOCK,
        thesis=(
            "Names whose trading volume spikes above their own norm tend to appreciate "
            "over the following weeks. Gervais, Kaniel & Mingelgrin (2001) call this the "
            "high-volume return premium and attribute it to visibility: a volume shock "
            "draws attention, attention draws buyers, and the price impact persists longer "
            "than the shock does. "
            "This is the only one of the four seeds that uses a signal other than price. "
            "That matters for the firm beyond this strategy — every effect it has tested "
            "so far is a function of closes alone, so a correlated failure across all of "
            "them would be indistinguishable from a defect in how it reads prices. "
            "Volume is measured against the name's OWN trailing average, never across "
            "names: a cross-name volume comparison ranks megacaps first every day and "
            "measures size instead of shock. "
            "No world-changer anchor: the thesis is entirely about market microstructure."
        ),
        kill_criteria=(
            "the premium is a liquidity artefact of the IEX feed rather than a real "
            "effect. Alpaca's free feed reports a biased subset of consolidated volume, so "
            "a volume 'shock' here may be an IEX routing change with no economic content — "
            "this is the seed most exposed to the data source, and the first thing to "
            "suspect if it looks good",
            "the effect reverses within the holding period, which is the documented "
            "failure mode for attention-driven premia: the visibility fades and the price "
            "gives it back",
            *COMMON_KILL_CRITERIA,
        ),
    ),
    FactorSeed(
        key="time-series-252",
        strategy_id="01KYRG32PAW0V0D8RVRBHAJ9HB",
        name="Time-series momentum (252d own-return, absolute)",
        factor=FACTOR_TIME_SERIES,
        thesis=(
            "Each name's OWN past twelve-month return predicts its own future return. "
            "Moskowitz, Ooi & Pedersen (2012) document this across dozens of asset classes "
            "and it is the basis of most managed-futures trend following. "
            "It is ABSOLUTE, not relative, and that is the entire distinction from the "
            "cross-sectional momentum already on record: this rule holds every name with a "
            "positive own-return and NOTHING otherwise, where the cross-sectional rule "
            "always finds ten names to hold no matter what the market is doing. In a broad "
            "drawdown this goes to cash on its own, with no regime classifier and no "
            "market filter bolted on. "
            "That is the direct test of something the firm got wrong once already: the "
            "standdown revision added a market filter to cross-sectional momentum and came "
            "back worse than its parent. This tests whether the answer was a different "
            "SIGNAL rather than a filter on the old one. "
            "No world-changer anchor: the thesis is entirely about price behaviour."
        ),
        kill_criteria=(
            "it holds nearly everything in a bull market and nearly nothing otherwise, "
            "making it a market-timing overlay on buy-and-hold rather than a selection "
            "effect. If its holdings count correlates near-perfectly with the benchmark's "
            "return, it has no selection content",
            "going to cash costs more in missed recoveries than it saves in avoided "
            "drawdowns — trend following is known to lag sharp V-shaped reversals, and "
            "2020 and 2022-23 both contain one",
            "twelve-month formation on daily bars produces too few position changes to "
            "clear the trade-count gate honestly, in which case the result is about "
            "sample size rather than about the effect",
            *COMMON_KILL_CRITERIA,
        ),
    ),
    FactorSeed(
        key="network-peripherality-252-10",
        strategy_id="01KYT494HAY1ZKBWPXB312F56F",
        name="Correlation-network peripherality (252d, top 10 least connected)",
        factor=FACTOR_NETWORK_PERIPHERALITY,
        thesis=(
            "Assets on the PERIPHERY of a denoised correlation network earn higher "
            "risk-adjusted returns than those at its core. The mechanism is a "
            "diversification story: a name weakly connected to everything else is one few "
            "portfolios need to hold, and being unwanted is compensated. "
            "This is the first strategy the firm proposed to itself. It came from "
            "arXiv 2607.10297, ingested by Tech Watcher's q-fin leg on 2026-07-30, "
            "accepted by the literature filter, and recorded by the Hypothesis Generator "
            "as a `missing-scorer` capability gap — the effect was real, cited, and "
            "computable from closes, and nobody had written the function. Every prior "
            "seed in this module was chosen by Mike from the canon. "
            "It is also the first effect the firm has tested that is RELATIONAL. Every "
            "other strategy on record scores a name from its own history; this one cannot "
            "be computed for a single stock at all. That matters beyond this result: a "
            "correlated failure across a corpus of self-referential signals would be "
            "indistinguishable from a defect in how the firm reads one name's history, "
            "and this is the first signal that would not share it. "
            "No world-changer anchor: the thesis is entirely about market structure."
        ),
        kill_criteria=(
            "peripherality is low beta wearing a network's name. The market mode is "
            "removed by regression before correlations are taken precisely to prevent "
            "this, but if its fold information ratios correlate above ~0.9 with the "
            "low-volatility strategy's, the removal did not work and the firm has one "
            "effect under two names",
            "the periphery is a small-cap or illiquid pocket rather than a structural "
            "position — check what it actually held before believing it, the same way "
            "the low-volatility seed must be checked for being a utilities bet",
            "peripherality does not persist from the formation window into the holding "
            "window. The whole effect rests on correlation structure being stable enough "
            "to rank on, and correlations are famously least stable exactly when they "
            "matter most",
            "the residual correlations are estimation noise. 252 observations across 50 "
            "names is roughly 5x more data than parameters, which is ample for mean "
            "correlations and thin in absolute terms — if the ranking is unstable "
            "week-to-week, it is sampling error being traded",
            *COMMON_KILL_CRITERIA,
        ),
    ),
)

FACTOR_SEEDS_BY_KEY: dict[str, FactorSeed] = {s.key: s for s in FACTOR_SEEDS}


def _factor_spec(seed: FactorSeed) -> dict[str, Any]:
    return {
        "rule": RULE_CROSS_SECTIONAL_FACTOR,
        "params": {
            "factor": seed.factor,
            "lookback": seed.effective_lookback,
            "top_n": seed.top_n,
            "gross_exposure": 1.0,
            "long_short": seed.long_short,
        },
        "param_bounds": {k: list(v) for k, v in FACTOR_PARAM_BOUNDS.items()},
    }


def compute_factor_spec_hash(seed: FactorSeed) -> str:
    """Dedup key, same shape as every other seed family."""

    material = json.dumps(
        {
            "name": seed.name,
            "archetype": ARCHETYPE_TECHNICAL_CATALYST,
            "anchor": ANCHOR,
            "tickers": {"long": list(_MOMENTUM_TICKERS), "short": []},
            "spec": _factor_spec(seed),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def factor_record(seed: FactorSeed) -> StrategyRecord:
    """Build one factor seed at ``hypothesis``, as a lineage root."""

    return StrategyRecord(
        strategy_id=seed.strategy_id,
        name=seed.name,
        version=1,
        archetype=ARCHETYPE_TECHNICAL_CATALYST,
        status=STATUS_HYPOTHESIS,
        source=SOURCE,
        thesis=seed.thesis,
        anchor=dict(ANCHOR),
        tickers={"long": list(_MOMENTUM_TICKERS), "short": []},
        spec=_factor_spec(seed),
        spec_hash=compute_factor_spec_hash(seed),
        regime_sizing_modifier=dict(REGIME_SIZING_MODIFIER),
        kill_criteria=list(seed.kill_criteria),
        code_ref=CODE_REF,
        created_at=None,
        updated_at=None,
        # Roots, every one. Four unrelated effects tested once each is four
        # honest experiments; recording any as a revision of another would
        # inflate that lineage's attempt count and raise its promote bar for
        # no reason (PR #148).
        parent_strategy_id=None,
        revision_reason=None,
        derived_from_evaluation_id=None,
    )


__all__ = [
    "CODE_REF",
    "COMMON_KILL_CRITERIA",
    "FACTOR_SEEDS",
    "FACTOR_SEEDS_BY_KEY",
    "FactorSeed",
    "compute_factor_spec_hash",
    "factor_record",
]
