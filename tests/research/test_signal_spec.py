"""Strategies as specs (`signal-spec`): the language, its limits, and fidelity.

The fidelity test is the one that matters most: a spec written to mean
"cross-sectional momentum, top ten, winners only" must choose exactly the names
the hand-written momentum class chooses, bar for bar. If it does not, every
strategy migrated to specs would be a different strategy wearing an old name.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta
from typing import Any

import pytest

from shrap.research.strategy_evaluator.cross_sectional import CrossSectionalMomentumStrategy
from shrap.research.strategy_evaluator.pipeline import (
    RULE_SIGNAL_SPEC,
    _default_strategy_factory,
)
from shrap.research.strategy_evaluator.signals import (
    MAX_DEPTH,
    MAX_NODES,
    SignalSpecError,
    SignalSpecStrategy,
    evaluate,
    parse,
)
from shrap.research.strategy_evaluator.strategy import BarSample, PricePanel
from shrap.research.strategy_registry import STATUS_HYPOTHESIS, StrategyRecord

START = date(2024, 1, 1)


def _bars(closes: list[float], volumes: list[float] | None = None) -> list[BarSample]:
    vols = volumes or [1_000.0] * len(closes)
    return [
        BarSample(
            session_date=START + timedelta(days=i),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=v,
        )
        for i, (c, v) in enumerate(zip(closes, vols, strict=True))
    ]


def _panel(series: dict[str, list[float]]) -> PricePanel:
    return PricePanel.from_bars({t: _bars(c) for t, c in series.items()})


def _last(panel: PricePanel):
    return panel.window(panel.n_bars - 1)


# --- validation -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"feature": "sentiment"}, "unknown feature"),
        ({"feature": "return"}, "requires 'lookback'"),
        ({"feature": "return", "lookback": 0}, "outside"),
        ({"feature": "return", "lookback": 10.5}, "whole number"),
        ({"feature": "return", "lookback": 10, "window": 3}, "takes no argument"),
        ({"feature": "sma_ratio", "fast": 50, "slow": 20}, "fast < slow"),
        ({"op": "rank"}, "exactly one operand"),
        ({"op": "sub", "args": [{"const": 1}]}, "wrong number"),
        ({"op": "power", "of": {"const": 1}}, "unknown op"),
        ({"const": float("inf")}, "finite"),
        ({"weights": 1}, "needs 'feature', 'op' or 'const'"),
    ],
)
def test_malformed_expressions_are_refused(raw: dict[str, Any], message: str) -> None:
    with pytest.raises(SignalSpecError, match=message):
        parse(raw)


def test_depth_and_size_are_bounded() -> None:
    deep: dict[str, Any] = {"feature": "return", "lookback": 5}
    for _ in range(MAX_DEPTH):
        deep = {"op": "neg", "of": deep}
    with pytest.raises(SignalSpecError, match="deeper"):
        parse(deep)

    wide = {"op": "add", "args": [{"const": 1}] * (MAX_NODES + 1)}
    with pytest.raises(SignalSpecError, match="nodes"):
        parse(wide)


def test_unknown_book_params_are_refused() -> None:
    with pytest.raises(SignalSpecError, match="unknown signal-spec param"):
        SignalSpecStrategy.from_spec({"signal": {"const": 1}, "stop_loss": 0.1})
    with pytest.raises(SignalSpecError, match="select"):
        SignalSpecStrategy.from_spec({"signal": {"const": 1}, "select": "best"})


# --- features, checked by hand ------------------------------------------------------


def test_features_compute_what_they_say() -> None:
    closes = [10.0, 11.0, 12.0, 11.0, 13.0]
    w = _last(_panel({"A": closes}))

    def one(raw: dict[str, Any]) -> float | None:
        return evaluate(parse(raw), w, ["A"])["A"]

    assert one({"feature": "return", "lookback": 4}) == pytest.approx(0.3)
    assert one({"feature": "return", "lookback": 2, "skip": 1}) == pytest.approx(0.0)
    assert one({"feature": "abs_return"}) == pytest.approx(13 / 11 - 1)
    assert one({"feature": "high_proximity", "lookback": 5}) == pytest.approx(1.0)
    assert one({"feature": "donchian", "lookback": 5}) == pytest.approx(1.0)
    assert one({"feature": "sma_ratio", "fast": 2, "slow": 4}) == pytest.approx(12.0 / 11.75 - 1)
    # Changes +1 +1 -1 +2: gains 4, losses 1.
    assert one({"feature": "rsi", "lookback": 4}) == pytest.approx(80.0)


def test_a_name_without_enough_history_scores_none_not_zero() -> None:
    w = _last(_panel({"A": [10.0, 11.0]}))
    assert evaluate(parse({"feature": "return", "lookback": 5}), w, ["A"]) == {"A": None}


def test_rank_shares_ties_and_ignores_unscorable_names() -> None:
    scores = evaluate(
        parse({"op": "rank", "of": {"feature": "return", "lookback": 1}}),
        _last(_panel({"A": [10.0, 11.0], "B": [10.0, 11.0], "C": [10.0, 9.0], "D": [10.0]})),
        ["A", "B", "C", "D"],
    )
    assert scores["C"] == 0.0
    assert scores["A"] == scores["B"] == pytest.approx(0.75)
    assert scores["D"] is None


def test_division_by_zero_is_unscorable() -> None:
    w = _last(_panel({"A": [10.0, 10.0]}))
    raw = {"op": "div", "args": [{"const": 1}, {"feature": "return", "lookback": 1}]}
    assert evaluate(parse(raw), w, ["A"]) == {"A": None}


# --- books ------------------------------------------------------------------------------


def _up(n: int, rate: float) -> list[float]:
    return [100.0 * (1.0 + rate) ** i for i in range(n)]


def test_top_bottom_and_positive_selection() -> None:
    panel = _panel({"A": _up(10, 0.03), "B": _up(10, 0.01), "C": _up(10, -0.02)})
    signal = {"feature": "return", "lookback": 5}

    def book(**kw: Any) -> dict[str, float]:
        strat = SignalSpecStrategy.from_spec({"signal": signal, **kw})
        return dict(strat.target_weights(_last(panel)))

    assert book(select="top", top_n=1) == {"A": 1.0, "B": 0.0, "C": 0.0}
    assert book(select="bottom", top_n=1) == {"A": 0.0, "B": 0.0, "C": 1.0}
    assert book(select="positive") == {"A": 0.5, "B": 0.5, "C": 0.0}
    # require_sign: the top-2 of a market where only one name rose holds one.
    assert book(select="top", top_n=3, require_sign=True) == {"A": 0.5, "B": 0.5, "C": 0.0}


def test_a_monthly_rebalance_holds_its_book_until_the_month_turns() -> None:
    # A leads through January; B overtakes mid-February.
    n = 70
    a = [100.0 + (i if i < 45 else 45 - (i - 45) * 3) for i in range(n)]
    b = [100.0 + i * 0.5 for i in range(n)]
    panel = _panel({"A": a, "B": b})
    strat = SignalSpecStrategy.from_spec(
        {
            "signal": {"feature": "return", "lookback": 5},
            "top_n": 1,
            "rebalance": "monthly",
        }
    )
    dates = panel.dates
    feb_first = next(i for i, d in enumerate(dates) if d.month == 2)
    mar_first = next(i for i, d in enumerate(dates) if d.month == 3)

    # Chosen on 1 Feb, when A still led — and still held on the last day of Feb,
    # although B had overtaken by then.
    assert strat.target_weights(panel.window(feb_first))["A"] == 1.0
    assert strat.target_weights(panel.window(mar_first - 1))["A"] == 1.0
    assert strat.target_weights(panel.window(mar_first))["B"] == 1.0


# --- wiring and fidelity ----------------------------------------------------------------


def _record(spec: dict[str, Any], tickers: list[str]) -> StrategyRecord:
    return StrategyRecord(
        strategy_id="spec-test",
        name="spec",
        version=1,
        archetype="technical-catalyst",
        status=STATUS_HYPOTHESIS,
        source="test",
        thesis="t",
        anchor={},
        tickers={"long": tickers},
        spec=spec,
        spec_hash="h",
        regime_sizing_modifier=None,
        kill_criteria=["x"],
        code_ref=None,
        created_at=None,
        updated_at=None,
    )


def test_the_factory_builds_a_signal_spec_strategy() -> None:
    spec = {"rule": RULE_SIGNAL_SPEC, "params": {"signal": {"feature": "rsi"}}}
    strat = _default_strategy_factory(_record(spec, ["A", "B"]), ["A", "B"])
    assert isinstance(strat, SignalSpecStrategy)
    assert strat.warmup == 15


def test_a_spec_reproduces_cross_sectional_momentum_bar_for_bar() -> None:
    """126/21 momentum is the return from 125 bars back to 21 bars back: 104 intervals."""

    rng = random.Random(7)
    series = {}
    for k in range(20):
        price, path = 100.0, []
        drift = rng.uniform(-0.001, 0.0015)
        for _ in range(260):
            price *= math.exp(drift + rng.gauss(0.0, 0.02))
            path.append(price)
        series[f"T{k:02d}"] = path
    panel = _panel(series)

    old = CrossSectionalMomentumStrategy.from_spec({"lookback": 126, "skip": 21, "top_n": 10})
    new = SignalSpecStrategy.from_spec(
        {
            "signal": {"feature": "return", "lookback": 104, "skip": 21},
            "top_n": 10,
            "require_sign": True,
        }
    )
    for i in range(127, panel.n_bars):
        window = panel.window(i)
        assert dict(new.target_weights(window)) == pytest.approx(dict(old.target_weights(window)))
