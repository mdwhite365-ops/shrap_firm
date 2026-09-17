"""Inverse-volatility weighting: the one weighting the firm has evidence for.

#226 measured, on the firm's own fifty names over 74 month-ends, that trailing
21-day volatility rank persists at Spearman rho **+0.880** month to month while
return rank sits at **+0.019**. Next month's volatility is close to known; next
month's return is not. Equal weighting ignores the only thing the firm can
forecast.

This is deliberately not an alpha claim. The names held are whatever the factor
selected; only the size of each changes, so it can help only through the
denominator of a risk-adjusted measure. These tests pin that boundary as much as
the arithmetic.
"""

from __future__ import annotations

import pytest

from shrap.research.strategy_evaluator.cross_sectional import (
    _equal_weights,
    _inverse_volatility_weights,
)
from shrap.research.strategy_evaluator.factors import (
    FACTOR_LOW_VOLATILITY,
    WEIGHTING_EQUAL,
    WEIGHTING_INVERSE_VOL,
    CrossSectionalFactorStrategy,
)

TICKERS = ("AAA", "BBB", "CCC", "DDD")


def test_a_quieter_name_gets_more_of_the_book() -> None:
    weights = _inverse_volatility_weights(TICKERS, ["AAA", "BBB"], 1.0, {"AAA": 0.01, "BBB": 0.02})

    assert weights["AAA"] == pytest.approx(2 / 3)
    assert weights["BBB"] == pytest.approx(1 / 3)


def test_the_book_still_sums_to_gross_exposure() -> None:
    """Whatever the volatilities, the strategy is not allowed to change its size."""

    for vols in ({"AAA": 0.01, "BBB": 0.9, "CCC": 0.05}, {"AAA": 1e-6, "BBB": 5.0, "CCC": 0.2}):
        weights = _inverse_volatility_weights(TICKERS, ["AAA", "BBB", "CCC"], 0.8, vols)
        assert sum(weights.values()) == pytest.approx(0.8)


def test_unselected_names_are_named_flat_not_omitted() -> None:
    """The engine diffs weights to recover trades.

    An omitted ticker reads as "unchanged" rather than "exit" — a silent way to
    never sell, which is the same shape as the momentum-account bug.
    """

    weights = _inverse_volatility_weights(TICKERS, ["AAA"], 1.0, {"AAA": 0.02})

    assert set(weights) == set(TICKERS)
    assert weights["DDD"] == 0.0


def test_a_missing_volatility_keeps_its_place_at_the_average() -> None:
    """A data gap must not silently change WHICH names are held.

    Dropping the name would turn a weighting experiment into a different
    portfolio, and the comparison against equal weighting would then be
    measuring two things at once.
    """

    weights = _inverse_volatility_weights(
        TICKERS, ["AAA", "BBB", "CCC"], 1.0, {"AAA": 0.02, "BBB": 0.02, "CCC": None}
    )

    assert weights["CCC"] > 0.0
    assert weights["CCC"] == pytest.approx(weights["AAA"])
    assert sum(weights.values()) == pytest.approx(1.0)


def test_zero_and_negative_volatility_count_as_missing() -> None:
    """A zero would divide; a flat series is absent data, not a riskless asset."""

    weights = _inverse_volatility_weights(TICKERS, ["AAA", "BBB"], 1.0, {"AAA": 0.02, "BBB": 0.0})

    assert weights["BBB"] == pytest.approx(weights["AAA"])


def test_no_usable_volatility_falls_back_to_equal_rather_than_refusing() -> None:
    """The factor's selection is still valid even when the vol estimate is not."""

    vols: dict[str, float | None] = {"AAA": None, "BBB": None}

    assert _inverse_volatility_weights(TICKERS, ["AAA", "BBB"], 1.0, vols) == _equal_weights(
        TICKERS, ["AAA", "BBB"], 1.0
    )


def test_nothing_selected_holds_nothing() -> None:
    assert _inverse_volatility_weights(TICKERS, [], 1.0, {}) == dict.fromkeys(TICKERS, 0.0)


# --- the strategy seam --------------------------------------------------------


def test_equal_is_the_default_so_the_registry_is_unchanged() -> None:
    """Every strategy already staged keeps its exact behaviour."""

    assert CrossSectionalFactorStrategy().weighting == WEIGHTING_EQUAL
    assert CrossSectionalFactorStrategy.from_spec({}).weighting == WEIGHTING_EQUAL


def test_the_weighting_is_read_from_the_spec() -> None:
    strategy = CrossSectionalFactorStrategy.from_spec(
        {"factor": FACTOR_LOW_VOLATILITY, "weighting": WEIGHTING_INVERSE_VOL}
    )

    assert strategy.weighting == WEIGHTING_INVERSE_VOL


def test_an_unknown_weighting_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown weighting"):
        CrossSectionalFactorStrategy(weighting="cap-weighted")


def test_inverse_vol_is_refused_on_a_long_short_book() -> None:
    """Scaling two legs independently silently breaks market neutrality.

    The long and short sleeves would each be normalised to gross_exposure by
    their own volatilities, so the book would no longer net to zero — a
    market-neutral strategy quietly acquiring a directional bet.
    """

    with pytest.raises(ValueError, match="market neutrality"):
        CrossSectionalFactorStrategy(weighting=WEIGHTING_INVERSE_VOL, long_short=True)
