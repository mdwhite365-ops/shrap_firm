"""Deterministic feature computation for the Regime Classifier.

Pure functions over daily closes. Missing or insufficient data yields None —
never a silent interpolation (per spec: mark the feature as missing and
continue). All formulas are documented inline; there is no learned component.

Proxy note: the regime cards in docs/regimes/ reference VIX, MOVE, and DXY,
none of which are available on Alpaca's free IEX feed. The v0 feature set
substitutes realized-volatility and ETF-ratio proxies (SPY realized vol for
the vol level, 5d/60d vol ratio for the vol trend, HYG/TLT relative return
for credit conditions). Threshold calibration against these proxies is an
open question owned by Mike (see the agent spec).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields

TRADING_DAYS_PER_YEAR = 252


def log_returns(closes: Sequence[float]) -> list[float]:
    """Daily log returns; length is len(closes) - 1."""

    return [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]


def realized_vol(closes: Sequence[float], window: int) -> float | None:
    """Annualized close-to-close volatility over the last ``window`` returns."""

    returns = log_returns(closes)
    if len(returns) < window:
        return None
    sample = returns[-window:]
    mean = sum(sample) / window
    variance = sum((r - mean) ** 2 for r in sample) / (window - 1)
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)


def sma(closes: Sequence[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def pct_above_sma(closes: Sequence[float], window: int) -> float | None:
    """Last close relative to its SMA: (close / sma) - 1."""

    average = sma(closes, window)
    if average is None or average <= 0 or not closes:
        return None
    return closes[-1] / average - 1.0


def trend_50_over_200(closes: Sequence[float]) -> float | None:
    """Golden-cross style trend: (SMA50 / SMA200) - 1."""

    fast = sma(closes, 50)
    slow = sma(closes, 200)
    if fast is None or slow is None or slow <= 0:
        return None
    return fast / slow - 1.0


def window_return(closes: Sequence[float], window: int) -> float | None:
    """Simple return over the last ``window`` days."""

    if len(closes) < window + 1 or closes[-window - 1] <= 0:
        return None
    return closes[-1] / closes[-window - 1] - 1.0


def breadth_pct_above_sma(
    closes_by_symbol: Mapping[str, Sequence[float]], window: int = 200
) -> float | None:
    """Fraction of symbols trading above their own SMA. None if none computable."""

    evaluated = 0
    above = 0
    for closes in closes_by_symbol.values():
        distance = pct_above_sma(closes, window)
        if distance is None:
            continue
        evaluated += 1
        if distance > 0:
            above += 1
    if evaluated == 0:
        return None
    return above / evaluated


def return_dispersion(
    closes_by_symbol: Mapping[str, Sequence[float]], window: int = 20
) -> float | None:
    """Cross-sectional stdev of ``window``-day returns. Needs >= 3 symbols."""

    window_returns = [
        value
        for value in (window_return(closes, window) for closes in closes_by_symbol.values())
        if value is not None
    ]
    if len(window_returns) < 3:
        return None
    mean = sum(window_returns) / len(window_returns)
    variance = sum((r - mean) ** 2 for r in window_returns) / (len(window_returns) - 1)
    return math.sqrt(variance)


@dataclass(frozen=True, slots=True)
class FeatureVector:
    """The v0 statistical feature set. None means missing, never interpolated."""

    vol_20d: float | None  # SPY annualized realized vol, 20d window
    vol_trend: float | None  # vol_5d / vol_60d — >1 means vol rising
    pct_above_200dma: float | None  # SPY distance above its own 200dma
    trend_50_200: float | None  # SPY SMA50/SMA200 - 1
    breadth_above_200dma: float | None  # fraction of tracked symbols above 200dma
    dispersion_20d: float | None  # cross-sectional stdev of 20d returns
    credit_hyg_tlt_20d: float | None  # HYG 20d return minus TLT 20d return

    def as_payload(self) -> dict[str, float | None]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def missing(self) -> list[str]:
        return [f.name for f in fields(self) if getattr(self, f.name) is None]

    def get(self, name: str) -> float | None:
        value: float | None = getattr(self, name)
        return value


def compute_features(
    primary_closes: Sequence[float],
    closes_by_symbol: Mapping[str, Sequence[float]],
    hyg_closes: Sequence[float],
    tlt_closes: Sequence[float],
) -> FeatureVector:
    """Compute the full v0 feature vector.

    ``primary_closes`` is the index proxy (SPY). ``closes_by_symbol`` is the
    breadth/dispersion set (may include the primary symbol).
    """

    vol_5d = realized_vol(primary_closes, 5)
    vol_60d = realized_vol(primary_closes, 60)
    vol_trend = None
    if vol_5d is not None and vol_60d is not None and vol_60d > 0:
        vol_trend = vol_5d / vol_60d

    hyg_return = window_return(hyg_closes, 20)
    tlt_return = window_return(tlt_closes, 20)
    credit = None
    if hyg_return is not None and tlt_return is not None:
        credit = hyg_return - tlt_return

    return FeatureVector(
        vol_20d=realized_vol(primary_closes, 20),
        vol_trend=vol_trend,
        pct_above_200dma=pct_above_sma(primary_closes, 200),
        trend_50_200=trend_50_over_200(primary_closes),
        breadth_above_200dma=breadth_pct_above_sma(closes_by_symbol, 200),
        dispersion_20d=return_dispersion(closes_by_symbol, 20),
        credit_hyg_tlt_20d=credit,
    )
