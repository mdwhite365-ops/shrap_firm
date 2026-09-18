"""The Runner reads the grain its strategy declared, not the grain the firm has.

Card 2 of the intraday path. Card 1 built the reader, the cadence arithmetic and
the window; the Runner then handed every strategy the *daily* reader regardless,
because it held one reader and had no reason to hold two.

**Every test here guards a failure that does not raise.** An intraday strategy
handed daily bars gets a well-formed panel, a real ranking and a filled order —
26 times a session, off a signal that only moves once a day. Nothing in the
event trail would look wrong. So these assert on *which reader was consulted*,
which is the only place the difference is observable before the money moves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from shrap.agents.research.strategy_runner.runner import _build_input
from shrap.research.strategy_evaluator.store import (
    PostgresEvaluatorReader,
    PostgresIntradayBarReader,
)
from shrap.research.strategy_evaluator.strategy import BarSample
from shrap.research.strategy_runner.cadence import DAILY, Cadence, read_cadence
from shrap.research.strategy_runner.readers import (
    CadenceBarReaders,
    SingleGrainReaders,
)

INTRADAY_15 = Cadence(kind="intraday", interval_minutes=15)
INTRADAY_5 = Cadence(kind="intraday", interval_minutes=5)


class FakePool:
    """Stands in for an asyncpg pool. Never acquired: no test here reads bars."""


def test_daily_cadence_gets_the_daily_reader() -> None:
    readers = CadenceBarReaders(FakePool())
    assert isinstance(readers.for_cadence(DAILY), PostgresEvaluatorReader)


def test_intraday_cadence_gets_an_intraday_reader_at_its_own_grain() -> None:
    readers = CadenceBarReaders(FakePool())
    reader = readers.for_cadence(INTRADAY_15)
    assert isinstance(reader, PostgresIntradayBarReader)
    # The token must match what the backfill stored in
    # market_data.intraday_bars.timeframe, or the read returns zero rows.
    assert reader._timeframe == "15Min"


def test_each_grain_gets_its_own_reader() -> None:
    readers = CadenceBarReaders(FakePool())
    assert readers.for_cadence(INTRADAY_5) is not readers.for_cadence(INTRADAY_15)


def test_readers_are_memoised_per_grain() -> None:
    """The session-bounds cache lives in the reader, so the reader must persist.

    PostgresIntradayBarReader caches the exchange calendar for one window on the
    assumption that the Runner reads every ticker over the same window. A reader
    rebuilt per call would re-derive the calendar once per ticker — slower every
    pass, and never an error.
    """

    readers = CadenceBarReaders(FakePool())
    assert readers.for_cadence(INTRADAY_15) is readers.for_cadence(INTRADAY_15)
    assert readers.for_cadence(DAILY) is readers.for_cadence(DAILY)


def test_extended_hours_is_off_unless_asked() -> None:
    default = CadenceBarReaders(FakePool()).for_cadence(INTRADAY_15)
    opted_in = CadenceBarReaders(FakePool(), include_extended=True).for_cadence(INTRADAY_15)
    assert default._include_extended is False
    assert opted_in._include_extended is True


@pytest.mark.parametrize(
    "spec",
    [
        {},
        {"cadence": "intrday"},  # the typo read_cadence deliberately swallows
        {"cadence": {"kind": "intraday", "interval_minutes": 0}},
        {"cadence": {"kind": "intraday", "interval_minutes": 9999}},
        {"cadence": 17},
    ],
)
def test_a_spec_that_does_not_declare_intraday_reads_daily_bars(spec: dict[str, Any]) -> None:
    """The reader agrees with the slot and the window on what a malformed spec is.

    read_cadence resolves all of these to DAILY, so the strategy acts once a
    session and reads a daily-sized window. The reader has to land on the same
    answer from the same read, or a typo would trade once a day off a
    15-minute panel.
    """

    readers = CadenceBarReaders(FakePool())
    assert isinstance(readers.for_cadence(read_cadence(spec)), PostgresEvaluatorReader)


def test_single_grain_resolver_ignores_cadence() -> None:
    sentinel = object()
    readers = SingleGrainReaders(sentinel)  # type: ignore[arg-type]
    assert readers.for_cadence(DAILY) is sentinel
    assert readers.for_cadence(INTRADAY_5) is sentinel


# --------------------------------------------------------------------------
# The integration guard: _build_input must consult the resolved reader.
# --------------------------------------------------------------------------


@dataclass
class RecordingReader:
    """Notes that it was asked, and for what window."""

    label: str
    calls: list[tuple[str, date, date]]

    async def read_bars(
        self, ticker: str, start: date, end: date, adjustment: str
    ) -> list[BarSample]:
        self.calls.append((ticker, start, end))
        return []


class SplitReaders:
    """A resolver whose two readers are distinguishable by which one was called."""

    def __init__(self) -> None:
        self.daily = RecordingReader("daily", [])
        self.intraday = RecordingReader("intraday", [])

    def for_cadence(self, cadence: Cadence) -> RecordingReader:
        return self.intraday if cadence.is_intraday else self.daily


@dataclass
class FakeRecord:
    strategy_id: str
    tickers: list[str]
    spec: dict[str, Any]
    account_id: str = "PA_TEST"
    name: str = "test"
    status: str = "paper"


async def _build(spec: dict[str, Any]) -> SplitReaders:
    readers = SplitReaders()
    await _build_input(
        FakeRecord("s1", ["AAPL", "MSFT"], spec),  # type: ignore[arg-type]
        readers,  # type: ignore[arg-type]
        date(2026, 9, 18),
        adjustment="all",
        buffer_days=10,
        max_days=1200,
    )
    return readers


@pytest.mark.asyncio
async def test_build_input_reads_daily_bars_for_a_daily_strategy() -> None:
    readers = await _build({"rule": "cross-sectional-momentum", "lookback": 126, "top_n": 10})
    assert [c[0] for c in readers.daily.calls] == ["AAPL", "MSFT"]
    assert readers.intraday.calls == []


@pytest.mark.asyncio
async def test_build_input_reads_intraday_bars_for_an_intraday_strategy() -> None:
    """This is the regression. Before this card it asserted the opposite."""

    readers = await _build(
        {
            "rule": "cross-sectional-momentum",
            "lookback": 126,
            "top_n": 10,
            "cadence": {"kind": "intraday", "interval_minutes": 15},
        }
    )
    assert [c[0] for c in readers.intraday.calls] == ["AAPL", "MSFT"]
    assert readers.daily.calls == []


@pytest.mark.asyncio
async def test_intraday_window_is_shorter_than_the_daily_one_for_the_same_warmup() -> None:
    """Warmup is counted in bars; 126 of them is half a year daily, days intraday.

    Asserted through _build_input rather than _lookback_start alone, because the
    cadence that sizes the window and the cadence that picks the reader are the
    same read — and this is the test that fails if they ever stop being.
    """

    base = {"rule": "cross-sectional-momentum", "lookback": 126, "top_n": 10}
    daily = (await _build(dict(base))).daily.calls[0]
    intraday = await _build({**base, "cadence": {"kind": "intraday", "interval_minutes": 15}})
    intraday_call = intraday.intraday.calls[0]
    daily_span = (daily[2] - daily[1]).days
    intraday_span = (intraday_call[2] - intraday_call[1]).days
    assert intraday_span < daily_span
