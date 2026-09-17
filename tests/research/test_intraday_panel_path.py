"""The intraday panel path: cadence arithmetic, the bar reader, and the window.

Card 1 of making validation work on a human timescale. The arithmetic behind the
card is that ``t = IR_annualized x sqrt(years elapsed)``, so neither position
size nor sampling the same strategy more often moves significance — both cancel.
Only breadth does, via ``IR = IC x sqrt(breadth)``. This path is the apparatus
for finding out whether per-decision skill survives a shorter horizon. It does
not assume that it does.

These tests concentrate on the two ways the path fails **silently**, because
neither raises:

1. Extended-hours bars reaching a panel, where a handful of shares at 04:00 sets
   a price no strategy could have traded at size.
2. A warmup counted in *bars* read as a warmup counted in *sessions*, which at a
   five-minute grain over-reads by 78x — slow, then slower, never an error.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from shrap.agents.research.strategy_runner.runner import _lookback_start
from shrap.operations.market_phase import is_regular_hours, regular_session_bounds
from shrap.research.strategy_evaluator.store import (
    SELECT_INTRADAY_BARS_SQL,
    PostgresIntradayBarReader,
)
from shrap.research.strategy_evaluator.strategy import BarSample, PricePanel
from shrap.research.strategy_runner.cadence import (
    CADENCE_INTRADAY,
    DAILY,
    REGULAR_SESSION_MINUTES,
    Cadence,
    alpaca_timeframe,
    bars_per_session,
    sessions_for_warmup,
)

ET = ZoneInfo("America/New_York")

# A Tuesday in mid-September 2026, comfortably clear of Labor Day (Sept 7) and
# of any half-day. Regular session 09:30-16:00 ET.
SESSION = date(2026, 9, 15)


def _et(hour: int, minute: int, day: date = SESSION) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


# --- cadence arithmetic -------------------------------------------------------


def test_bars_per_session_counts_whole_bars() -> None:
    assert bars_per_session(DAILY) == 1
    assert bars_per_session(Cadence(CADENCE_INTRADAY, 5)) == 78
    assert bars_per_session(Cadence(CADENCE_INTRADAY, 15)) == 26
    assert bars_per_session(Cadence(CADENCE_INTRADAY, REGULAR_SESSION_MINUTES)) == 1


def test_bars_per_session_floors_rather_than_rounds_up() -> None:
    """A cadence that does not divide the session reports WHOLE bars.

    Seven minutes gives 55 complete bars and a 5-minute stub. Counting the stub
    would let a warmup window come up one bar short on its first session, which
    surfaces as a strategy that silently declines to trade rather than as an
    error.
    """

    assert bars_per_session(Cadence(CADENCE_INTRADAY, 7)) == REGULAR_SESSION_MINUTES // 7


def test_sessions_for_warmup_converts_bars_to_sessions() -> None:
    """The conversion whose absence was the expensive bug.

    A 200-bar warmup is 200 sessions at a daily grain and under three at five
    minutes. Reading the former for the latter pulls roughly a calendar year of
    intraday rows per ticker to compute a signal that needed three days.
    """

    assert sessions_for_warmup(DAILY, 200) == 200
    assert sessions_for_warmup(Cadence(CADENCE_INTRADAY, 5), 200) == 3
    assert sessions_for_warmup(Cadence(CADENCE_INTRADAY, 15), 200) == 8


def test_sessions_for_warmup_rounds_up_to_cover_the_warmup() -> None:
    """Ceil, not floor: a partial session still has to be read."""

    per_session = bars_per_session(Cadence(CADENCE_INTRADAY, 5))
    assert sessions_for_warmup(Cadence(CADENCE_INTRADAY, 5), per_session) == 1
    assert sessions_for_warmup(Cadence(CADENCE_INTRADAY, 5), per_session + 1) == 2
    # A zero or negative warmup still reads a session rather than nothing.
    assert sessions_for_warmup(Cadence(CADENCE_INTRADAY, 5), 0) == 1


def test_alpaca_timeframe_matches_the_stored_token() -> None:
    assert alpaca_timeframe(DAILY) == "1Day"
    assert alpaca_timeframe(Cadence(CADENCE_INTRADAY, 5)) == "5Min"
    assert alpaca_timeframe(Cadence(CADENCE_INTRADAY, 15)) == "15Min"


# --- regular-hours placement --------------------------------------------------


def test_regular_session_bounds_omits_non_sessions() -> None:
    """Absent, not empty — so a missing key is a complete 'not a session' test."""

    bounds = regular_session_bounds(date(2026, 9, 12), date(2026, 9, 15))

    assert date(2026, 9, 12) not in bounds  # Saturday
    assert date(2026, 9, 13) not in bounds  # Sunday
    assert date(2026, 9, 14) in bounds  # Monday
    assert SESSION in bounds  # Tuesday

    opened, closed = bounds[SESSION]
    assert opened == datetime(2026, 9, 15, 13, 30, tzinfo=UTC)  # 09:30 ET
    assert closed == datetime(2026, 9, 15, 20, 0, tzinfo=UTC)  # 16:00 ET


def test_is_regular_hours_excludes_pre_and_post_market() -> None:
    bounds = regular_session_bounds(SESSION, SESSION)

    assert not is_regular_hours(_et(4, 0), bounds)  # pre-market open
    assert not is_regular_hours(_et(9, 29), bounds)
    assert is_regular_hours(_et(9, 30), bounds)  # the opening bar counts
    assert is_regular_hours(_et(12, 0), bounds)
    assert is_regular_hours(_et(15, 59), bounds)
    assert not is_regular_hours(_et(16, 0), bounds)  # half-open: close excluded
    assert not is_regular_hours(_et(19, 0), bounds)


def test_early_close_is_why_this_reads_the_calendar() -> None:
    """A fixed 16:00 cutoff would admit three hours of post-market prints.

    The Friday after Thanksgiving closes at 13:00 ET and Alpaca keeps returning
    bars until 17:00. Those bars are well-formed; they are simply not from the
    session a strategy thinks it is trading. Roughly half a dozen days a year,
    and silent on every one of them.

    This also pins DST: the same 09:30 ET open is 13:30 UTC in September and
    14:30 UTC in November, and neither is hardcoded.
    """

    half_day = date(2026, 11, 27)
    bounds = regular_session_bounds(half_day, half_day)

    opened, closed = bounds[half_day]
    assert opened == datetime(2026, 11, 27, 14, 30, tzinfo=UTC)  # 09:30 EST
    assert closed == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)  # 13:00 EST

    assert is_regular_hours(_et(12, 59, half_day), bounds)
    assert not is_regular_hours(_et(13, 0, half_day), bounds)
    # What a hardcoded 16:00 would have let through:
    assert not is_regular_hours(_et(15, 30, half_day), bounds)


def test_thanksgiving_itself_is_not_a_session() -> None:
    bounds = regular_session_bounds(date(2026, 11, 26), date(2026, 11, 26))
    assert bounds == {}


def test_is_regular_hours_rejects_naive_timestamps() -> None:
    """A naive timestamp cannot be placed in a session and must not be guessed.

    Silently assuming UTC would put a 09:30 ET bar four hours before the open
    and drop the whole session.
    """

    bounds = regular_session_bounds(SESSION, SESSION)
    with pytest.raises(ValueError, match="timezone-aware"):
        is_regular_hours(datetime(2026, 9, 15, 14, 0), bounds)


# --- the reader ---------------------------------------------------------------


class FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.fetched: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        self.fetched.append((sql, args))
        return self.rows


class FakeAcquire:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakePool:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.conn = FakeConn(rows)

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)


def _row(moment: datetime, close: float) -> dict[str, Any]:
    return {
        "bar_ts": moment.astimezone(UTC),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000.0,
    }


async def test_reader_excludes_extended_hours_by_default() -> None:
    """The trap the backfill's own docstring warns about.

    Alpaca returns 04:00-20:00 and the backfill stores all of it. A consumer that
    forgets is trading four-in-the-morning IEX prints.
    """

    pool = FakePool(
        [
            _row(_et(4, 30), 100.0),  # pre-market
            _row(_et(9, 30), 101.0),
            _row(_et(12, 0), 102.0),
            _row(_et(15, 55), 103.0),
            _row(_et(18, 0), 104.0),  # after-hours
        ]
    )
    reader = PostgresIntradayBarReader(pool, timeframe="5Min")

    bars = await reader.read_bars("AAPL", SESSION, SESSION, "raw")

    assert [bar.close for bar in bars] == [101.0, 102.0, 103.0]


async def test_reader_includes_extended_hours_on_request() -> None:
    """The data is legitimately there for whoever asks; it is never the default."""

    pool = FakePool([_row(_et(4, 30), 100.0), _row(_et(12, 0), 102.0)])
    reader = PostgresIntradayBarReader(pool, timeframe="5Min", include_extended=True)

    bars = await reader.read_bars("AAPL", SESSION, SESSION, "raw")

    assert [bar.close for bar in bars] == [100.0, 102.0]


async def test_reader_filters_on_timeframe_and_uses_a_half_open_window() -> None:
    """A closed upper bound would double-count the bar on a chunk seam."""

    pool = FakePool([])
    reader = PostgresIntradayBarReader(pool, timeframe="15Min")

    await reader.read_bars("NVDA", SESSION, SESSION, "split")

    sql, args = pool.conn.fetched[0]
    assert sql == SELECT_INTRADAY_BARS_SQL
    assert args[0] == "NVDA"
    assert args[1] == "split"
    assert args[2] == "15Min"
    # Exchange-local midnight to midnight-after-end, so the window is a whole
    # number of sessions however the caller derived its dates.
    assert args[3] == datetime(2026, 9, 15, 0, 0, tzinfo=ET)
    assert args[4] == datetime(2026, 9, 16, 0, 0, tzinfo=ET)


async def test_reader_reads_the_calendar_once_per_window() -> None:
    """The Runner reads every ticker over one window; the calendar is not cheap."""

    pool = FakePool([_row(_et(12, 0), 102.0)])
    reader = PostgresIntradayBarReader(pool, timeframe="5Min")

    await reader.read_bars("AAPL", SESSION, SESSION, "raw")
    first = reader._bounds
    await reader.read_bars("NVDA", SESSION, SESSION, "raw")

    assert reader._bounds is first  # same object: not recomputed


# --- the read window ----------------------------------------------------------


def test_lookback_window_is_sessions_not_bars() -> None:
    """The over-read that would not have raised.

    A 200-bar warmup at five minutes needs under three sessions. Read as 200
    *sessions* it spans more than a calendar year, and a year of 5-minute bars
    for fifty names is roughly 24.6 million rows — fetched every pass, to
    compute a signal that needed three days of them.

    Nothing about that is an error. It is slow, and it gets slower as the
    universe grows, which is the shape that hides for months.
    """

    daily_start = _lookback_start(SESSION, 200, buffer_days=10, max_days=2000)
    intraday_start = _lookback_start(
        SESSION, 200, buffer_days=10, max_days=2000, cadence=Cadence(CADENCE_INTRADAY, 5)
    )

    assert (SESSION - daily_start).days == 410  # 200 * 2 + 10, unchanged
    assert (SESSION - intraday_start).days == 16  # 3 * 2 + 10


def test_lookback_window_unchanged_for_every_strategy_running_today() -> None:
    """Default cadence is DAILY, so nothing in the registry reads differently."""

    for warmup in (1, 20, 126, 252):
        assert _lookback_start(SESSION, warmup, 10, 2000) == _lookback_start(
            SESSION, warmup, 10, 2000, cadence=DAILY
        )


def test_lookback_window_still_respects_its_ceiling() -> None:
    assert _lookback_start(SESSION, 5000, buffer_days=10, max_days=730) == SESSION - timedelta(
        days=730
    )


# --- the panel ----------------------------------------------------------------


def test_panel_aligns_datetime_bars_exactly_as_it_aligns_dates() -> None:
    """``datetime`` is a subclass of ``date``, so no type migration is needed.

    ``PricePanel`` only ever uses ``session_date`` as a sortable, hashable key.
    This proves the subclass relationship carries through alignment rather than
    asserting it from the language spec.
    """

    intraday = {
        "AAPL": [BarSample(_et(9, 30), 1.0, 1.0, 1.0, 100.0, 10.0)],
        "NVDA": [BarSample(_et(9, 30), 1.0, 1.0, 1.0, 200.0, 20.0)],
    }
    daily = {
        "AAPL": [BarSample(SESSION, 1.0, 1.0, 1.0, 100.0, 10.0)],
        "NVDA": [BarSample(SESSION, 1.0, 1.0, 1.0, 200.0, 20.0)],
    }

    intraday_panel = PricePanel.from_bars(intraday)
    daily_panel = PricePanel.from_bars(daily)

    assert len(intraday_panel.dates) == len(daily_panel.dates) == 1
    assert intraday_panel.tickers == daily_panel.tickers


def test_panel_spans_many_bars_within_one_session() -> None:
    """The point of the whole card: more decision points per calendar day."""

    bars = [
        BarSample(_et(9, 30) + timedelta(minutes=5 * i), 1.0, 1.0, 1.0, 100.0 + i, 10.0)
        for i in range(78)
    ]
    panel = PricePanel.from_bars({"AAPL": bars})

    assert len(panel.dates) == 78
    assert panel.dates == tuple(sorted(panel.dates))
