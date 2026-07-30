"""The first effect the firm chose for itself.

arXiv 2607.10297 — peripheral assets in a denoised correlation network earn
higher risk-adjusted returns — reached the registry as a `missing-scorer`
capability gap on 2026-07-30, cited to a paper Tech Watcher's q-fin leg ingested
and the Hypothesis Generator read. Every earlier seed was picked by Mike out of
the canon.

Three properties carry the whole implementation, and each is a way it could
silently be a different effect:

1. **Market-mode removal.** Without it, "least correlated to everything" is
   "lowest beta", which the firm already holds under `low-volatility`.
2. **Date alignment.** The panel is ragged; correlating two compressed series
   would pair one name's Tuesday with another's Thursday.
3. **The O(N·T) identity.** `sum_j corr(i,j) = z_i · S` is exact, not an
   approximation — a test compares it against the naive pairwise matrix.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta
from itertools import pairwise

import pytest

from shrap.research.strategy_evaluator.factors import (
    ALL_FACTORS,
    CROSS_SECTIONAL_SCORERS,
    FACTOR_NETWORK_PERIPHERALITY,
    MIN_UNIVERSE_FOR_NETWORK,
    CrossSectionalFactorStrategy,
)
from shrap.research.strategy_evaluator.strategy import PanelWindow, PricePanel

_LOOKBACK = 60


def _panel(series: dict[str, list[float]]) -> PricePanel:
    """Build a panel from per-ticker close series. `nan` marks an absent bar."""

    n = len(next(iter(series.values())))
    dates = tuple(date(2024, 1, 1) + timedelta(days=i) for i in range(n))
    tickers = tuple(series)
    closes = {t: tuple(v) for t, v in series.items()}
    live = {t: tuple(math.isfinite(c) for c in v) for t, v in series.items()}
    empty = {t: tuple(0.0 for _ in v) for t, v in series.items()}
    return PricePanel(
        tickers=tickers,
        dates=dates,
        opens=closes,
        highs=closes,
        lows=closes,
        closes=closes,
        volumes=empty,
        live=live,
    )


def _walk(returns: list[float], start: float = 100.0) -> list[float]:
    price = start
    out = [price]
    for r in returns:
        price *= 1.0 + r
        out.append(price)
    return out


def _score(series: dict[str, list[float]], lookback: int = _LOOKBACK) -> dict[str, float]:
    panel = _panel(series)
    window = PanelWindow(panel, panel.n_bars - 1)
    return CROSS_SECTIONAL_SCORERS[FACTOR_NETWORK_PERIPHERALITY](window, lookback)


def _market_plus_idio(
    rng: random.Random, n: int, beta: float, idio_scale: float, market: list[float]
) -> list[float]:
    return [beta * m + rng.gauss(0.0, idio_scale) for m in market[:n]]


# --- it is a network measure, not a beta measure -------------------------------


def test_a_high_beta_name_with_unique_residuals_is_peripheral() -> None:
    """The property that separates this from `low-volatility`. A name can be
    violently market-sensitive and still be structurally alone; without market-
    mode removal it would rank as maximally connected."""

    rng = random.Random(7)
    market = [rng.gauss(0.0, 0.01) for _ in range(_LOOKBACK)]
    shared = [rng.gauss(0.0, 0.006) for _ in range(_LOOKBACK)]

    series: dict[str, list[float]] = {}
    # A cluster whose residuals move together — the network's core.
    for i in range(12):
        rng_i = random.Random(100 + i)
        series[f"CORE{i}"] = _walk(
            [1.0 * market[k] + shared[k] + rng_i.gauss(0.0, 0.002) for k in range(_LOOKBACK)]
        )
    # High beta, residuals correlated with nothing.
    rng_l = random.Random(999)
    series["LONER"] = _walk([2.5 * market[k] + rng_l.gauss(0.0, 0.006) for k in range(_LOOKBACK)])

    scores = _score(series)

    assert scores["LONER"] > max(v for t, v in scores.items() if t != "LONER")


def test_the_core_of_a_cluster_scores_lowest() -> None:
    rng = random.Random(11)
    market = [rng.gauss(0.0, 0.01) for _ in range(_LOOKBACK)]
    shared = [rng.gauss(0.0, 0.008) for _ in range(_LOOKBACK)]

    series: dict[str, list[float]] = {}
    for i in range(11):
        r = random.Random(200 + i)
        series[f"CORE{i}"] = _walk(
            [market[k] + shared[k] + r.gauss(0.0, 0.001) for k in range(_LOOKBACK)]
        )
    for i in range(4):
        r = random.Random(300 + i)
        series[f"FREE{i}"] = _walk([market[k] + r.gauss(0.0, 0.008) for k in range(_LOOKBACK)])

    scores = _score(series)

    assert min(scores, key=lambda t: scores[t]).startswith("CORE")
    assert max(scores, key=lambda t: scores[t]).startswith("FREE")


# --- the O(N·T) identity is exact ---------------------------------------------


def test_the_fast_path_equals_the_naive_pairwise_matrix() -> None:
    """`sum_j corr(i,j) = z_i · S` avoids ever forming the N x N matrix. If that
    identity were wrong the scores would be plausible and meaningless, so it is
    checked against the thing it replaces."""

    rng = random.Random(3)
    market = [rng.gauss(0.0, 0.01) for _ in range(_LOOKBACK)]
    series = {
        f"T{i}": _walk(_market_plus_idio(random.Random(i), _LOOKBACK, 0.5 + i * 0.1, 0.01, market))
        for i in range(12)
    }

    scores = _score(series)

    # Naive: build residuals the same way, then average pairwise correlations.
    returns = {t: [b / a - 1.0 for a, b in pairwise(v)] for t, v in series.items()}
    width = _LOOKBACK
    mkt = [sum(r[k] for r in returns.values()) / len(returns) for k in range(width)]
    mkt_mean = sum(mkt) / width
    mkt_var = sum((m - mkt_mean) ** 2 for m in mkt)
    resid: dict[str, list[float]] = {}
    for t, r in returns.items():
        own = sum(r) / width
        cov = sum((r[k] - own) * (mkt[k] - mkt_mean) for k in range(width))
        beta = cov / mkt_var
        e = [r[k] - beta * mkt[k] for k in range(width)]
        em = sum(e) / width
        resid[t] = [x - em for x in e]

    def corr(a: list[float], b: list[float]) -> float:
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)

    for t in series:
        others = [corr(resid[t], resid[o]) for o in series if o != t]
        expected = -sum(others) / len(others)
        assert scores[t] == pytest.approx(expected, abs=1e-9)


# --- the ragged panel ----------------------------------------------------------


def test_a_name_without_a_bar_on_every_window_date_is_excluded() -> None:
    """A correlation needs overlapping observations. The panel is ragged by
    design, so a name that listed midway has no relationship to measure yet —
    and pairing its compressed series with another's would pair one name's
    Tuesday with another's Thursday."""

    rng = random.Random(5)
    market = [rng.gauss(0.0, 0.01) for _ in range(_LOOKBACK)]
    series = {
        f"T{i}": _walk(_market_plus_idio(random.Random(i), _LOOKBACK, 1.0, 0.01, market))
        for i in range(12)
    }
    late = _walk(_market_plus_idio(random.Random(77), _LOOKBACK, 1.0, 0.01, market))
    series["LATE"] = [float("nan")] * 20 + late[20:]

    scores = _score(series)

    assert "LATE" not in scores
    assert len(scores) == 12


