"""``shrap-strategy-evaluate --timeframe``: backtesting on an intraday panel.

#217 built the intraday reader and nothing called it, so the question the whole
breadth argument rests on could not actually be asked. This is the wiring, and
the measurement it enables is the point:

    run one strategy through the same walk-forward at two grains
    and compare the two information ratios

``IR = IC x sqrt(breadth)`` predicts ~8.8x for 78 decisions a day against one,
**if** per-decision skill survives the shorter horizon. It usually degrades, and
costs scale with turnover. Nothing here assumes which way that lands.
"""

from __future__ import annotations

import argparse
from datetime import date
from typing import Any

import pytest

from shrap.research.strategy_evaluator.cli import (
    TIMEFRAME_DAILY,
    _build_parser,
    _check_intraday_window,
)
from shrap.research.strategy_evaluator.pipeline import EvaluationError
from shrap.research.strategy_evaluator.store import (
    IntradayEvaluatorReader,
    PostgresEvaluatorReader,
)


def _args(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {"timeframe": TIMEFRAME_DAILY, "window_years": None}
    base.update(overrides)
    return argparse.Namespace(**base)


# --- the unbounded-window guard -----------------------------------------------


def test_daily_keeps_its_unbounded_default() -> None:
    """The existing behaviour, and the reason the guard is conditional.

    "Every bar in the store" is the right default for daily bars and is the point
    of backfilling deep history. The guard must not take that away.
    """

    _check_intraday_window(_args(timeframe=TIMEFRAME_DAILY, window_years=None))


def test_intraday_without_a_window_is_refused() -> None:
    """One ticker-year is ~252 daily rows and ~98,000 at 1Min.

    Refused rather than silently defaulted, because only the caller knows how
    much history they backfilled, and a quietly truncated window would produce a
    verdict on a different panel than the one they think they asked for.
    """

    with pytest.raises(EvaluationError, match="needs an explicit --window-years"):
        _check_intraday_window(_args(timeframe="1Min", window_years=None))


def test_intraday_with_a_window_is_allowed() -> None:
    _check_intraday_window(_args(timeframe="15Min", window_years=1))


def test_the_refusal_names_the_grain_it_refused() -> None:
    """A message that does not say which timeframe tripped it is a worse message."""

    with pytest.raises(EvaluationError, match="5Min"):
        _check_intraday_window(_args(timeframe="5Min", window_years=None))


# --- parser defaults ----------------------------------------------------------


def test_timeframe_defaults_to_daily() -> None:
    """Every existing invocation keeps reading market_data.daily_bars."""

    args = _build_parser().parse_args(["--strategy-id", "01STRAT"])

    assert args.timeframe == TIMEFRAME_DAILY
    assert args.include_extended is False


def test_extended_hours_must_be_asked_for() -> None:
    args = _build_parser().parse_args(
        ["--strategy-id", "01STRAT", "--timeframe", "15Min", "--include-extended"]
    )

    assert args.include_extended is True


# --- the delegating reader ----------------------------------------------------


class FakeConn:
    def __init__(self) -> None:
        self.fetched: list[tuple[str, tuple[object, ...]]] = []
        self.fetchrow_result: dict[str, Any] | None = None

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        self.fetched.append((sql, args))
        return []

    async def fetchrow(self, sql: str, *args: object) -> dict[str, Any] | None:
        self.fetched.append((sql, args))
        return self.fetchrow_result

    async def execute(self, sql: str, *args: object) -> object:
        return "OK"


class FakeAcquire:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakePool:
    def __init__(self) -> None:
        self.conn = FakeConn()

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)


def test_it_satisfies_the_same_reader_port() -> None:
    """Five methods; only read_bars differs by grain.

    Anchor freshness, tier eligibility, the draw count and the parent's IR are
    questions about a *strategy*, not about a timeframe — which is why this
    delegates four and overrides one instead of either reader growing a mode flag.
    """

    intraday = IntradayEvaluatorReader(FakePool(), timeframe="15Min")

    for method in (
        "world_changer_status",
        "ticker_tier",
        "read_bars",
        "latest_information_ratio",
        "count_draws",
    ):
        assert hasattr(intraday, method), method
        assert hasattr(PostgresEvaluatorReader(FakePool()), method), method


async def test_read_bars_goes_to_the_intraday_table() -> None:
    pool = FakePool()
    reader = IntradayEvaluatorReader(pool, timeframe="15Min")

    await reader.read_bars("AAPL", date(2026, 9, 15), date(2026, 9, 15), "raw")

    sql, args = pool.conn.fetched[0]
    assert "market_data.intraday_bars" in sql
    assert "15Min" in args


async def test_the_other_four_go_to_the_daily_reader() -> None:
    """Delegation is real, not a reimplementation that could drift."""

    pool = FakePool()
    reader = IntradayEvaluatorReader(pool, timeframe="15Min")

    await reader.ticker_tier("AAPL")
    await reader.world_changer_status("01CAND")

    issued = " ".join(sql for sql, _ in pool.conn.fetched)
    assert "research.universe_tiers" in issued
    assert "research.world_changers" in issued
    assert "intraday_bars" not in issued


async def test_extended_hours_flag_reaches_the_bar_reader() -> None:
    """The default excludes 04:00 prints; the opt-out has to actually plumb through."""

    default = IntradayEvaluatorReader(FakePool(), timeframe="15Min")
    opted_in = IntradayEvaluatorReader(FakePool(), timeframe="15Min", include_extended=True)

    assert default._bars._include_extended is False
    assert opted_in._bars._include_extended is True
