"""Four documented effects, each implemented as written.

Mike, 2026-07-30: *"we are testing known and coming up with unknown strats to
see what works and doesn't and learn and adapt."* This is the **known** half —
published, falsifiable, decades-old results, each seeded as its own lineage root
so none of them inherits another's attempt count.

The four:

``low-volatility``   Ang, Hodrick, Xing & Zhang (2006); Baker, Bradley & Wurgler
                     (2011). Low-volatility stocks earn higher risk-adjusted
                     returns than high-volatility ones — the anomaly that runs
                     directly against CAPM and has survived four decades of
                     attempts to explain it away.
``high-proximity``   George & Hwang (2004). Nearness to the 52-week high
                     predicts continued outperformance, and does so *better*
                     than past return itself — which is why it is a distinct
                     effect rather than momentum in a hat.
``volume-shock``     Gervais, Kaniel & Mingelgrin (2001), the high-volume return
                     premium. Names whose volume spikes above their own norm
                     tend to appreciate over the following weeks.
``time-series``      Moskowitz, Ooi & Pedersen (2012). Each name's OWN past
                     return predicts its own future return — an absolute signal,
                     not a relative one, which is what separates it from the
                     cross-sectional momentum already on record.

**Why one class and a factor table rather than four near-duplicate rules.**
Construction is held identical on purpose — same ranking, same equal weighting,
same dollar-neutral option — so a comparison across them measures the *effects*
rather than four implementations. This is the same discipline the reversal card
applied against momentum, extended to a family.

**What is deliberately absent.** True turnover (Datar, Naik & Radcliffe 1998)
needs shares outstanding to compute, and the firm stores none. Substituting
dollar volume would measure size, not turnover, and would be a different effect
wearing a cited name. ``volume-shock`` is a real effect that IS computable from
what the panel holds.

The 52-week high uses the highest **close**, not the highest intraday high:
``PanelWindow`` exposes closes and volumes only. Closing prices are the standard
construction in George & Hwang and avoid intraday spikes, so this is a
defensible reading rather than a compromise — but it is a reading, and it is
recorded here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from shrap.research.strategy_evaluator.cross_sectional import (
    DEFAULT_GROSS_EXPOSURE,
    DEFAULT_TOP_N,
    _equal_weights,
    _long_short_weights,
)
from shrap.research.strategy_evaluator.strategy import PanelWindow

CROSS_SECTIONAL_FACTOR_NAME = "cross-sectional-factor"

FACTOR_LOW_VOLATILITY = "low-volatility"
FACTOR_HIGH_PROXIMITY = "high-proximity"
FACTOR_VOLUME_SHOCK = "volume-shock"
FACTOR_TIME_SERIES = "time-series"

# Horizons are each effect's own documented one, not a shared default and not a
# search result. Changing one to match another would be tuning.
DEFAULT_FACTOR_LOOKBACKS: dict[str, int] = {
    FACTOR_LOW_VOLATILITY: 252,  # one year of realised vol
    FACTOR_HIGH_PROXIMITY: 252,  # the 52-week high, literally
    FACTOR_VOLUME_SHOCK: 50,  # ~10 weeks, the GKM formation window
    FACTOR_TIME_SERIES: 252,  # 12-month own-return, per Moskowitz et al.
}

FACTOR_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "lookback": (20.0, 504.0),
    "top_n": (1.0, 50.0),
    "gross_exposure": (0.0, 1.0),
}


def _returns(closes: tuple[float, ...]) -> list[float]:
    return [b / a - 1.0 for a, b in pairwise(closes) if a > 0.0 and b > 0.0]


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return float(variance**0.5)


def _score_low_volatility(window: PanelWindow, ticker: str, lookback: int) -> float | None:
    """Negated realised volatility, so a higher score is still 'better'.

    Negating rather than reversing the sort keeps every factor on one
    convention: rank descending, hold the top. A per-factor sort direction is
    exactly the kind of asymmetry that makes one rule quietly the inverse of
    what its name says.
    """

    closes = window.closes(ticker)
    if len(closes) < lookback + 1:
        return None
    vol = _stdev(_returns(closes[-(lookback + 1) :]))
    if vol is None or vol <= 0.0:
        return None
    return -vol


def _score_high_proximity(window: PanelWindow, ticker: str, lookback: int) -> float | None:
    """Current close as a fraction of the highest close in the window."""

    closes = window.closes(ticker)
    if len(closes) < lookback:
        return None
    recent = closes[-lookback:]
    peak = max(recent)
    if peak <= 0.0:
        return None
    return recent[-1] / peak


def _score_volume_shock(window: PanelWindow, ticker: str, lookback: int) -> float | None:
    """Latest volume relative to its own trailing average.

    Relative to the name's OWN norm, never to the universe's. A cross-name
    volume comparison would rank megacaps first every single day and measure
    size instead of the shock this effect is about.
    """

    volumes = window.volumes(ticker)
    if len(volumes) < lookback + 1:
        return None
    baseline = volumes[-(lookback + 1) : -1]
    average = sum(baseline) / len(baseline)
    if average <= 0.0:
        return None
    return volumes[-1] / average


def _score_time_series(window: PanelWindow, ticker: str, lookback: int) -> float | None:
    """The name's own trailing return."""

    closes = window.closes(ticker)
    if len(closes) < lookback + 1:
        return None
    first, last = closes[-(lookback + 1)], closes[-1]
    if first <= 0.0:
        return None
    return last / first - 1.0


