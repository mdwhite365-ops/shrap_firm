"""The benchmark a strategy has to beat: equal-weight buy-and-hold, no timing.

Measured on 2026-07-28, naive equal-weight buy-and-hold through this very engine
scored an annualised Sharpe of 1.026 (1 name) to 1.158 (50 names) on synthetic
data with a ~7.5%/yr drift and **no timing rule whatsoever** — clearing the 1.0
promote floor purely by being invested. At zero drift the same portfolios scored
0.33-0.45, which identifies the term doing the work: market drift, not skill and
not diversification.

An absolute Sharpe floor therefore cannot answer the only question that matters —
**does this strategy beat the alternative of just owning the thing?** In one run
a cross-sectional timing rule scored 2.28 against buy-and-hold's 3.22: it
destroyed value against the basket and would still have promoted.

So every evaluation now runs a second backtest over the identical panel, periods
and cost model, using this rule. The difference between the two return series is
the strategy's *active* return, and its risk-adjusted form is the information
ratio. See ``docs/research/eval-protocol.md`` §6b.

Two construction choices worth stating, because both could reasonably go the
other way:

**Fully invested, always.** The benchmark holds ``1/N`` of every ticker in the
panel from the first period to the last. It does not inherit the strategy's
``gross_exposure``. A strategy that chooses to sit in cash is making a decision,
and the benchmark exists to price that decision — matching its exposure would
hide exactly the choice being evaluated.

**It pays costs.** The benchmark runs through the same ``run_backtest`` and the
same cost model, so it books its entry costs like anything else. Comparing a
costed strategy to a frictionless benchmark would understate active return by
the one quantity the friction stress already exists to interrogate.

The benchmark generalises correctly at N=1: for a single-name timing rule it is
buy-and-hold that name, and the question becomes "did the timing beat simply
owning it?" — which is the right question there too.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from shrap.research.strategy_evaluator.strategy import PanelWindow

BENCHMARK_NAME = "equal-weight-buy-and-hold"


@dataclass(frozen=True, slots=True)
class EqualWeightBuyAndHold:
    """Hold ``1/N`` of every ticker that has listed. No decisions.

    **N is the number of names trading today, not the number in the panel.**
    The panel is ragged — it spans every date any member traded — so before a
    name lists there is no price to buy it at. Weighting over the full roster
    would hold a fraction of nothing and quietly run the benchmark at less than
    fully invested, understating it early in the window and inflating every
    information ratio measured against it.

    So the benchmark's universe grows as names list, and it rebalances when one
    does. That is a real behaviour and worth being explicit about: it is still
    "own everything, decide nothing", which is the alternative a strategy has to
    beat. It is not a timing rule — the entry dates are the listing dates, and
    no other information is used.
    """

    @property
    def name(self) -> str:
        return BENCHMARK_NAME

    @property
    def warmup(self) -> int:
        # Deliberately 1, not the strategy's warmup. The engine gives both runs
        # the SAME first and last period, so the benchmark must not impose its
        # own headroom; a larger warmup here would silently shorten the window
        # both are measured over.
        return 1

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        live = window.live_tickers
        if not live:
            return dict.fromkeys(window.tickers, 0.0)
        per_name = 1.0 / len(live)
        chosen = set(live)
        # Every panel ticker is named, including the not-yet-listed ones at 0.0:
        # the engine diffs weights per ticker to recover trades, so an omitted
        # name reads as "unchanged" rather than "not held".
        return {t: (per_name if t in chosen else 0.0) for t in window.tickers}


def active_returns(
    strategy_returns: Sequence[float], benchmark_returns: Sequence[float]
) -> list[float]:
    """Per-period excess of the strategy over the benchmark.

    Lengths must match — both runs cover the identical period range by
    construction, so a mismatch means the engine wired them differently and the
    comparison would be meaningless rather than merely imprecise.
    """

    if len(strategy_returns) != len(benchmark_returns):
        raise ValueError(
            f"strategy and benchmark must cover the same periods "
            f"({len(strategy_returns)} vs {len(benchmark_returns)})"
        )
    return [s - b for s, b in zip(strategy_returns, benchmark_returns, strict=True)]


__all__ = ["BENCHMARK_NAME", "EqualWeightBuyAndHold", "active_returns"]
