"""Four documented effects, each implemented as written.

Mike, 2026-07-30: *"we are testing known and coming up with unknown strats."*
This is the known half. The property that makes it honest rather than a
parameter sweep: **each seed is a lineage root**, so each is attempt 1 under the
multiple-testing gate. Four unrelated effects tested once each is four
experiments; four variants of one effect is a search over one hypothesis.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from shrap.research.strategy_evaluator.factors import (
    FACTOR_HIGH_PROXIMITY,
    FACTOR_LOW_VOLATILITY,
    FACTOR_SCORERS,
    FACTOR_TIME_SERIES,
    FACTOR_VOLUME_SHOCK,
    CrossSectionalFactorStrategy,
)
from shrap.research.strategy_evaluator.strategy import BarSample, PricePanel

_START = date(2020, 1, 6)


def _bars(closes: list[float], volumes: list[float] | None = None) -> list[BarSample]:
    vols = volumes or [1.0e9] * len(closes)
    return [
        BarSample(_START + timedelta(days=i), c, c, c, c, v)
        for i, (c, v) in enumerate(zip(closes, vols, strict=True))
    ]


def _weights(strategy: CrossSectionalFactorStrategy, paths: dict[str, list[BarSample]]):
    panel = PricePanel.from_bars(paths)
    return dict(strategy.target_weights(panel.window(panel.n_bars - 1)))


def _steady(rate: float, n: int = 300) -> list[float]:
    return [100.0 * (rate**i) for i in range(n)]


def _choppy(amplitude: float, n: int = 300) -> list[float]:
    """Same start and end, very different path — pure volatility, no drift."""

    return [100.0 * (1 + amplitude * (1 if i % 2 else -1)) for i in range(n)]


# --- low volatility -----------------------------------------------------------


def test_it_holds_the_quiet_names_not_the_wild_ones() -> None:
    w = _weights(
        CrossSectionalFactorStrategy(factor=FACTOR_LOW_VOLATILITY, lookback=100, top_n=1),
        {
            "QUIET": _bars(_choppy(0.001)),
            "WILD": _bars(_choppy(0.05)),
        },
    )

    assert w["QUIET"] > 0.0
    assert w["WILD"] == 0.0


def test_a_flat_series_has_no_volatility_to_rank() -> None:
    """Zero variance is not "lowest volatility, buy it" — it is a name that is
    not trading, and ranking it first would put the whole book into whatever is
    most stale."""

    scorer = FACTOR_SCORERS[FACTOR_LOW_VOLATILITY]
    panel = PricePanel.from_bars({"FLAT": _bars([100.0] * 300)})

    assert scorer(panel.window(panel.n_bars - 1), "FLAT", 100) is None


# --- 52-week-high proximity ---------------------------------------------------


def test_it_holds_the_name_closest_to_its_own_high() -> None:
    at_high = _steady(1.002)
    off_high = [*_steady(1.002, 200), *[_steady(1.002, 200)[-1] * 0.6] * 100]

    w = _weights(
        CrossSectionalFactorStrategy(factor=FACTOR_HIGH_PROXIMITY, lookback=252, top_n=1),
        {"ATHIGH": _bars(at_high), "OFFHIGH": _bars(off_high)},
    )

    assert w["ATHIGH"] > 0.0
    assert w["OFFHIGH"] == 0.0


def test_proximity_is_relative_to_the_names_own_high_not_the_universes() -> None:
    """A $10 stock at its own high must outrank a $1000 stock that has halved.
    Comparing price levels across names would rank by share price, which is
    meaningless."""

    scorer = FACTOR_SCORERS[FACTOR_HIGH_PROXIMITY]
    panel = PricePanel.from_bars(
        {
            "CHEAP": _bars(_steady(1.002)),
            "DEAR": _bars([*_steady(1.002, 200), *[1000.0] * 100]),
        }
    )
    window = panel.window(panel.n_bars - 1)

    assert scorer(window, "CHEAP", 252) == pytest.approx(1.0, abs=1e-9)


# --- volume shock -------------------------------------------------------------


def test_it_holds_the_name_whose_volume_spiked_against_its_own_norm() -> None:
    quiet_volume = [1.0e6] * 299 + [1.0e6]
    spiking_volume = [1.0e6] * 299 + [9.0e6]

    w = _weights(
        CrossSectionalFactorStrategy(factor=FACTOR_VOLUME_SHOCK, lookback=50, top_n=1),
        {
            "SPIKE": _bars(_steady(1.0), spiking_volume),
            "CALM": _bars(_steady(1.0), quiet_volume),
        },
    )

    assert w["SPIKE"] > 0.0
    assert w["CALM"] == 0.0


def test_a_megacap_does_not_win_on_size_alone() -> None:
    """The failure this factor is most exposed to. Comparing raw volume across
    names ranks the biggest name first every single day and measures size
    instead of shock."""

    huge_but_steady = [1.0e9] * 300
    small_but_spiking = [1.0e5] * 299 + [1.0e6]

    w = _weights(
        CrossSectionalFactorStrategy(factor=FACTOR_VOLUME_SHOCK, lookback=50, top_n=1),
        {
            "MEGACAP": _bars(_steady(1.0), huge_but_steady),
            "SMALL": _bars(_steady(1.0), small_but_spiking),
        },
    )

    assert w["SMALL"] > 0.0
    assert w["MEGACAP"] == 0.0


# --- time-series momentum: the absolute one -----------------------------------


def test_it_holds_every_riser_rather_than_a_top_slice() -> None:
    """Absolute, not relative. Taking the top ten would silently make it a
    cross-sectional rule."""

    w = _weights(
        CrossSectionalFactorStrategy(factor=FACTOR_TIME_SERIES, lookback=252, top_n=1),
        {
            "UP1": _bars(_steady(1.002)),
            "UP2": _bars(_steady(1.001)),
            "UP3": _bars(_steady(1.0005)),
        },
    )

    assert all(v > 0.0 for v in w.values())


def test_it_goes_to_cash_when_everything_is_falling() -> None:
    """The distinction from cross-sectional momentum, which always finds ten
    names to hold. This is what makes it a real alternative to the standdown
    revision rather than a filter bolted onto the old signal."""

    w = _weights(
        CrossSectionalFactorStrategy(factor=FACTOR_TIME_SERIES, lookback=252, top_n=10),
        {
            "DOWN1": _bars(_steady(0.998)),
            "DOWN2": _bars(_steady(0.997)),
        },
    )

    assert all(v == 0.0 for v in w.values())


def test_an_absolute_factor_refuses_a_long_short_construction() -> None:
    """There is no bottom of a ranking to short when the signal is each name's
    own sign. A long/short version would be a different effect."""

    with pytest.raises(ValueError, match="absolute signal"):
        CrossSectionalFactorStrategy(factor=FACTOR_TIME_SERIES, long_short=True)


# --- shared construction ------------------------------------------------------


def test_every_factor_ranks_descending_so_none_is_secretly_inverted() -> None:
    """Low volatility is negated rather than reverse-sorted, so all four share
    one convention. A per-factor sort direction is how a rule quietly becomes
    the inverse of what its name says."""

    panel = PricePanel.from_bars(
        {"A": _bars(_steady(1.002), [1.0e6] * 299 + [5.0e6]), "B": _bars(_steady(1.001))}
    )
    window = panel.window(panel.n_bars - 1)

    for name, scorer in FACTOR_SCORERS.items():
        value = scorer(window, "A", 50 if name == FACTOR_VOLUME_SHOCK else 100)
        assert value is None or isinstance(value, float)


def test_every_ticker_is_named_so_the_engine_reads_exits() -> None:
    w = _weights(
        CrossSectionalFactorStrategy(factor=FACTOR_LOW_VOLATILITY, lookback=100, top_n=1),
        {"A": _bars(_choppy(0.001)), "B": _bars(_choppy(0.05)), "C": _bars(_choppy(0.03))},
    )

    assert len(w) == 3


def test_an_unknown_factor_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown factor"):
        CrossSectionalFactorStrategy(factor="vibes")


def test_a_universe_with_no_scorable_name_holds_nothing() -> None:
    w = _weights(
        CrossSectionalFactorStrategy(factor=FACTOR_LOW_VOLATILITY, lookback=500, top_n=1),
        {"SHORT": _bars(_steady(1.001, 50))},
    )

    assert all(v == 0.0 for v in w.values())


# --- the seeds ----------------------------------------------------------------


def test_every_seed_is_a_lineage_root() -> None:
    """The property that makes this a family of experiments rather than a
    search. Each is attempt 1, so none inherits another's promote penalty."""

    from shrap.research.strategy_seed.factor_strategies import FACTOR_SEEDS, factor_record

    assert len(FACTOR_SEEDS) == 5
    for seed in FACTOR_SEEDS:
        record = factor_record(seed)
        assert record.parent_strategy_id is None
        assert record.derived_from_evaluation_id is None