FACTOR_SCORERS: dict[str, Callable[[PanelWindow, str, int], float | None]] = {
    FACTOR_LOW_VOLATILITY: _score_low_volatility,
    FACTOR_HIGH_PROXIMITY: _score_high_proximity,
    FACTOR_VOLUME_SHOCK: _score_volume_shock,
    FACTOR_TIME_SERIES: _score_time_series,
}

# Factors whose signal is ABSOLUTE rather than relative. A time-series rule holds
# every name with a positive own-return and nothing else — being the least-bad
# of a falling universe is not a buy signal, which is precisely the distinction
# from cross-sectional momentum.
ABSOLUTE_FACTORS: frozenset[str] = frozenset({FACTOR_TIME_SERIES})


@dataclass(frozen=True, slots=True)
class CrossSectionalFactorStrategy:
    """Rank the universe on one documented factor, hold the top ``top_n``."""

    factor: str = FACTOR_LOW_VOLATILITY
    lookback: int = 252
    top_n: int = DEFAULT_TOP_N
    gross_exposure: float = DEFAULT_GROSS_EXPOSURE
    long_short: bool = False

    def __post_init__(self) -> None:
        if self.factor not in FACTOR_SCORERS:
            known = ", ".join(sorted(FACTOR_SCORERS))
            raise ValueError(f"unknown factor {self.factor!r}; known factors are {known}")
        if self.lookback < 2:
            raise ValueError("lookback must span at least two bars")
        if self.top_n < 1:
            raise ValueError("top_n must be at least 1")
        if not 0.0 <= self.gross_exposure <= 1.0:
            raise ValueError("gross_exposure must lie in [0, 1]")
        if self.long_short and self.factor in ABSOLUTE_FACTORS:
            raise ValueError(
                f"{self.factor!r} is an absolute signal, so there is no bottom of a "
                "ranking to short; a long/short version would be a different effect"
            )

    @property
    def name(self) -> str:
        return CROSS_SECTIONAL_FACTOR_NAME

    @property
    def warmup(self) -> int:
        return self.lookback + 1

    @property
    def is_absolute(self) -> bool:
        return self.factor in ABSOLUTE_FACTORS

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        score = FACTOR_SCORERS[self.factor]
        scored: list[tuple[float, str]] = []
        for ticker in window.tickers:
            value = score(window, ticker, self.lookback)
            if value is not None:
                scored.append((value, ticker))
        if not scored:
            return dict.fromkeys(window.tickers, 0.0)

        if self.is_absolute:
            # Every name with a positive own-signal, equal-weighted. No ranking:
            # a time-series rule that took the top ten would silently become a
            # cross-sectional one.
            selected = [t for v, t in scored if v > 0.0]
            return _equal_weights(window.tickers, selected, self.gross_exposure)

        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        if self.long_short:
            n = len(scored)
            k = min(self.top_n, n // 2)
            longs = [t for _, t in scored[:k]]
            shorts = [t for _, t in scored[n - k :]]
            return _long_short_weights(window.tickers, longs, shorts, self.gross_exposure)
        selected = [t for _, t in scored[: self.top_n]]
        return _equal_weights(window.tickers, selected, self.gross_exposure)

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> CrossSectionalFactorStrategy:
        factor = str(params.get("factor", FACTOR_LOW_VOLATILITY))
        return cls(
            factor=factor,
            lookback=int(params.get("lookback", DEFAULT_FACTOR_LOOKBACKS.get(factor, 252))),
            top_n=int(params.get("top_n", DEFAULT_TOP_N)),
            gross_exposure=float(params.get("gross_exposure", DEFAULT_GROSS_EXPOSURE)),
            long_short=bool(params.get("long_short", False)),
        )


__all__ = [
    "ABSOLUTE_FACTORS",
    "CROSS_SECTIONAL_FACTOR_NAME",
    "DEFAULT_FACTOR_LOOKBACKS",
    "FACTOR_HIGH_PROXIMITY",
    "FACTOR_LOW_VOLATILITY",
    "FACTOR_PARAM_BOUNDS",
    "FACTOR_SCORERS",
    "FACTOR_TIME_SERIES",
    "FACTOR_VOLUME_SHOCK",
    "CrossSectionalFactorStrategy",
]