def test_an_interior_gap_excludes_a_name_too() -> None:
    """Not just late listings. A hole in the middle breaks alignment exactly the
    same way, and `closes()` would hide it by compressing it away."""

    rng = random.Random(9)
    market = [rng.gauss(0.0, 0.01) for _ in range(_LOOKBACK)]
    series = {
        f"T{i}": _walk(_market_plus_idio(random.Random(i), _LOOKBACK, 1.0, 0.01, market))
        for i in range(12)
    }
    holed = _walk(_market_plus_idio(random.Random(42), _LOOKBACK, 1.0, 0.01, market))
    holed[30] = float("nan")
    series["HOLED"] = holed

    assert "HOLED" not in _score(series)


def test_too_few_names_scores_nothing_rather_than_ranking_a_handful() -> None:
    """Below the floor it is not a network, it is a few pairs. Returning
    nothing reads as a flat book for that date; returning a ranking of three
    would trade estimation noise."""

    rng = random.Random(13)
    market = [rng.gauss(0.0, 0.01) for _ in range(_LOOKBACK)]
    series = {
        f"T{i}": _walk(_market_plus_idio(random.Random(i), _LOOKBACK, 1.0, 0.01, market))
        for i in range(MIN_UNIVERSE_FOR_NETWORK - 1)
    }

    assert _score(series) == {}


def test_a_short_panel_scores_nothing() -> None:
    rng = random.Random(17)
    market = [rng.gauss(0.0, 0.01) for _ in range(20)]
    series = {
        f"T{i}": _walk(_market_plus_idio(random.Random(i), 20, 1.0, 0.01, market))
        for i in range(12)
    }

    assert _score(series, lookback=_LOOKBACK) == {}


# --- it is wired into the rule ------------------------------------------------


def test_the_strategy_accepts_the_cross_sectional_factor() -> None:
    strategy = CrossSectionalFactorStrategy(factor=FACTOR_NETWORK_PERIPHERALITY, lookback=60)

    assert strategy.factor in ALL_FACTORS
    assert strategy.warmup == 61


def test_the_strategy_holds_the_most_peripheral_names() -> None:
    rng = random.Random(23)
    market = [rng.gauss(0.0, 0.01) for _ in range(_LOOKBACK)]
    shared = [rng.gauss(0.0, 0.008) for _ in range(_LOOKBACK)]
    series: dict[str, list[float]] = {}
    for i in range(11):
        r = random.Random(400 + i)
        series[f"CORE{i}"] = _walk(
            [market[k] + shared[k] + r.gauss(0.0, 0.001) for k in range(_LOOKBACK)]
        )
    for i in range(3):
        r = random.Random(500 + i)
        series[f"FREE{i}"] = _walk([market[k] + r.gauss(0.0, 0.01) for k in range(_LOOKBACK)])

    panel = _panel(series)
    weights = CrossSectionalFactorStrategy(
        factor=FACTOR_NETWORK_PERIPHERALITY, lookback=_LOOKBACK, top_n=3
    ).target_weights(PanelWindow(panel, panel.n_bars - 1))

    held = {t for t, w in weights.items() if w > 0.0}

    assert held == {"FREE0", "FREE1", "FREE2"}


def test_an_unknown_factor_still_raises() -> None:
    with pytest.raises(ValueError, match="unknown factor"):
        CrossSectionalFactorStrategy(factor="not-a-factor")
