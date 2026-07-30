"""Grouping positions that are really one trade.

The spec calls this "the single most-important defense against the 'everything
is one trade' disaster". A book of ten names that all move together is a
one-name book with extra commission, and every per-ticker limit in the firm
passes it.

Clustering is **single-linkage on realized return correlation**: A and B join
when their correlation clears the threshold, and a cluster is the transitive
closure of that relation. Single-linkage is the pessimistic choice — it merges
aggressively, so clusters come out larger and the cap binds sooner. Complete
linkage would produce tighter, more defensible groupings and a weaker limit; for
a veto authority the pessimistic error is the right one.

**A pair whose correlation cannot be computed is treated as correlated.** Too
little history, missing bars, a constant series — all of it resolves to "assume
they move together". Unknown correlation is not zero correlation, and treating
it as zero is precisely the assumption of independence this rule exists to
refuse. The cost is a cluster that is too big; the cost of the other error is
the disaster in the paragraph above.

No numpy. Runtime deps are deliberately small (see `pyproject.toml`) and a
Pearson correlation over a few hundred points does not justify adding one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

# Below this many overlapping return observations, a correlation estimate is
# noise. Mirrors `PortfolioLimits.min_cluster_history`, which is the value
# actually used; this is the floor beneath which no threshold makes sense.
ABSOLUTE_MIN_OBSERVATIONS = 2


def returns(closes: Sequence[float]) -> list[float]:
    """Simple period-over-period returns. Non-positive prices break the chain.

    A zero or negative close is not a price; rather than fabricate a return
    across it, the pair of observations is dropped, which shortens the overlap
    and makes the correlation more likely to be unavailable — the safe direction.
    """

    out: list[float] = []
    for previous, current in pairwise(closes):
        if previous <= 0.0 or current <= 0.0:
            continue
        out.append(current / previous - 1.0)
    return out


def correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    """Pearson correlation over the overlapping tail, or ``None`` if unusable.

    ``None`` is a real answer here and callers must not coerce it to 0.0 — see
    the module docstring. A constant series has zero variance and no defined
    correlation, which is ``None`` rather than 0.
    """

    n = min(len(left), len(right))
    if n < ABSOLUTE_MIN_OBSERVATIONS:
        return None
    a = list(left[-n:])
    b = list(right[-n:])
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_a <= 0.0 or var_b <= 0.0:
        return None
    return float(cov / (var_a**0.5 * var_b**0.5))


@dataclass(frozen=True, slots=True)
class Cluster:
    """A set of names treated as one exposure."""

    tickers: tuple[str, ...]
    weight: float
    """Summed **absolute** weight of the members, as a fraction of NAV."""

    @property
    def is_singleton(self) -> bool:
        return len(self.tickers) == 1


class _Union:
    """Union-find over ticker symbols. Small, so path compression is enough."""

    def __init__(self, items: Sequence[str]) -> None:
        self._parent = {item: item for item in items}

    def find(self, item: str) -> str:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root


def cluster_positions(
    weights: Mapping[str, float],
    price_history: Mapping[str, Sequence[float]],
    *,
    threshold: float,
    min_history: int,
) -> tuple[Cluster, ...]:
    """Group held names by correlation and sum each group's absolute weight.

    ``weights`` are signed weights as fractions of NAV; the cluster weight sums
    absolute values. A long and a short of two names that move together is a
    hedge, not a doubled exposure — but netting them here would let a book claim
    zero cluster exposure while holding two large offsetting positions whose
    correlation is an estimate that can break. Summing absolute values keeps the
    cap binding on the size of the bet rather than on its current sign.

    Names absent from ``weights`` are not clustered: this measures the book, not
    the universe.
    """

    tickers = sorted({t.strip().upper() for t in weights if weights[t] != 0.0})
    if not tickers:
        return ()

    series = {ticker: returns(price_history.get(ticker, ())) for ticker in tickers}
    union = _Union(tickers)
    for i, left in enumerate(tickers):
        for right in tickers[i + 1 :]:
            left_returns, right_returns = series[left], series[right]
            overlap = min(len(left_returns), len(right_returns))
            if overlap < min_history:
                # Not enough shared history to claim independence.
                union.union(left, right)
                continue
            rho = correlation(left_returns, right_returns)
            if rho is None or rho >= threshold:
                union.union(left, right)

    grouped: dict[str, list[str]] = {}
    for ticker in tickers:
        grouped.setdefault(union.find(ticker), []).append(ticker)

    normalised = {t.strip().upper(): w for t, w in weights.items()}
    clusters = [
        Cluster(
            tickers=tuple(sorted(members)),
            weight=sum(abs(normalised.get(m, 0.0)) for m in members),
        )
        for members in grouped.values()
    ]
    return tuple(sorted(clusters, key=lambda c: (-c.weight, c.tickers)))


def breaching_cluster(clusters: Sequence[Cluster], cap: float) -> Cluster | None:
    """The largest cluster over ``cap``, or ``None``."""

    for cluster in clusters:
        if cluster.weight > cap:
            return cluster
    return None


__all__ = [
    "ABSOLUTE_MIN_OBSERVATIONS",
    "Cluster",
    "breaching_cluster",
    "cluster_positions",
    "correlation",
    "returns",
]
