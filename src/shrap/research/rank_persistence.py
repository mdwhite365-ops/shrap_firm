"""Is a cross-sectional rank forecastable one step ahead?

Built to test the central empirical claim of arXiv 2607.27461 — *"the volatility
rank is forecastable one step ahead while the return rank stays close to
unforecastable"* — on the firm's own universe before anything was built on it.

**It replicated, and strongly** (50 names, 74 month-ends, 2026-09-17):

    volatility rank    mean rho +0.880   sd 0.051   positive in 100% of 73 months
    return rank        mean rho +0.019   sd 0.286   positive in  55%

The volatility result is not a surprise and should not be sold as one —
volatility clustering is among the oldest stylised facts in finance. What the
probe is for is confirming the mechanism holds on **fifty names** rather than the
five hundred the paper used, because a cross-sectional method's whole content is
in the cross-section and 50 is a tenth of the breadth it was validated on.

**The return result is the one that pays for this module.** It says 21-day return
rank carries no forecastable information on this universe — which independently
corroborates the Evaluator killing cross-sectional momentum at IR 0.415 and
0.392. Two different measurements, same conclusion, and the second explains the
first.

Deliberately a *probe*, not a strategy: it measures whether a signal has memory,
which is a necessary condition for a ranking strategy and nowhere near a
sufficient one. A rank can be perfectly forecastable and still unprofitable, and
this module must never be cited as evidence of edge.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

# One trading month. The paper ranks monthly; 21 bars is the usual convention and
# matches the firm's other 21-day windows (the momentum skip, for one).
DEFAULT_WINDOW = 21

# Below this the cross-section is too thin for a rank correlation to mean much.
MIN_NAMES = 20


@dataclass(frozen=True, slots=True)
class PersistenceResult:
    """How well a ranking predicts its own next value."""

    label: str
    mean_rho: float
    stdev_rho: float
    periods: int
    share_positive: float

    def line(self) -> str:
        return (
            f"{self.label:22} mean rho {self.mean_rho:+.3f}  sd {self.stdev_rho:.3f}  "
            f"periods {self.periods:3d}  rho>0 in {self.share_positive:.0%}"
        )


def spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    """Rank correlation. ``None`` when undefined rather than 0.0.

    Zero is a real answer meaning "no relationship"; returning it for "could not
    be computed" would silently pull a mean toward no-effect.
    """

    n = len(left)
    if n < 3 or n != len(right):
        return None
    lrank = {value: i for i, value in enumerate(sorted(left))}
    rrank = {value: i for i, value in enumerate(sorted(right))}
    xs = [lrank[value] for value in left]
    ys = [rrank[value] for value in right]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0.0 or dy == 0.0:
        return None
    return float(num / (dx * dy))


def realised_volatility(closes: Sequence[float]) -> float | None:
    """Population stdev of simple returns over the window."""

    if len(closes) < 3:
        return None
    returns = [
        closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1] > 0.0
    ]
    if len(returns) < 2:
        return None
    return float(statistics.pstdev(returns))


def window_return(closes: Sequence[float]) -> float | None:
    if len(closes) < 2 or closes[0] <= 0.0:
        return None
    return closes[-1] / closes[0] - 1.0


def rank_persistence(
    snapshots: Sequence[Mapping[str, float]],
    *,
    label: str,
    min_names: int = MIN_NAMES,
) -> PersistenceResult | None:
    """Spearman rho between consecutive snapshots, over names present in both.

    **Intersected per pair rather than over the whole history.** A name that
    listed midway through must not drop every period before it existed, and a
    name that delisted must not drop every period after — either would silently
    change which universe is being measured from one period to the next.
    """

    rhos: list[float] = []
    for current, following in pairwise(snapshots):
        shared = sorted(set(current) & set(following))
        if len(shared) < min_names:
            continue
        rho = spearman([current[t] for t in shared], [following[t] for t in shared])
        if rho is not None:
            rhos.append(rho)
    if not rhos:
        return None
    return PersistenceResult(
        label=label,
        mean_rho=sum(rhos) / len(rhos),
        stdev_rho=statistics.pstdev(rhos) if len(rhos) > 1 else 0.0,
        periods=len(rhos),
        share_positive=sum(1 for r in rhos if r > 0.0) / len(rhos),
    )


def month_end_snapshots(
    closes_by_ticker: Mapping[str, Sequence[tuple[date, float]]],
    *,
    window: int = DEFAULT_WINDOW,
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    """``(volatility snapshots, return snapshots)``, one entry per calendar month.

    The last observation in each month wins, which is the month-end the paper
    ranks on. Months are ordered, so consecutive entries are consecutive months.
    """

    vol_by_month: dict[tuple[int, int], dict[str, float]] = {}
    ret_by_month: dict[tuple[int, int], dict[str, float]] = {}
    for ticker, series in closes_by_ticker.items():
        ordered = sorted(series)
        for index in range(window, len(ordered)):
            day = ordered[index][0]
            prices = [price for _, price in ordered[index - window : index + 1]]
            vol = realised_volatility(prices)
            ret = window_return(prices)
            key = (day.year, day.month)
            if vol is not None:
                vol_by_month.setdefault(key, {})[ticker] = vol
            if ret is not None:
                ret_by_month.setdefault(key, {})[ticker] = ret
    months = sorted(set(vol_by_month) | set(ret_by_month))
    return (
        [vol_by_month.get(m, {}) for m in months],
        [ret_by_month.get(m, {}) for m in months],
    )


__all__ = [
    "DEFAULT_WINDOW",
    "MIN_NAMES",
    "PersistenceResult",
    "month_end_snapshots",
    "rank_persistence",
    "realised_volatility",
    "spearman",
    "window_return",
]