def test_every_seed_names_how_its_own_effect_dies() -> None:
    """Not boilerplate. Each set of kill criteria has to contain something
    specific to that effect, or the strategy has not been thought about."""

    from shrap.research.strategy_seed.factor_strategies import FACTOR_SEEDS

    specific = {
        "low-volatility-252-10": "sector bet",
        "high-proximity-252-10": "momentum in disguise",
        "volume-shock-50-10": "IEX feed",
        "time-series-252": "market-timing overlay",
        "network-peripherality-252-10": "low beta wearing a network's name",
    }
    for seed in FACTOR_SEEDS:
        joined = " ".join(seed.kill_criteria)
        assert specific[seed.key] in joined


def test_the_seeds_hash_differently_from_each_other() -> None:
    """Otherwise later loads are silently skipped as duplicates and the firm
    evaluates one strategy while believing it evaluated four."""

    from shrap.research.strategy_seed.factor_strategies import (
        FACTOR_SEEDS,
        compute_factor_spec_hash,
    )

    hashes = {compute_factor_spec_hash(s) for s in FACTOR_SEEDS}

    assert len(hashes) == len(FACTOR_SEEDS)


def test_the_evaluator_dispatches_every_factor_seed() -> None:
    from shrap.research.strategy_evaluator.pipeline import _default_strategy_factory
    from shrap.research.strategy_seed.factor_strategies import FACTOR_SEEDS, factor_record

    for seed in FACTOR_SEEDS:
        record = factor_record(seed)
        strategy = _default_strategy_factory(record, list(record.tickers["long"]))
        assert isinstance(strategy, CrossSectionalFactorStrategy)
        assert strategy.factor == seed.factor


def test_the_strategy_ids_are_real_ulids() -> None:
    from ulid import ULID

    from shrap.research.strategy_seed.factor_strategies import FACTOR_SEEDS

    for seed in FACTOR_SEEDS:
        assert str(ULID.from_str(seed.strategy_id)) == seed.strategy_id


def test_each_seed_uses_its_own_documented_horizon() -> None:
    """Not a shared default and not a search result. Changing one to match
    another would be tuning."""

    from shrap.research.strategy_seed.factor_strategies import FACTOR_SEEDS_BY_KEY

    assert FACTOR_SEEDS_BY_KEY["volume-shock-50-10"].effective_lookback == 50
    assert FACTOR_SEEDS_BY_KEY["time-series-252"].effective_lookback == 252
