"""Momentum is two-sided; the rule ran half of it.

Mike, 2026-07-29: *"momentum can be up or down, realizing that switch is key."*

The textbook construction (Jegadeesh-Titman) is long the winners AND short the
losers. Dropping the short leg leaves a book that is structurally ~100% long
equity, competing against a 100%-long benchmark on stock selection alone — and
the first evaluation showed exactly that shape:

    fold IR correlates +0.97 with fold RETURN (excluding the crash)
    beat the benchmark in the three folds the market ran hard
    dead flat in the crash (-0.004), lost in the two quiet years

That is a trend amplifier, not a market-neutral factor.

Holding both legs also answers "how does it know the market switched" without a
regime call: when leadership rolls over, names migrate from the long leg to the
short leg on their own.
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


def _ramp(rate: float, n: int = 200) -> list[float]:
    return [100.0 * (rate**i) for i in range(n)]


def _dispersed_panel() -> PricePanel:
    """Three strong risers, three clear fallers, and two in between."""

    paths = {
        "WIN1": _ramp(1.004),
        "WIN2": _ramp(1.003),
        "WIN3": _ramp(1.002),
        "MID1": _ramp(1.0002),
        "MID2": _ramp(0.9999),
        "LOSE3": _ramp(0.998),
        "LOSE2": _ramp(0.997),
        "LOSE1": _ramp(0.996),
    }
    return PricePanel.from_bars({t: _series(c) for t, c in paths.items()})


def _weights(strategy: CrossSectionalMomentumStrategy) -> dict[str, float]:
    panel = _dispersed_panel()
    return dict(strategy.target_weights(panel.window(panel.n_bars - 1)))


# --- the short leg ------------------------------------------------------------


def test_long_only_ignores_the_losers_entirely() -> None:
    """The behaviour on record: half the effect."""

    w = _weights(CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=2))

    assert w["WIN1"] > 0.0
    assert w["LOSE1"] == 0.0
    assert all(v >= 0.0 for v in w.values())


def test_long_short_takes_both_ends_of_the_ranking() -> None:
    w = _weights(CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=2, long_short=True))

    assert w["WIN1"] > 0.0 and w["WIN2"] > 0.0
    assert w["LOSE1"] < 0.0 and w["LOSE2"] < 0.0
    # The middle of the ranking is held on neither side.
    assert w["MID1"] == 0.0 and w["MID2"] == 0.0


def test_the_book_is_dollar_neutral() -> None:
    """Equal capital each side, which is what makes it a factor bet rather than
    a directional one."""

    w = _weights(CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=2, long_short=True))

    longs = sum(v for v in w.values() if v > 0)
    shorts = sum(-v for v in w.values() if v < 0)
    assert abs(longs - shorts) < 1e-12
    assert abs(longs - 0.5) < 1e-12  # half the gross per side


def test_the_short_leg_requires_a_negative_formation_return() -> None:
    """Symmetric with the long leg, which requires a positive one. In a market
    where nothing is falling there is nothing to short — the rule does not
    short the least-good riser just to fill the leg."""

    paths = {f"UP{i}": _ramp(1.001 + i * 0.0005) for i in range(6)}
    panel = PricePanel.from_bars({t: _series(c) for t, c in paths.items()})
    strategy = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=2, long_short=True)

    w = strategy.target_weights(panel.window(panel.n_bars - 1))

    assert all(v >= 0.0 for v in w.values())


def test_an_empty_short_leg_does_not_hand_its_half_to_the_long_leg() -> None:
    """Otherwise the strategy silently reverts to long-only exactly when
    long-only does best, hiding the switch it exists to express."""

    paths = {f"UP{i}": _ramp(1.001 + i * 0.0005) for i in range(6)}
    panel = PricePanel.from_bars({t: _series(c) for t, c in paths.items()})
    strategy = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=2, long_short=True)

    w = strategy.target_weights(panel.window(panel.n_bars - 1))

    assert abs(sum(w.values()) - 0.5) < 1e-12  # half invested, half cash


# --- the switch ---------------------------------------------------------------


def test_a_name_that_rolls_over_migrates_from_the_long_leg_to_the_short_leg() -> None:
    """The answer to "how does it know the market switched" — it does not have
    to. A name whose leadership breaks moves across the book on its own, with no
    regime call and no classifier."""

    # Rises for the first half, then falls harder for the second.
    rolled = [100.0 * (1.004**i) for i in range(140)]
    for _ in range(140):
        rolled.append(rolled[-1] * 0.99)
    steady = _ramp(1.0005, n=280)
    panel = PricePanel.from_bars(
        {
            "ROLLED": _series(rolled),
            "STEADY1": _series(steady),
            "STEADY2": _series(steady),
            "STEADY3": _series(steady),
        }
    )
    strategy = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=1, long_short=True)

    early = strategy.target_weights(panel.window(139))
    late = strategy.target_weights(panel.window(279))

    assert early["ROLLED"] > 0.0  # leading
    assert late["ROLLED"] < 0.0  # now shorted


# --- guards -------------------------------------------------------------------


def test_the_legs_cannot_overlap_on_a_small_universe() -> None:
    """With four names and top_n=10, taking the top and bottom ten would put
    every name on both sides of the book."""

    paths = {"A": _ramp(1.004), "B": _ramp(1.002), "C": _ramp(0.998), "D": _ramp(0.996)}
    panel = PricePanel.from_bars({t: _series(c) for t, c in paths.items()})
    strategy = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=10, long_short=True)

    w = strategy.target_weights(panel.window(panel.n_bars - 1))

    assert w["A"] > 0.0 and w["D"] < 0.0
    longs = sum(1 for v in w.values() if v > 0)
    shorts = sum(1 for v in w.values() if v < 0)
    assert longs == shorts == 2  # half the universe per side, no overlap


def test_the_default_is_long_only_so_the_evaluated_strategy_is_unchanged() -> None:
    assert CrossSectionalMomentumStrategy().long_short is False


def test_it_adds_no_new_numeric_parameter() -> None:
    """The short leg is the bottom `top_n`, mirroring the long leg. A separate
    `short_n` would be a knob, and a knob chosen after seeing the fold table is
    fitted to it."""

    assert set(MOMENTUM_PARAM_BOUNDS) == {"lookback", "skip", "top_n", "gross_exposure"}


def test_every_ticker_is_named_so_the_engine_reads_exits() -> None:
    w = _weights(CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=2, long_short=True))

    assert len(w) == 8
    assert all(isinstance(v, float) for v in w.values())


def test_the_flag_is_read_from_the_spec() -> None:
    on = CrossSectionalMomentumStrategy.from_spec({"top_n": 10, "long_short": True})
    off = CrossSectionalMomentumStrategy.from_spec({"top_n": 10})

    assert on.long_short is True
    assert off.long_short is False


# --- the seeded revision, and the gap it must not hide ------------------------


def test_the_long_short_seed_is_a_recorded_revision() -> None:
    from shrap.research.strategy_seed.technical_strategies import (
        MOMENTUM_SEEDS_BY_KEY,
        momentum_record,
    )

    record = momentum_record(MOMENTUM_SEEDS_BY_KEY["xs-momentum-126-21-10-longshort"])

    assert record.parent_strategy_id == "01KYNH9VKXVQXJ48T4MF306PHE"
    assert record.spec["params"]["long_short"] is True
    assert "half the effect" in (record.revision_reason or "").lower()


def test_the_thesis_states_that_it_cannot_be_traded_yet() -> None:
    """The Strategy Runner treats a negative weight as flat and never opens a
    short, so a promoted long-short strategy would trade only its long leg —
    silently, and at half the intended book.

    The evaluator can measure this; the live path cannot express it. That gap
    belongs in the record rather than in someone's memory.
    """

    from shrap.research.strategy_seed.technical_strategies import (
        MOMENTUM_SEEDS_BY_KEY,
        momentum_record,
    )

    record = momentum_record(MOMENTUM_SEEDS_BY_KEY["xs-momentum-126-21-10-longshort"])

    assert "NOT TRADEABLE YET" in record.thesis


def test_the_runner_still_refuses_to_open_a_short() -> None:
    """Pinned deliberately. If someone later makes the Runner short-capable,
    this test failing is the prompt to revisit the thesis above rather than a
    nuisance to delete."""

    from shrap.research.strategy_runner.engine import _invested

    assert _invested(0.5) is True
    assert _invested(0.0) is False
    assert _invested(-0.5) is False
