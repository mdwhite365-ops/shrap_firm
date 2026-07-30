"""Short-horizon reversal — the documented counterpart to momentum.

The momentum rule's own docstring names this effect as its reason for skipping
the most recent month: "short-horizon reversal is a well-documented effect that
runs opposite to momentum". This strategy trades what that rule steps around.

**Why this and not a bear-market hedge.** Momentum's measured fold table
(2026-07-29) shows it did not fail in the crash — 2022 came in at IR -0.004,
level with the benchmark. It failed in 2023 (IR -0.457) and 2026 (IR -0.241),
both quiet, modestly-positive years where it churned 455 and 330 trades to lag a
basket that sat still. Reversal is the documented effect that earns in exactly
those conditions.

The construction is deliberately near-identical to the momentum rule — same
formation return, same parameter names, same dollar-neutral option — so that
comparing the two measures two effects rather than two implementations.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from shrap.research.strategy_evaluator.cross_sectional import (
    MOMENTUM_PARAM_BOUNDS,
    REVERSAL_PARAM_BOUNDS,
    CrossSectionalMomentumStrategy,
    CrossSectionalReversalStrategy,
)
from shrap.research.strategy_evaluator.strategy import BarSample, PricePanel

_START = date(2020, 1, 6)


def _series(closes: list[float]) -> list[BarSample]:
    return [BarSample(_START + timedelta(days=i), c, c, c, c, 1.0e9) for i, c in enumerate(closes)]


def _panel(paths: dict[str, list[float]]) -> PricePanel:
    return PricePanel.from_bars({t: _series(c) for t, c in paths.items()})


def _weights(strategy: CrossSectionalReversalStrategy, paths: dict[str, list[float]]):
    panel = _panel(paths)
    return dict(strategy.target_weights(panel.window(panel.n_bars - 1)))


def _flat_then(move: float, n: int = 40) -> list[float]:
    """A quiet series that makes one decisive move over the last 5 sessions."""

    series = [100.0] * n
    for _ in range(5):
        series.append(series[-1] * (1 + move))
    return series


# --- the direction, which is the whole rule ----------------------------------


def test_it_buys_the_recent_losers() -> None:
    w = _weights(
        CrossSectionalReversalStrategy(top_n=2),
        {
            "FELL1": _flat_then(-0.03),
            "FELL2": _flat_then(-0.02),
            "ROSE1": _flat_then(0.02),
            "ROSE2": _flat_then(0.03),
        },
    )

    assert w["FELL1"] > 0.0 and w["FELL2"] > 0.0
    assert w["ROSE1"] == 0.0 and w["ROSE2"] == 0.0


def test_it_is_the_exact_opposite_of_momentum_on_the_same_panel() -> None:
    """The two rules must disagree only about direction. If they ever pick the
    same names, one of them has stopped being what it claims."""

    paths = {
        "FELL1": _flat_then(-0.03),
        "FELL2": _flat_then(-0.02),
        "ROSE1": _flat_then(0.02),
        "ROSE2": _flat_then(0.03),
    }
    panel = _panel(paths)
    window = panel.window(panel.n_bars - 1)

    reversal = dict(
        CrossSectionalReversalStrategy(lookback=5, skip=1, top_n=2).target_weights(window)
    )
    momentum = dict(
        CrossSectionalMomentumStrategy(lookback=5, skip=1, top_n=2).target_weights(window)
    )

    held_by_reversal = {t for t, v in reversal.items() if v > 0}
    held_by_momentum = {t for t, v in momentum.items() if v > 0}
    assert held_by_reversal and held_by_momentum
    assert held_by_reversal.isdisjoint(held_by_momentum)


def test_a_name_that_rose_is_never_bought_long_only() -> None:
    """Symmetric with momentum refusing to hold a negative-momentum name. In a
    market where everything rose there is nothing to buy, and the rule holds
    nothing rather than buying the least-good riser."""

    w = _weights(
        CrossSectionalReversalStrategy(top_n=2),
        {f"UP{i}": _flat_then(0.01 + i * 0.005) for i in range(4)},
    )

    assert all(v == 0.0 for v in w.values())


# --- the short leg ------------------------------------------------------------


def test_long_short_takes_both_ends() -> None:
    w = _weights(
        CrossSectionalReversalStrategy(top_n=2, long_short=True),
        {
            "FELL1": _flat_then(-0.03),
            "FELL2": _flat_then(-0.02),
            "MID": _flat_then(0.0),
            "ROSE1": _flat_then(0.02),
            "ROSE2": _flat_then(0.03),
        },
    )

    assert w["FELL1"] > 0.0 and w["FELL2"] > 0.0
    assert w["ROSE1"] < 0.0 and w["ROSE2"] < 0.0


def test_the_long_short_book_is_dollar_neutral() -> None:
    w = _weights(
        CrossSectionalReversalStrategy(top_n=2, long_short=True),
        {
            "FELL1": _flat_then(-0.03),
            "FELL2": _flat_then(-0.02),
            "ROSE1": _flat_then(0.02),
            "ROSE2": _flat_then(0.03),
        },
    )

    longs = sum(v for v in w.values() if v > 0)
    shorts = sum(-v for v in w.values() if v < 0)
    assert abs(longs - shorts) < 1e-12
    assert abs(longs - 0.5) < 1e-12


def test_an_empty_leg_does_not_hand_its_half_to_the_other() -> None:
    """Same discipline as the momentum long/short rule. In a market with nothing
    that fell, the book is half short and half cash rather than silently
    doubling the short side."""

    w = _weights(
        CrossSectionalReversalStrategy(top_n=2, long_short=True),
        {f"UP{i}": _flat_then(0.01 + i * 0.005) for i in range(4)},
    )

    assert abs(sum(w.values()) + 0.5) < 1e-12  # half short, half cash


def test_the_legs_cannot_overlap_on_a_small_universe() -> None:
    w = _weights(
        CrossSectionalReversalStrategy(top_n=10, long_short=True),
        {
            "A": _flat_then(-0.03),
            "B": _flat_then(-0.01),
            "C": _flat_then(0.01),
            "D": _flat_then(0.03),
        },
    )

    longs = sum(1 for v in w.values() if v > 0)
    shorts = sum(1 for v in w.values() if v < 0)
    assert longs == shorts == 2


# --- construction discipline --------------------------------------------------


def test_the_skip_defaults_to_one_bar() -> None:
    """The most recent close is where bid-ask bounce lives, and buying
    yesterday's worst close is the classic way to harvest a spread that does not
    exist at fill time."""

    assert CrossSectionalReversalStrategy().skip == 1


def test_the_horizon_defaults_to_five_sessions() -> None:
    assert CrossSectionalReversalStrategy().lookback == 5


def test_the_bounds_cannot_express_a_momentum_horizon() -> None:
    """Momentum starts at 21 sessions, reversal stops at 21. A spec that could
    express either has stopped saying which effect it trades — and would let a
    parameter sweep quietly turn one strategy into the other."""

    assert REVERSAL_PARAM_BOUNDS["lookback"][1] <= MOMENTUM_PARAM_BOUNDS["lookback"][0]


def test_it_adds_no_parameter_momentum_does_not_have() -> None:
    """The two rules must be comparable. A knob on one side only would make any
    difference between them attributable to the knob."""

    assert set(REVERSAL_PARAM_BOUNDS) == set(MOMENTUM_PARAM_BOUNDS)


def test_the_formation_return_matches_the_momentum_rule_exactly() -> None:
    """Both rules must measure the same quantity and disagree only about what to
    do with it. A separate implementation would let the comparison drift on an
    accounting difference rather than on the effect."""

    paths = {"X": _flat_then(-0.02)}
    panel = _panel(paths)
    window = panel.window(panel.n_bars - 1)

    reversal = CrossSectionalReversalStrategy(lookback=5, skip=1)
    momentum = CrossSectionalMomentumStrategy(lookback=5, skip=1)

    assert reversal._formation_return(window, "X") == momentum._formation_return(window, "X")


def test_every_ticker_is_named_so_the_engine_reads_exits() -> None:
    w = _weights(
        CrossSectionalReversalStrategy(top_n=1),
        {"A": _flat_then(-0.03), "B": _flat_then(0.02), "C": _flat_then(0.01)},
    )

    assert len(w) == 3


def test_invalid_parameters_are_refused() -> None:
    with pytest.raises(ValueError, match="lookback"):
        CrossSectionalReversalStrategy(lookback=1)
    with pytest.raises(ValueError, match="skip"):
        CrossSectionalReversalStrategy(lookback=5, skip=5)


def test_the_flag_is_read_from_the_spec() -> None:
    on = CrossSectionalReversalStrategy.from_spec({"long_short": True})
    off = CrossSectionalReversalStrategy.from_spec({})

    assert on.long_short is True
    assert off.long_short is False
    assert off.lookback == 5 and off.skip == 1


# --- the seeds, and the claim they must answer for ---------------------------


def test_the_evaluator_dispatches_the_reversal_rule() -> None:
    from shrap.research.strategy_evaluator.pipeline import (
        RULE_CROSS_SECTIONAL_REVERSAL,
        _default_strategy_factory,
    )
    from shrap.research.strategy_seed.technical_strategies import (
        REVERSAL_SEEDS_BY_KEY,
        reversal_record,
    )

    record = reversal_record(REVERSAL_SEEDS_BY_KEY["xs-reversal-5-1-10-longshort"])
    strategy = _default_strategy_factory(record, list(record.tickers["long"]))

    assert record.spec["rule"] == RULE_CROSS_SECTIONAL_REVERSAL
    assert isinstance(strategy, CrossSectionalReversalStrategy)
    assert strategy.long_short is True


def test_both_seeds_are_lineage_roots_not_revisions_of_momentum() -> None:
    """They express a different effect with its own prior. Recording either as a
    revision of the momentum strategy would put a new hypothesis into that
    lineage's attempt count, which measures how many tries one idea has burned.
    """

    from shrap.research.strategy_seed.technical_strategies import (
        REVERSAL_SEEDS,
        reversal_record,
    )

    for seed in REVERSAL_SEEDS:
        record = reversal_record(seed)
        assert record.parent_strategy_id is None
        assert record.derived_from_evaluation_id is None


def test_the_kill_criteria_name_the_folds_this_must_win() -> None:
    """The falsifiable claim, written before the run so the result cannot be
    read to fit. An aggregate that looks respectable while losing 2023 and 2026
    has failed the hypothesis whatever its headline number says.
    """

    from shrap.research.strategy_seed.technical_strategies import reversal_kill_criteria

    criteria = " ".join(reversal_kill_criteria()).lower()
    assert "2023" in criteria and "2026" in criteria
    assert "correlate positively" in criteria


def test_the_long_only_seed_records_itself_as_a_deviation() -> None:
    """Dropping a leg is exactly the error the momentum rule made. Shipping the
    same deviation without recording it would repeat the failure that the
    long/short card was opened to correct."""

    from shrap.research.strategy_seed.technical_strategies import (
        REVERSAL_SEEDS_BY_KEY,
        reversal_record,
    )

    record = reversal_record(REVERSAL_SEEDS_BY_KEY["xs-reversal-5-1-10-longonly"])

    assert "DELIBERATE DEVIATION" in record.thesis
    assert record.spec["params"]["long_short"] is False


def test_the_long_short_seed_warns_it_cannot_be_traded_yet() -> None:
    from shrap.research.strategy_seed.technical_strategies import (
        REVERSAL_SEEDS_BY_KEY,
        reversal_record,
    )

    record = reversal_record(REVERSAL_SEEDS_BY_KEY["xs-reversal-5-1-10-longshort"])

    assert "NOT TRADEABLE YET" in record.thesis


def test_the_two_seeds_hash_differently() -> None:
    """Otherwise the second load is silently skipped as a duplicate and the
    firm evaluates one strategy while believing it evaluated two."""

    from shrap.research.strategy_seed.technical_strategies import (
        REVERSAL_SEEDS_BY_KEY,
        compute_reversal_spec_hash,
    )

    a = compute_reversal_spec_hash(REVERSAL_SEEDS_BY_KEY["xs-reversal-5-1-10-longshort"])
    b = compute_reversal_spec_hash(REVERSAL_SEEDS_BY_KEY["xs-reversal-5-1-10-longonly"])

    assert a != b


def test_the_seeds_trade_the_same_universe_as_momentum() -> None:
    """The comparison between the two rules is the point of the card, and a
    different universe would confound it with a selection difference."""

    from shrap.research.strategy_seed.technical_strategies import (
        MOMENTUM_SEEDS_BY_KEY,
        REVERSAL_SEEDS_BY_KEY,
    )

    momentum = MOMENTUM_SEEDS_BY_KEY["xs-momentum-126-21-10"].tickers
    reversal = REVERSAL_SEEDS_BY_KEY["xs-reversal-5-1-10-longshort"].tickers

    assert momentum == reversal


def test_the_strategy_ids_are_real_ulids() -> None:
    from ulid import ULID

    from shrap.research.strategy_seed.technical_strategies import REVERSAL_SEEDS

    for seed in REVERSAL_SEEDS:
        assert str(ULID.from_str(seed.strategy_id)) == seed.strategy_id
