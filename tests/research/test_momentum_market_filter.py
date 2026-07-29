"""Standing down when the whole universe is falling.

The first real evaluation of the momentum rule produced this fold table:

    fold 1  2021-12-27 -> 2022-11-22   -33.76%   sharpe -1.036   609 trades

The worst return AND the highest turnover of any fold. The rule already declines
to hold a name with negative momentum, so the intuition "it had no cash
condition" is wrong — it has one, per name. What it lacked was any notion of the
market's own state, and a *relative* ranking always finds something to buy: in
2022 energy and defense were genuinely positive, so it concentrated into them
and got whipsawed by bear-market rallies.

That is the strategy's own declared kill criterion #3, fired.
"""

from __future__ import annotations

from datetime import date, timedelta

from shrap.research.strategy_evaluator.cross_sectional import (
    MOMENTUM_PARAM_BOUNDS,
    CrossSectionalMomentumStrategy,
)
from shrap.research.strategy_evaluator.strategy import BarSample, PricePanel

_START = date(2020, 1, 6)


def _series(closes: list[float]) -> list[BarSample]:
    return [BarSample(_START + timedelta(days=i), c, c, c, c, 1.0e9) for i, c in enumerate(closes)]


def _panel(paths: dict[str, list[float]]) -> PricePanel:
    return PricePanel.from_bars({t: _series(c) for t, c in paths.items()})


def _ramp(start: float, rate: float, n: int = 200) -> list[float]:
    return [start * (rate**i) for i in range(n)]


# --- the 2022 shape ----------------------------------------------------------


def _bear_market_with_one_winner() -> PricePanel:
    """Nine names falling, one rising — 2022 in miniature.

    XLE is the stand-in for the sector that genuinely had positive six-month
    momentum while everything else fell.
    """

    paths = {f"FALL{i}": _ramp(100.0, 0.9975) for i in range(9)}
    paths["XLE"] = _ramp(100.0, 1.004)
    return _panel(paths)


def test_without_the_filter_it_buys_the_least_bad_name() -> None:
    """The behaviour that produced fold 1. Not a bug — it is what a relative
    ranking does, and it is exactly why the rule needs a market-level view."""

    panel = _bear_market_with_one_winner()
    strategy = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=3)

    weights = strategy.target_weights(panel.window(panel.n_bars - 1))

    assert weights["XLE"] > 0.0
    assert sum(weights.values()) > 0.0


def test_with_the_filter_it_stands_down_entirely() -> None:
    panel = _bear_market_with_one_winner()
    strategy = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=3, market_filter=True)

    weights = strategy.target_weights(panel.window(panel.n_bars - 1))

    assert weights["XLE"] == 0.0
    assert sum(weights.values()) == 0.0


def test_standing_down_names_every_ticker_so_the_engine_reads_an_exit() -> None:
    """The engine recovers trades by diffing weights per ticker. An omitted
    ticker reads as "unchanged" rather than "sell" — a silent way to stand down
    on paper while still holding the book."""

    panel = _bear_market_with_one_winner()
    strategy = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=3, market_filter=True)

    weights = strategy.target_weights(panel.window(panel.n_bars - 1))

    assert set(weights) == set(panel.tickers)
    assert all(w == 0.0 for w in weights.values())


# --- it must not change anything in a rising market --------------------------


def test_a_rising_market_is_unaffected_by_the_filter() -> None:
    """The filter must be inert when it should be — otherwise it is not a crash
    guard, it is a different strategy."""

    paths = {f"UP{i}": _ramp(100.0, 1.001 + i * 0.0005) for i in range(6)}
    panel = _panel(paths)
    plain = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=2)
    filtered = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=2, market_filter=True)

    window = panel.window(panel.n_bars - 1)
    assert filtered.target_weights(window) == plain.target_weights(window)


def test_a_mixed_market_that_is_net_up_still_trades() -> None:
    """Half falling, half rising strongly enough that the average name is up.
    The guard is for broad drawdowns, not for any dispersion at all."""

    paths = {f"DOWN{i}": _ramp(100.0, 0.9995) for i in range(3)}
    paths.update({f"UP{i}": _ramp(100.0, 1.003) for i in range(3)})
    panel = _panel(paths)
    filtered = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=2, market_filter=True)

    weights = filtered.target_weights(panel.window(panel.n_bars - 1))

    assert sum(weights.values()) > 0.0


def test_the_default_is_off_so_the_evaluated_strategy_is_unchanged() -> None:
    """`01KYNH9VKX...` has a verdict on record. Changing what that rule does in
    place would silently alter what its evaluation meant."""

    assert CrossSectionalMomentumStrategy().market_filter is False


# --- the property that keeps this a revision and not a parameter sweep -------


