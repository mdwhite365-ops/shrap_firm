"""Benchmark-relative evaluation: the gate that separates skill from exposure.

The finding this closes (2026-07-28): naive equal-weight buy-and-hold with no
timing rule at all scored Sharpe 1.026-1.158 through this engine on synthetic
data with realistic drift, clearing the 1.0 promote floor purely by being
invested. Absolute Sharpe cannot answer "did the strategy add anything."

The load-bearing test is `test_a_strategy_that_merely_holds_earns_no_active_edge`:
if that ever passes with a positive information ratio, the gate has stopped
working and the firm is back to promoting market beta.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from datetime import date, timedelta

import pytest

from shrap.research.strategy_evaluator.benchmark import (
    BENCHMARK_NAME,
    EqualWeightBuyAndHold,
    active_returns,
)
from shrap.research.strategy_evaluator.engine import (
    DEFAULT_INFORMATION_RATIO_FLOOR,
    EvalConfig,
    walk_forward,
)
from shrap.research.strategy_evaluator.strategy import BarSample, PanelWindow, PricePanel
from shrap.research.strategy_evaluator.verdict import (
    REASON_BELOW_INFORMATION_RATIO_FLOOR,
    REASON_NO_ACTIVE_EDGE,
    REASON_PROMOTE,
    VERDICT_HOLD,
    VERDICT_KILL,
    VERDICT_PROMOTE,
    map_verdict,
)

_START = date(2020, 1, 1)


def _bars(closes: list[float]) -> list[BarSample]:
    return [
        BarSample(
            session_date=_START + timedelta(days=i),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=5.0e8,
        )
        for i, c in enumerate(closes)
    ]


def _drifting(seed: int, n: int, drift: float = 0.0004) -> list[float]:
    r = random.Random(seed)
    px = [100.0]
    for _ in range(n - 1):
        px.append(max(1.0, px[-1] * (1.0 + r.gauss(drift, 0.012))))
    return px


class AlwaysInvested:
    """Holds everything, always — identical to the benchmark by construction."""

    name = "always-invested"
    warmup = 1

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        n = len(window.tickers)
        return dict.fromkeys(window.tickers, 1.0 / n)


class PerfectTiming:
    """Invested only when the next bar rises. Cheating, and that is the point."""

    name = "perfect-timing"
    warmup = 1

    def __init__(self, panel: PricePanel) -> None:
        self._panel = panel

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        i = window.current_index
        out: dict[str, float] = {}
        n = len(window.tickers)
        for t in window.tickers:
            closes = self._panel.closes[t]
            nxt = i + 1
            rises = nxt < len(closes) and closes[nxt] > closes[i]
            out[t] = (1.0 / n) if rises else 0.0
        return out


# --- the benchmark rule ------------------------------------------------------


def test_benchmark_holds_every_name_equally_and_names_them_all() -> None:
    panel = PricePanel.from_bars({t: _bars(_drifting(i, 60)) for i, t in enumerate("ABC")})
    weights = EqualWeightBuyAndHold().target_weights(panel.window(59))
    assert set(weights) == {"A", "B", "C"}
    assert all(w == pytest.approx(1 / 3) for w in weights.values())
    assert EqualWeightBuyAndHold().name == BENCHMARK_NAME


def test_benchmark_warmup_is_one_so_it_cannot_shorten_the_window() -> None:
    """A larger benchmark warmup would silently shrink what both runs measure."""

    assert EqualWeightBuyAndHold().warmup == 1


def test_active_returns_refuse_mismatched_periods() -> None:
    """Unequal lengths mean the two runs were not wired to the same periods."""

    with pytest.raises(ValueError, match="same periods"):
        active_returns([0.1, 0.2], [0.1])


# --- the gate ----------------------------------------------------------------


def test_a_strategy_that_merely_holds_earns_no_active_edge() -> None:
    """THE test. Being invested must not read as being skilful.

    `AlwaysInvested` is the benchmark wearing a different name. Its absolute
    Sharpe is whatever the market did; its information ratio must be ~0, and the
    verdict must be a kill rather than a promotion.
    """

    panel = PricePanel.from_bars({f"T{i:02d}": _bars(_drifting(i, 900)) for i in range(20)})
    result = walk_forward(panel, AlwaysInvested(), EvalConfig())

    assert result.active.information_ratio == pytest.approx(0.0, abs=1e-6)
    verdict = map_verdict(
        anchor_fresh=True,
        total_trades=result.aggregate.trade_count,
        base_sharpe=result.aggregate.sharpe,
        stress_sharpe=result.stress.sharpe,
        min_trades=0,
        sharpe_floor=1.0,
        information_ratio=result.active.information_ratio,
        information_ratio_floor=DEFAULT_INFORMATION_RATIO_FLOOR,
    )
    assert (verdict.verdict, verdict.reason) == (VERDICT_KILL, REASON_NO_ACTIVE_EDGE)


def test_genuine_timing_skill_produces_a_positive_information_ratio() -> None:
    """The gate must admit real skill, or it is only a switch-off."""

    panel = PricePanel.from_bars({f"T{i:02d}": _bars(_drifting(i, 900)) for i in range(5)})
    result = walk_forward(panel, PerfectTiming(panel), EvalConfig())

    assert result.active.information_ratio > 1.0
    assert result.active.active_total_return > 0.0


def test_the_benchmark_is_reported_alongside_the_strategy() -> None:
    """A verdict a human cannot audit against its own comparison is not auditable."""

    panel = PricePanel.from_bars({f"T{i:02d}": _bars(_drifting(i, 900)) for i in range(5)})
    result = walk_forward(panel, AlwaysInvested(), EvalConfig())
    blob = result.as_dict()["active"]
    assert set(blob) == {
        "information_ratio",
        "active_total_return",
        "benchmark_sharpe",
        "benchmark_total_return",
        "n_periods",
    }
    assert blob["n_periods"] == result.aggregate.n_periods


# --- verdict priority --------------------------------------------------------


def _v(**kw: object) -> tuple[str, str]:
    base = {
        "anchor_fresh": True,
        "total_trades": 500,
        "base_sharpe": 2.0,
        "stress_sharpe": 1.0,
        "min_trades": 150,
        "sharpe_floor": 1.0,
        "information_ratio": 1.0,
        "information_ratio_floor": 0.5,
    }
    base.update(kw)
    verdict = map_verdict(**base)  # type: ignore[arg-type]
    return verdict.verdict, verdict.reason


def test_losing_to_the_benchmark_kills_rather_than_holds() -> None:
    """A strategy that traded all year to finish behind the basket it trades has
    been measured and found actively harmful. More data cannot redeem decisions
    already made."""

    assert _v(information_ratio=-0.4) == (VERDICT_KILL, REASON_NO_ACTIVE_EDGE)
    assert _v(information_ratio=0.0) == (VERDICT_KILL, REASON_NO_ACTIVE_EDGE)


def test_beating_the_benchmark_insufficiently_only_holds() -> None:
    assert _v(information_ratio=0.3) == (VERDICT_HOLD, REASON_BELOW_INFORMATION_RATIO_FLOOR)


def test_clearing_both_floors_promotes() -> None:
    assert _v(information_ratio=0.9) == (VERDICT_PROMOTE, REASON_PROMOTE)


def test_an_unmeasured_information_ratio_does_not_silently_pass() -> None:
    """None means 'not measured'. It must not gate, and must not be read as 0.0
    either — 0.0 would kill every caller that has not been updated."""

    assert _v(information_ratio=None) == (VERDICT_PROMOTE, REASON_PROMOTE)


def test_the_trade_count_gate_still_outranks_the_benchmark_gate() -> None:
    """Priority order is deliberate: an unpowered test is not evidence of
    anything, including of failing to beat a benchmark."""

    assert _v(total_trades=10, information_ratio=-5.0)[1] != REASON_NO_ACTIVE_EDGE
