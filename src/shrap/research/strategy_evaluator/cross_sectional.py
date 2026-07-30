"""Cross-sectional strategies — rules that trade the universe, not one name.

Every strategy the firm has evaluated has traded exactly one ticker, because
``_default_strategy_factory`` built the reference rule from ``tickers[0]`` and
discarded the rest. The *engine* was never single-name: ``PricePanel`` is
documented as "one or more tickers" and ``walk_forward`` counts a trade per
ticker per weight change. Only the rules were.

That one line distorted a conclusion. The probes' verdicts were read as showing
that a daily-bar rule cannot clear the 150-trade gate — true for a single
instrument (a five-year window is ~1,260 bars, so 150 trades demands a flip
every ~8 bars) and **false across a universe**, where the same rule applied to
50 names generates roughly 50x the trades.

The distinction that matters: the trade-count gate measures **sample size**, not
speed. Breadth supplies sample size. A cross-sectional daily rule is still not a
*fast* strategy — each position holds for weeks — but it is a properly powered
one, which is what the gate is actually asking for.

**The honest caveat, recorded here rather than discovered later.** Trades across
50 US equities are not 50 independent observations. Daily equity returns are
heavily cross-correlated; in a drawdown every name flips together. The effective
sample is materially smaller than the nominal trade count, so a breadth-based
strategy can satisfy the gate while being tested less rigorously than the number
implies. The gate should eventually count independent *episodes* rather than raw
weight changes. Until it does, read a cross-sectional trade count as an upper
bound on statistical power, never as the power itself.

Three rules live here:

- :class:`CrossSectionalTrendStrategy` — the reference crossover applied to every
  ticker, equal-weighted across whichever names are invested. A direct
  generalisation, useful mainly because it isolates breadth as the only change
  from the runs already on record.
- :class:`CrossSectionalMomentumStrategy` — rank the universe on trailing return,
  hold the top N equal-weighted. This is the one with a real prior behind it
  rather than a rule invented to have something to run.
- :class:`CrossSectionalReversalStrategy` — the same ranking sorted the other
  way at a five-day horizon. Momentum skips its most recent month precisely
  because reversal lives there; this rule trades what momentum steps around.

The last two are deliberately near-identical in construction — same formation
return, same parameter names, same dollar-neutral option — so that a comparison
between them measures the two effects rather than two implementations.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from shrap.research.strategy_evaluator.strategy import PanelWindow

CROSS_SECTIONAL_TREND_NAME = "cross-sectional-ma-crossover"
CROSS_SECTIONAL_MOMENTUM_NAME = "cross-sectional-momentum"
CROSS_SECTIONAL_REVERSAL_NAME = "cross-sectional-reversal"

DEFAULT_GROSS_EXPOSURE = 1.0
DEFAULT_LOOKBACK = 126
DEFAULT_SKIP = 21
DEFAULT_TOP_N = 10

# Short-horizon reversal defaults. Five sessions is the horizon the documented
# effect lives at (Lehmann 1990; Lo & MacKinlay 1990) and one skipped bar keeps
# the rule off the close where bid-ask bounce is largest. Neither was searched.
DEFAULT_REVERSAL_LOOKBACK = 5
DEFAULT_REVERSAL_SKIP = 1

# Validated by the pipeline's `_validate_param_bounds` against the record's
# declared `param_bounds`, same contract as the reference rule.
TREND_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "fast": (2.0, 100.0),
    "slow": (5.0, 400.0),
    "gross_exposure": (0.0, 1.0),
}

MOMENTUM_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "lookback": (21.0, 504.0),
    "skip": (0.0, 63.0),
    "top_n": (1.0, 50.0),
    "gross_exposure": (0.0, 1.0),
}

# Deliberately disjoint from MOMENTUM_PARAM_BOUNDS on `lookback`: momentum
# starts at 21 sessions, reversal stops at 21. The two rules trade opposite
# effects and the horizon is what separates them, so a spec that could express
# either is a spec that has stopped saying which one it is.
REVERSAL_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "lookback": (2.0, 21.0),
    "skip": (0.0, 5.0),
    "top_n": (1.0, 50.0),
    "gross_exposure": (0.0, 1.0),
}


def _equal_weights(
    tickers: tuple[str, ...], selected: list[str], gross_exposure: float
) -> dict[str, float]:
    """Spread ``gross_exposure`` equally over ``selected``; everything else flat.

    Every ticker in the panel is named explicitly, including the flat ones. The
    engine diffs weights per ticker to recover trades, so an omitted ticker
    would read as "unchanged" rather than "exit" — a silent way to never sell.
    """

    if not selected:
        return dict.fromkeys(tickers, 0.0)
    per_name = gross_exposure / len(selected)
    chosen = set(selected)
    return {t: (per_name if t in chosen else 0.0) for t in tickers}


@dataclass(frozen=True, slots=True)
class CrossSectionalTrendStrategy:
    """The reference crossover, applied to every ticker in the panel."""

    fast: int = 10
    slow: int = 30
    gross_exposure: float = DEFAULT_GROSS_EXPOSURE
    long_only: bool = True

    def __post_init__(self) -> None:
        if self.fast < 1 or self.slow < 1:
            raise ValueError("moving-average windows must be >= 1")
        if self.fast >= self.slow:
            raise ValueError("fast window must be shorter than slow window")
        if not 0.0 <= self.gross_exposure <= 1.0:
            raise ValueError("gross_exposure must lie in [0, 1]")

    @property
    def name(self) -> str:
        return CROSS_SECTIONAL_TREND_NAME

    @property
    def warmup(self) -> int:
        return self.slow

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        selected: list[str] = []
        for ticker in window.tickers:
            closes = window.closes(ticker)
            if len(closes) < self.slow:
                continue
            fast_ma = sum(closes[-self.fast :]) / self.fast
            slow_ma = sum(closes[-self.slow :]) / self.slow
            if fast_ma > slow_ma:
                selected.append(ticker)
        return _equal_weights(window.tickers, selected, self.gross_exposure)

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> CrossSectionalTrendStrategy:
        return cls(
            fast=int(params.get("fast", 10)),
            slow=int(params.get("slow", 30)),
            gross_exposure=float(params.get("gross_exposure", DEFAULT_GROSS_EXPOSURE)),
            long_only=bool(params.get("long_only", True)),
        )


def _long_short_weights(
    tickers: tuple[str, ...],
    longs: list[str],
    shorts: list[str],
    gross_exposure: float,
) -> dict[str, float]:
    """Dollar-neutral: each side gets half the gross, spread over its members.

    An empty side contributes nothing rather than handing its half to the other.
    In a market with nothing worth shorting the book is half long and half cash,
    which is an honest statement about opportunity — silently giving the long
    side full exposure would turn this back into a long-only strategy exactly
    when the long-only strategy does best, and hide the switch it was built to
    express.
    """

    weights = dict.fromkeys(tickers, 0.0)
    half = gross_exposure / 2.0
    if longs:
        per_long = half / len(longs)
        for ticker in longs:
            weights[ticker] = per_long
    if shorts:
        per_short = half / len(shorts)
        for ticker in shorts:
            weights[ticker] = -per_short
    return weights


@dataclass(frozen=True, slots=True)
class CrossSectionalMomentumStrategy:
    """Hold the top ``top_n`` names by trailing return, equal-weighted.

    ``skip`` omits the most recent bars from the ranking window. That is not a
    tuning knob — short-horizon reversal is a well-documented effect that runs
    opposite to momentum, so including the last month in the formation window
    mixes two opposing signals. Skipping it is the standard construction, and
    setting ``skip=0`` is a deliberate choice to measure something different.
    """

    lookback: int = DEFAULT_LOOKBACK
    skip: int = DEFAULT_SKIP
    top_n: int = DEFAULT_TOP_N
    gross_exposure: float = DEFAULT_GROSS_EXPOSURE
    long_short: bool = False
    """Short the bottom of the ranking as well as buying the top.

    Momentum is a two-sided effect and this rule ran half of it. The textbook
    construction (Jegadeesh-Titman) is long the winners AND short the losers;
    dropping the short side leaves a book that is structurally ~100% long
    equity, competing against a 100%-long benchmark on stock selection alone.

    The first evaluation showed exactly that shape — fold information ratio
    correlating +0.97 with fold RETURN once the crash is excluded. It beat the
    benchmark in the three folds the market ran hard (IR +1.090, +0.692,
    +1.073), was dead flat in the crash (-0.004), and lost in the two quiet
    years (-0.457, -0.241). That is a trend amplifier, not a market-neutral
    factor.

    **This is also the answer to "how does it know the market switched".** It
    does not have to. Holding both sides of the cross-section expresses the
    switch continuously — when leadership rolls over, names migrate from the
    long leg to the short leg on their own. No regime call, no classifier, no
    `spec.regime_gate`.

    **Symmetric, so no new numeric parameter.** The short leg is the bottom
    ``top_n``, mirroring the long leg's top ``top_n``, and requires a NEGATIVE
    formation return exactly as the long leg requires a positive one. Sizing is
    dollar-neutral, half the gross per side.
    """

    market_filter: bool = False
    """Hold nothing while the average name in the universe is falling.

    The rule already declines to hold a name with negative momentum, and in a
    broad drawdown that is not enough: a *relative* ranking always finds
    something to buy. Measured on the first real evaluation, the 2022 fold was
    -33.76% with 609 trades — the worst return and the HIGHEST turnover of any
    fold. It did not stand down, it churned into whatever was least-bad and got
    whipsawed by bear-market rallies. That is the strategy's own declared kill
    criterion #3 ("momentum crashes... a single fold with a large negative
    return is evidence about the strategy rather than noise").

    **This is not a regime gate.** `spec.regime_gate` is refused by the
    Evaluator, and rightly — gating on a classifier makes a strategy inherit
    that classifier's errors and turns "it works except when it doesn't" into
    something unfalsifiable. This condition is computed from the same price
    panel the rule already sees, using the same formation window, with no
    external signal and no new parameter to tune. It is part of the signal, not
    a switch on top of it.

    **Zero new numeric parameters, deliberately.** The market's formation return
    is the mean of the per-name formation returns already computed for the
    ranking, over the same `lookback` and `skip`. Adding a threshold or a
    separate window would make this a tuning knob and the revision a parameter
    sweep wearing a thesis.
    """

    def __post_init__(self) -> None:
        if self.lookback < 2:
            raise ValueError("lookback must span at least two bars")
        if self.skip < 0:
            raise ValueError("skip must not be negative")
        if self.skip >= self.lookback:
            raise ValueError("skip must be shorter than lookback, or nothing is ranked")
        if self.top_n < 1:
            raise ValueError("top_n must be at least 1")
        if not 0.0 <= self.gross_exposure <= 1.0:
            raise ValueError("gross_exposure must lie in [0, 1]")

    @property
    def name(self) -> str:
        return CROSS_SECTIONAL_MOMENTUM_NAME

    @property
    def warmup(self) -> int:
        # One extra bar: the formation return needs a bar before the window
        # starts to difference against.
        return self.lookback + 1

    def _formation_return(self, window: PanelWindow, ticker: str) -> float | None:
        closes = window.closes(ticker)
        if len(closes) < self.lookback + 1:
            return None
        end = len(closes) - self.skip
        start = end - (self.lookback - self.skip)
        if start < 0 or end <= start:
            return None
        first, last = closes[start], closes[end - 1]
        if first <= 0.0:
            return None
        return last / first - 1.0

    @staticmethod
    def _market_is_rising(scored: list[tuple[float, str]]) -> bool:
        """Is the average name in the universe up over the formation window?

        Computed from the same per-name formation returns already used for the
        ranking, so it costs nothing and cannot disagree with them about the
        window it measures. Names without enough history are absent from
        `scored` and so are excluded here too — a name that cannot be ranked
        does not get a vote on the market's state.
        """

        return sum(r for r, _ in scored) / len(scored) > 0.0

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        scored: list[tuple[float, str]] = []
        for ticker in window.tickers:
            r = self._formation_return(window, ticker)
            if r is not None:
                scored.append((r, ticker))
        if not scored:
            return dict.fromkeys(window.tickers, 0.0)
        if self.market_filter and not self._market_is_rising(scored):
            # Flat, and every ticker named at 0.0 so the engine reads an EXIT
            # rather than "unchanged" — standing down has to actually sell.
            return dict.fromkeys(window.tickers, 0.0)
        # Sort by ticker first so ties resolve deterministically rather than by
        # panel order — a reproducible backtest cannot depend on dict ordering.
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        if self.long_short:
            # `k` caps each leg at half the rankable names so the two slices can
            # never overlap. With 50 names and top_n=10 this is simply 10; it
            # only binds on a universe too small to fill both legs, where taking
            # `top_n` from each end would put a name on both sides of the book.
            n = len(scored)
            k = min(self.top_n, n // 2)
            longs = [t for r, t in scored[:k] if r > 0.0]
            shorts = [t for r, t in scored[n - k :] if r < 0.0]
            return _long_short_weights(window.tickers, longs, shorts, self.gross_exposure)
        # Long-only by construction: a negative formation return is a loser, and
        # holding it would make this a different strategy wearing this name.
        selected = [t for r, t in scored[: self.top_n] if r > 0.0]
        return _equal_weights(window.tickers, selected, self.gross_exposure)

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> CrossSectionalMomentumStrategy:
        return cls(
            lookback=int(params.get("lookback", DEFAULT_LOOKBACK)),
            skip=int(params.get("skip", DEFAULT_SKIP)),
            top_n=int(params.get("top_n", DEFAULT_TOP_N)),
            gross_exposure=float(params.get("gross_exposure", DEFAULT_GROSS_EXPOSURE)),
            market_filter=bool(params.get("market_filter", False)),
            long_short=bool(params.get("long_short", False)),
        )


@dataclass(frozen=True, slots=True)
class CrossSectionalReversalStrategy:
    """Buy the recent losers, sell the recent winners — the short-horizon mirror.

    Momentum's own docstring above already names this effect: it skips the most
    recent month *because* "short-horizon reversal is a well-documented effect
    that runs opposite to momentum". This rule trades the thing momentum steps
    around, at the horizon momentum deliberately avoids.

    The prior is Lehmann (1990) and Lo & MacKinlay (1990) — short-term
    contrarian profits in the cross-section of equity returns. Same standing as
    Jegadeesh-Titman behind the momentum rule: a documented, falsifiable,
    decades-old result, implemented as written rather than invented here.

    **Why this specific gap, and not a bear-market hedge.** The measured fold
    table of the momentum strategy (2026-07-29):

        2021  +70.41%   IR +1.090   beat
        2022  -33.76%   IR -0.004   LEVEL with the benchmark
        2023   +9.05%   IR -0.457   lost
        2024  +68.84%   IR +0.692   beat
        2025  +69.95%   IR +1.073   beat
        2026   +6.58%   IR -0.241   lost

    Momentum did not fail in the crash. Relative to simply owning the names it
    was dead level there (-0.004). It failed in the two **quiet, modestly
    positive** years, where it churned 455 and 330 trades to lag a basket that
    sat still. A bear-market strategy would hedge a risk that has not cost
    anything; this targets the two folds that actually did.

    That is a falsifiable claim and the point of the card: this rule must earn
    its keep **in 2023 and 2026 specifically**. An aggregate that looks fine
    while losing those two folds is a failure of the hypothesis, whatever the
    headline number says.

    **Symmetric with momentum by construction.** Same formation-return
    machinery, same parameter names, same dollar-neutral long/short option, same
    bounds contract. The only difference is the sort direction and the horizon
    defaults. Anything else would make the comparison between the two rules
    confounded by implementation rather than by the effect.

    ``skip=1`` rather than 0: the most recent close is where bid-ask bounce
    lives, and buying yesterday's worst close is the classic way to harvest a
    spread that does not exist at fill time. Skipping one bar is the standard
    defence, not a tuned value.
    """

    lookback: int = DEFAULT_REVERSAL_LOOKBACK
    skip: int = DEFAULT_REVERSAL_SKIP
    top_n: int = DEFAULT_TOP_N
    gross_exposure: float = DEFAULT_GROSS_EXPOSURE
    long_short: bool = False
    """Short the recent winners as well as buying the recent losers.

    Same reasoning as the momentum rule's own flag: a long-only contrarian book
    is still structurally long equity and competes against a 100%-long benchmark
    on selection alone. Holding both ends measures the reversal effect itself
    rather than the market plus a tilt.
    """

    def __post_init__(self) -> None:
        if self.lookback < 2:
            raise ValueError("lookback must span at least two bars")
        if self.skip < 0:
            raise ValueError("skip must not be negative")
        if self.skip >= self.lookback:
            raise ValueError("skip must be shorter than lookback, or nothing is ranked")
        if self.top_n < 1:
            raise ValueError("top_n must be at least 1")
        if not 0.0 <= self.gross_exposure <= 1.0:
            raise ValueError("gross_exposure must lie in [0, 1]")

    @property
    def name(self) -> str:
        return CROSS_SECTIONAL_REVERSAL_NAME

    @property
    def warmup(self) -> int:
        return self.lookback + 1

    def _formation_return(self, window: PanelWindow, ticker: str) -> float | None:
        """Identical to the momentum rule's, deliberately.

        The two strategies must measure the same quantity and disagree only
        about what to do with it. A separate implementation here would let the
        comparison drift on an accounting difference rather than on the effect.
        """

        closes = window.closes(ticker)
        if len(closes) < self.lookback + 1:
            return None
        end = len(closes) - self.skip
        start = end - (self.lookback - self.skip)
        if start < 0 or end <= start:
            return None
        first, last = closes[start], closes[end - 1]
        if first <= 0.0:
            return None
        return last / first - 1.0

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        scored: list[tuple[float, str]] = []
        for ticker in window.tickers:
            r = self._formation_return(window, ticker)
            if r is not None:
                scored.append((r, ticker))
        if not scored:
            return dict.fromkeys(window.tickers, 0.0)
        # ASCENDING — the one line that separates this rule from momentum.
        # Ticker breaks ties so the ordering cannot depend on dict iteration.
        scored.sort(key=lambda pair: (pair[0], pair[1]))
        if self.long_short:
            n = len(scored)
            k = min(self.top_n, n // 2)
            # Long the losers (negative formation return), short the winners
            # (positive). Mirrors the momentum rule's sign requirement: a leg is
            # only taken where the effect it trades is actually present.
            longs = [t for r, t in scored[:k] if r < 0.0]
            shorts = [t for r, t in scored[n - k :] if r > 0.0]
            return _long_short_weights(window.tickers, longs, shorts, self.gross_exposure)
        # Long-only: buy the fallers. A name that ROSE is not a reversal
        # candidate, so a universe where nothing fell holds nothing — the same
        # discipline that keeps the momentum rule out of a market with no
        # winners.
        selected = [t for r, t in scored[: self.top_n] if r < 0.0]
        return _equal_weights(window.tickers, selected, self.gross_exposure)

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> CrossSectionalReversalStrategy:
        return cls(
            lookback=int(params.get("lookback", DEFAULT_REVERSAL_LOOKBACK)),
            skip=int(params.get("skip", DEFAULT_REVERSAL_SKIP)),
            top_n=int(params.get("top_n", DEFAULT_TOP_N)),
            gross_exposure=float(params.get("gross_exposure", DEFAULT_GROSS_EXPOSURE)),
            long_short=bool(params.get("long_short", False)),
        )


__all__ = [
    "CROSS_SECTIONAL_MOMENTUM_NAME",
    "CROSS_SECTIONAL_REVERSAL_NAME",
    "CROSS_SECTIONAL_TREND_NAME",
    "DEFAULT_GROSS_EXPOSURE",
    "DEFAULT_LOOKBACK",
    "DEFAULT_SKIP",
    "DEFAULT_TOP_N",
    "MOMENTUM_PARAM_BOUNDS",
    "TREND_PARAM_BOUNDS",
    "CrossSectionalMomentumStrategy",
    "CrossSectionalTrendStrategy",
]