def test_the_filter_adds_no_new_numeric_parameter() -> None:
    """The whole design constraint.

    A threshold ("stand down below -2%") or a separate window ("use a 60-day
    market trend") would be a knob, and a knob turned after seeing 2022 is
    fitted to 2022. The market's formation return reuses `lookback` and `skip`
    exactly, so there is nothing here to tune.
    """

    assert "market_filter" not in MOMENTUM_PARAM_BOUNDS
    assert set(MOMENTUM_PARAM_BOUNDS) == {"lookback", "skip", "top_n", "gross_exposure"}


def test_the_market_view_uses_the_same_window_as_the_ranking() -> None:
    """If the two could disagree about the window, the guard would be measuring
    a different market from the one being ranked."""

    paths = {f"N{i}": _ramp(100.0, 0.997) for i in range(4)}
    panel = _panel(paths)
    window = panel.window(panel.n_bars - 1)
    strategy = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=2, market_filter=True)

    scored = [
        (r, t) for t in window.tickers if (r := strategy._formation_return(window, t)) is not None
    ]
    assert not strategy._market_is_rising(scored)
    assert sum(strategy.target_weights(window).values()) == 0.0


def test_a_name_that_cannot_be_ranked_gets_no_vote_on_the_market() -> None:
    """A newly-listed name has no formation return, so it is absent from the
    ranking — and must be absent from the market average too, or the guard would
    be measuring a universe the rule is not trading."""

    paths = {f"OLD{i}": _ramp(100.0, 1.002) for i in range(3)}
    panel = PricePanel.from_bars(
        {
            **{t: _series(c) for t, c in paths.items()},
            # Lists late and crashes; too short to rank, so it must not drag the
            # market average negative on its own.
            "NEW": _series(_ramp(100.0, 0.95, n=20))[:20],
        }
    )
    strategy = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=2, market_filter=True)

    weights = strategy.target_weights(panel.window(panel.n_bars - 1))

    assert weights["NEW"] == 0.0
    assert sum(weights.values()) > 0.0  # the rankable names are rising


# --- spec round-trip ---------------------------------------------------------


def test_the_filter_is_read_from_the_spec() -> None:
    on = CrossSectionalMomentumStrategy.from_spec(
        {"lookback": 126, "skip": 21, "top_n": 10, "market_filter": True}
    )
    off = CrossSectionalMomentumStrategy.from_spec({"lookback": 126, "skip": 21, "top_n": 10})

    assert on.market_filter is True
    assert off.market_filter is False


# --- the seeded revision -----------------------------------------------------


def test_the_standdown_seed_is_a_recorded_revision_of_the_evaluated_strategy() -> None:
    """Lineage's first real use. Without a parent this would register as a fresh
    idea at attempt one, and the firm would have no record that it is the second
    thing tried on the same hypothesis."""

    from shrap.research.strategy_seed.technical_strategies import (
        MOMENTUM_SEEDS_BY_KEY,
        momentum_record,
    )

    seed = MOMENTUM_SEEDS_BY_KEY["xs-momentum-126-21-10-standdown"]
    record = momentum_record(seed)

    assert record.parent_strategy_id == "01KYNH9VKXVQXJ48T4MF306PHE"
    assert record.is_revision
    assert record.derived_from_evaluation_id == "01KYQYKPHDRVYADBZH1VNCK55R"
    # The reason has to survive a reading months later as evidence rather than
    # assertion, so it names the fold, the numbers and the diagnosis.
    assert "Kill criterion 3" in (record.revision_reason or "")
    assert "-33.76%" in (record.revision_reason or "")


def test_the_revision_changes_only_the_market_condition() -> None:
    """A controlled comparison. If the universe, window, skip or decile moved as
    well, an improved information ratio could not be attributed to the guard."""

    from shrap.research.strategy_seed.technical_strategies import MOMENTUM_SEEDS_BY_KEY

    base = MOMENTUM_SEEDS_BY_KEY["xs-momentum-126-21-10"]
    revised = MOMENTUM_SEEDS_BY_KEY["xs-momentum-126-21-10-standdown"]

    assert (revised.lookback, revised.skip, revised.top_n) == (
        base.lookback,
        base.skip,
        base.top_n,
    )
    assert revised.tickers == base.tickers
    assert base.market_filter is False
    assert revised.market_filter is True
    assert revised.strategy_id != base.strategy_id


def test_the_revision_passes_spec_hygiene() -> None:
    """`market_filter` is a bool, so it needs no numeric bound — but the spec
    still has to survive the Evaluator's validator, including the `regime_gate`
    refusal this condition was carefully written NOT to trip."""

    from shrap.research.strategy_evaluator.pipeline import _validate_param_bounds
    from shrap.research.strategy_seed.technical_strategies import (
        MOMENTUM_SEEDS_BY_KEY,
        momentum_record,
    )

    record = momentum_record(MOMENTUM_SEEDS_BY_KEY["xs-momentum-126-21-10-standdown"])

    _validate_param_bounds(record.spec)
    assert "regime_gate" not in record.spec
    assert record.spec["params"]["market_filter"] is True
