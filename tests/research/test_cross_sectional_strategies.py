"""Cross-sectional rules, and the factory that selects a rule from a spec.

Deliberately a new file rather than an append to an existing one: KI-016 records
that two open PRs both appending to the same file tail merge into garbage, and
two are open right now.

The load-bearing tests here are the two that guard silent failures — an omitted
ticker reading as "hold" instead of "sell", and a single-ticker rule quietly
discarding extra tickers while shortening the panel.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta

import pytest

from shrap.research.strategy_evaluator.cross_sectional import (
    CROSS_SECTIONAL_MOMENTUM_NAME,
    CROSS_SECTIONAL_TREND_NAME,
    CrossSectionalMomentumStrategy,
    CrossSectionalTrendStrategy,
)
from shrap.research.strategy_evaluator.pipeline import (
    DEFERRED_RULES,
    RULE_CROSS_SECTIONAL_MOMENTUM,
    RULE_CROSS_SECTIONAL_TREND,
    RULE_REFERENCE_TREND,
    EvaluationPipeline,
    SpecHygieneError,
    _default_strategy_factory,
)
from shrap.research.strategy_evaluator.reference_strategy import ReferenceTrendStrategy
from shrap.research.strategy_evaluator.strategy import BarSample, PricePanel
from shrap.research.strategy_registry import STATUS_HYPOTHESIS, StrategyRecord

_START = date(2024, 1, 1)


@contextmanager
def monkeypatch_deferred(rule: str, reason: str) -> Iterator[None]:
    """Temporarily list a rule as deferred, restoring the real table after."""

    DEFERRED_RULES[rule] = reason
    try:
        yield
    finally:
        DEFERRED_RULES.pop(rule, None)


class _DummyPort:
    """Spec hygiene never calls a port; this stands in for all four."""


def _pipeline() -> EvaluationPipeline:
    dummy = _DummyPort()
    return EvaluationPipeline(
        registry=dummy,  # type: ignore[arg-type]
        reader=dummy,  # type: ignore[arg-type]
        store=dummy,  # type: ignore[arg-type]
        publisher=dummy,  # type: ignore[arg-type]
    )


def _bars(closes: list[float]) -> list[BarSample]:
    return [
        BarSample(
            session_date=_START + timedelta(days=i),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=1.0e9,
        )
        for i, c in enumerate(closes)
    ]


def _panel(series: dict[str, list[float]]) -> PricePanel:
    return PricePanel.from_bars({t: _bars(c) for t, c in series.items()})


def _record(spec: dict[str, object], tickers: list[str]) -> StrategyRecord:
    return StrategyRecord(
        strategy_id="01TEST",
        name="test",
        version=1,
        archetype="technical-catalyst",
        status=STATUS_HYPOTHESIS,
        source="mike-seed",
        thesis="test",
        anchor={},
        tickers={"long": tickers, "short": []},
        spec=spec,
        spec_hash="hash",
        regime_sizing_modifier=None,
        kill_criteria=["k"],
        code_ref=None,
        created_at=None,
        updated_at=None,
    )


# --- cross-sectional trend ---------------------------------------------------


def test_trend_holds_every_name_whose_fast_ma_is_above_its_slow_ma() -> None:
    """Breadth is the whole point: one rule, many simultaneous positions."""

    rising = [100.0 * (1.02**i) for i in range(40)]
    falling = [100.0 * (0.98**i) for i in range(40)]
    panel = _panel({"AAA": rising, "BBB": rising, "CCC": falling})

    weights = CrossSectionalTrendStrategy(fast=5, slow=20).target_weights(panel.window(39))

    assert weights["AAA"] == pytest.approx(0.5)
    assert weights["BBB"] == pytest.approx(0.5)
    assert weights["CCC"] == 0.0


def test_trend_names_every_ticker_including_the_flat_ones() -> None:
    """An omitted ticker reads as 'unchanged' to the engine, never as 'exit'.

    The engine recovers trades by diffing weights per ticker, so a rule that
    simply stops mentioning a name it has left would hold that position forever
    and never book the sale.
    """

    rising = [100.0 * (1.02**i) for i in range(40)]
    falling = [100.0 * (0.98**i) for i in range(40)]
    panel = _panel({"AAA": rising, "CCC": falling})

    weights = CrossSectionalTrendStrategy(fast=5, slow=20).target_weights(panel.window(39))

    assert set(weights) == {"AAA", "CCC"}


def test_trend_is_flat_everywhere_before_warmup() -> None:
    panel = _panel({"AAA": [100.0] * 10, "BBB": [100.0] * 10})
    weights = CrossSectionalTrendStrategy(fast=5, slow=20).target_weights(panel.window(9))
    assert set(weights.values()) == {0.0}


def test_trend_gross_exposure_is_never_exceeded() -> None:
    """Equal-weighting must divide the book, not multiply it across names."""

    rising = [100.0 * (1.02**i) for i in range(40)]
    panel = _panel(dict.fromkeys(("AAA", "BBB", "CCC", "DDD"), rising))

    weights = CrossSectionalTrendStrategy(fast=5, slow=20, gross_exposure=0.8).target_weights(
        panel.window(39)
    )

    assert sum(weights.values()) == pytest.approx(0.8)


def test_trend_rejects_an_inverted_window_pair() -> None:
    with pytest.raises(ValueError, match="fast window must be shorter"):
        CrossSectionalTrendStrategy(fast=30, slow=10)


# --- cross-sectional momentum ------------------------------------------------


def test_momentum_holds_the_strongest_names_only() -> None:
    n = 200
    strong = [100.0 * (1.01**i) for i in range(n)]
    mild = [100.0 * (1.001**i) for i in range(n)]
    weak = [100.0 * (0.99**i) for i in range(n)]
    panel = _panel({"STRONG": strong, "MILD": mild, "WEAK": weak})

    weights = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=1).target_weights(
        panel.window(n - 1)
    )

    assert weights["STRONG"] == pytest.approx(1.0)
    assert weights["MILD"] == 0.0
    assert weights["WEAK"] == 0.0


def test_momentum_never_holds_a_negative_formation_return() -> None:
    """top_n is a cap, not a quota — a portfolio of losers is a different rule."""

    n = 200
    weak = [100.0 * (0.99**i) for i in range(n)]
    panel = _panel({"AAA": weak, "BBB": weak, "CCC": weak})

    weights = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=3).target_weights(
        panel.window(n - 1)
    )

    assert set(weights.values()) == {0.0}


def test_momentum_skip_excludes_the_most_recent_bars() -> None:
    """A name that soared only in the skip window must not rank on it.

    Short-horizon reversal runs opposite to momentum; including the last month
    in the formation window mixes two opposing signals.
    """

    n = 200
    flat_then_spike = [100.0] * (n - 10) + [100.0 * (1.20**i) for i in range(1, 11)]
    steady = [100.0 * (1.005**i) for i in range(n)]
    panel = _panel({"SPIKE": flat_then_spike, "STEADY": steady})

    weights = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=1).target_weights(
        panel.window(n - 1)
    )

    assert weights["STEADY"] == pytest.approx(1.0)
    assert weights["SPIKE"] == 0.0


def test_momentum_ties_resolve_deterministically() -> None:
    """A reproducible backtest cannot depend on panel or dict ordering."""

    n = 200
    same = [100.0 * (1.01**i) for i in range(n)]
    forward = _panel({"AAA": same, "BBB": same, "CCC": same})
    reversed_ = _panel({"CCC": same, "BBB": same, "AAA": same})
    rule = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=2)

    assert dict(rule.target_weights(forward.window(n - 1))) == dict(
        rule.target_weights(reversed_.window(n - 1))
    )


def test_momentum_is_flat_before_warmup() -> None:
    panel = _panel({"AAA": [100.0] * 30, "BBB": [100.0] * 30})
    rule = CrossSectionalMomentumStrategy(lookback=126, skip=21, top_n=1)
    assert set(rule.target_weights(panel.window(29)).values()) == {0.0}
    assert rule.warmup == 127


def test_momentum_rejects_a_skip_that_swallows_the_lookback() -> None:
    with pytest.raises(ValueError, match="skip must be shorter than lookback"):
        CrossSectionalMomentumStrategy(lookback=21, skip=21)


# --- the factory -------------------------------------------------------------


def test_factory_defaults_to_the_reference_rule_when_no_rule_is_named() -> None:
    """Every strategy written before the registry existed assumed this."""

    rule = _default_strategy_factory(_record({"params": {"fast": 5, "slow": 20}}, ["SPY"]), ["SPY"])
    assert isinstance(rule, ReferenceTrendStrategy)
    assert rule.name != CROSS_SECTIONAL_TREND_NAME


def test_factory_selects_cross_sectional_rules_by_name() -> None:
    tickers = ["AAA", "BBB"]
    trend = _default_strategy_factory(
        _record({"rule": RULE_CROSS_SECTIONAL_TREND, "params": {"fast": 5, "slow": 20}}, tickers),
        tickers,
    )
    momentum = _default_strategy_factory(
        _record(
            {"rule": RULE_CROSS_SECTIONAL_MOMENTUM, "params": {"lookback": 126, "top_n": 5}},
            tickers,
        ),
        tickers,
    )
    assert trend.name == CROSS_SECTIONAL_TREND_NAME
    assert momentum.name == CROSS_SECTIONAL_MOMENTUM_NAME


def test_factory_refuses_a_single_ticker_rule_declaring_many_tickers() -> None:
    """The silent bug this guard exists for.

    `_build_panel` fetches every declared ticker and PricePanel intersects their
    session dates, so extra tickers on a single-name rule would *shorten* the
    usable history while contributing no trades — a shorter backtest that looks
    no worse, with nothing to indicate why.
    """

    tickers = ["SPY", "QQQ", "AAPL"]
    record = _record({"rule": RULE_REFERENCE_TREND, "params": {"fast": 5, "slow": 20}}, tickers)
    with pytest.raises(SpecHygieneError, match="trades one ticker"):
        _default_strategy_factory(record, tickers)


def test_factory_refuses_an_unknown_rule_rather_than_falling_back() -> None:
    """Fail closed. A typo must not silently evaluate a different strategy."""

    record = _record({"rule": "momentum-ish", "params": {}}, ["SPY"])
    with pytest.raises(SpecHygieneError, match="unknown rule"):
        _default_strategy_factory(record, ["SPY"])


def test_cross_sectional_rules_accept_the_full_universe() -> None:
    """The point of the card: 50 names through one factory call."""

    tickers = [f"T{i:02d}" for i in range(50)]
    record = _record(
        {"rule": RULE_CROSS_SECTIONAL_MOMENTUM, "params": {"lookback": 126, "top_n": 10}}, tickers
    )
    rule = _default_strategy_factory(record, tickers)
    assert isinstance(rule, CrossSectionalMomentumStrategy)
    assert rule.top_n == 10


# --- the benchmark gap: these rules must not be evaluable yet ----------------


def test_cross_sectional_rules_are_evaluable_now_that_a_benchmark_exists() -> None:
    """These shipped deferred in #110 and are enabled here.

    The deferral existed because the promote gate was an absolute Sharpe floor,
    which a diversified long-only portfolio clears on market drift alone.
    `map_verdict` now also gates on the information ratio against equal-weight
    buy-and-hold of the strategy's own panel, so the reason no longer holds.
    """

    for rule in (RULE_CROSS_SECTIONAL_TREND, RULE_CROSS_SECTIONAL_MOMENTUM):
        assert rule not in DEFERRED_RULES
        record = _record(
            {
                "rule": rule,
                "params": {"fast": 5, "slow": 20},
                "param_bounds": {"fast": [2, 100], "slow": [5, 400]},
            },
            ["AAA", "BBB"],
        )
        assert _pipeline()._check_spec_hygiene(record) == ["AAA", "BBB"]


def test_the_deferral_mechanism_still_refuses_whatever_is_listed() -> None:
    """DEFERRED_RULES is empty, not deleted — the situation will recur.

    Injecting an entry rather than relying on a real rule keeps this test
    honest about what it checks: the mechanism, not today's contents.
    """

    record = _record({"rule": "some-future-rule", "params": {}}, ["AAA"])
    with monkeypatch_deferred("some-future-rule", "its dependency does not exist"):
        with pytest.raises(SpecHygieneError, match="not evaluable yet"):
            _pipeline()._check_spec_hygiene(record)


def test_the_existing_single_name_rule_is_not_deferred() -> None:
    """The deferral is scoped to the rules the gap actually endangers.

    Absolute Sharpe is wrong for every long-only strategy, but a single-name
    timing rule is at least measured against being flat. Deferring it too would
    stop the firm evaluating anything at all.
    """

    assert RULE_REFERENCE_TREND not in DEFERRED_RULES
    record = _record(
        {
            "params": {"fast": 5, "slow": 20},
            "param_bounds": {"fast": [2, 100], "slow": [5, 400]},
        },
        ["SPY"],
    )
    assert _pipeline()._check_spec_hygiene(record) == ["SPY"]


def test_a_deferred_rule_is_refused_not_killed() -> None:
    """Refusal writes nothing — the strategy keeps its hypothesis stage.

    A rule we declined to evaluate has not earned a terminal verdict, and a kill
    would be unrecoverable: `killed` has no outbound transitions.
    """

    record = _record({"rule": "some-future-rule", "params": {}}, ["AAA"])
    with monkeypatch_deferred("some-future-rule", "not ready"):
        with pytest.raises(SpecHygieneError):
            _pipeline()._check_spec_hygiene(record)
    assert record.status == STATUS_HYPOTHESIS
