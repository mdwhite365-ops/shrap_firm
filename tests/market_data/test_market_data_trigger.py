"""Tests for the scheduled daily-bars sweep (KI-024).

The logic worth testing is :func:`plan_windows` — it decides what the sweep
asks for, and every failure mode of this card is a wrong window. The loop
itself is a timer around it.

Fakes mirror ``test_market_data_backfill.py``, with one addition: the store now
reads as well as writes, so ``FakeConn`` grows a ``fetch``.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from shrap.market_data.store import (
    SELECT_CLOSES_SINCE_SQL,
    SELECT_LAST_SESSION_BY_TICKER_SQL,
    DailyBarRow,
    PostgresDailyBarStore,
)
from shrap.market_data.trigger_service import (
    WindowPlan,
    plan_windows,
    restated_tickers,
    run_sweep,
)
from shrap.operations.staleness import DEFAULT_TARGETS

TODAY = date(2026, 7, 31)


# --- fakes ---------------------------------------------------------------------


class FakeConn:
    """Answers the last-session read with ``rows`` and the closes read with
    ``closes`` — a list of row lists handed out in order, one per read, so a
    test can make the history change between the before and after reads."""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        closes: list[list[dict[str, Any]]] | None = None,
    ) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.fetched: list[str] = []
        self._rows = rows or []
        self._closes = list(closes or [])

    async def execute(self, sql: str, *args: object) -> object:
        self.executed.append((sql, args))
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        self.fetched.append(sql)
        if sql == SELECT_CLOSES_SINCE_SQL:
            return self._closes.pop(0) if self._closes else []
        return self._rows


class FakeAcquire:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakePool:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        closes: list[list[dict[str, Any]]] | None = None,
    ) -> None:
        self.conn = FakeConn(rows, closes)

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)


class FakeBarsClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def get_daily_bars(
        self,
        http_client: object,
        ticker: str,
        start_day: str,
        end_day: str,
        *,
        limit: int = 10000,
    ) -> list[DailyBarRow]:
        self.calls.append((ticker, start_day, end_day))
        return [
            DailyBarRow(
                ticker=ticker,
                session_date=TODAY,
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=100.0,
                trade_count=10,
                vwap=1.4,
                adjustment="all",
                source="alpaca-iex",
            )
        ]


# --- plan_windows ---------------------------------------------------------------


def test_the_steady_state_collapses_to_one_window() -> None:
    """Every name last stored on the same session is one request shape, not fifty."""

    last = {t: date(2026, 7, 29) for t in ("AAPL", "NVDA", "TSLA")}

    plans = plan_windows(last, ["AAPL", "NVDA", "TSLA"], TODAY, restate_days=5, bootstrap_days=1825)

    assert plans == (
        WindowPlan(start_day="2026-07-24", end_day="2026-07-31", tickers=("AAPL", "NVDA", "TSLA")),
    )


def test_a_ticker_with_no_history_gets_the_bootstrap_window() -> None:
    """A name added to the universe later must not start its series at today."""

    last = {"AAPL": date(2026, 7, 29)}

    plans = plan_windows(last, ["AAPL", "NEWCO"], TODAY, restate_days=5, bootstrap_days=1825)

    by_ticker = {p.tickers: p.start_day for p in plans}
    assert by_ticker[("NEWCO",)] == (TODAY - timedelta(days=1825)).isoformat()
    assert by_ticker[("AAPL",)] == "2026-07-24"


def test_the_restate_buffer_re_requests_settled_days() -> None:
    """adjustment=all restates history, so the newest bars are not final."""

    plans = plan_windows(
        {"AAPL": date(2026, 7, 30)}, ["AAPL"], TODAY, restate_days=5, bootstrap_days=1825
    )

    assert plans[0].start_day == "2026-07-25"
    assert plans[0].end_day == "2026-07-31"


def test_a_future_last_session_cannot_invert_the_window() -> None:
    """Clock skew or a bad row must not produce start > end."""

    plans = plan_windows(
        {"AAPL": TODAY + timedelta(days=30)}, ["AAPL"], TODAY, restate_days=0, bootstrap_days=1825
    )

    assert plans[0].start_day == plans[0].end_day == TODAY.isoformat()


def test_no_tickers_is_no_work() -> None:
    assert plan_windows({}, [], TODAY, restate_days=5, bootstrap_days=1825) == ()


def test_windows_are_ordered_by_start_day() -> None:
    last = {"OLD": date(2026, 1, 5), "NEW": date(2026, 7, 30)}

    plans = plan_windows(last, ["NEW", "OLD"], TODAY, restate_days=0, bootstrap_days=1825)

    assert [p.start_day for p in plans] == ["2026-01-05", "2026-07-30"]


# --- run_sweep ------------------------------------------------------------------


async def test_the_sweep_asks_only_for_the_gap_and_aggregates_the_pass() -> None:
    pool = FakePool([{"ticker": "AAPL", "last_session": date(2026, 7, 29)}])
    store = PostgresDailyBarStore(pool)
    client = FakeBarsClient()

    summary = await run_sweep(
        store,
        client,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        ["AAPL", "NEWCO"],
        TODAY,
        restate_days=5,
        bootstrap_days=10,
        inter_ticker_delay_seconds=0.0,
    )

    # The watermark, then the window's stored closes. Nothing is stored in the
    # window here, so there is nothing to compare and no second read.
    assert pool.conn.fetched == [SELECT_LAST_SESSION_BY_TICKER_SQL, SELECT_CLOSES_SINCE_SQL]
    # Two windows: AAPL's incremental gap, NEWCO's bootstrap.
    assert sorted(client.calls) == [
        ("AAPL", "2026-07-24", "2026-07-31"),
        ("NEWCO", "2026-07-21", "2026-07-31"),
    ]
    # The summary covers the whole pass, not just its last window.
    assert summary.tickers == 2
    assert summary.rows_fetched == 2
    assert summary.rows_upserted == 2


async def test_a_dry_run_fetches_and_writes_nothing() -> None:
    pool = FakePool([{"ticker": "AAPL", "last_session": date(2026, 7, 29)}])
    store = PostgresDailyBarStore(pool)
    client = FakeBarsClient()

    summary = await run_sweep(
        store,
        client,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        ["AAPL"],
        TODAY,
        restate_days=5,
        bootstrap_days=10,
        dry_run=True,
        inter_ticker_delay_seconds=0.0,
    )

    assert client.calls  # it did fetch
    assert summary.rows_upserted == 0
    assert pool.conn.executed == []


async def test_last_session_by_ticker_reads_the_grouped_maximum() -> None:
    pool = FakePool(
        [
            {"ticker": "AAPL", "last_session": date(2026, 7, 29)},
            {"ticker": "NVDA", "last_session": date(2026, 7, 30)},
        ]
    )

    assert await PostgresDailyBarStore(pool).last_session_by_ticker() == {
        "AAPL": date(2026, 7, 29),
        "NVDA": date(2026, 7, 30),
    }


# --- a restated history ----------------------------------------------------------


def _key(day: int, ticker: str = "ETHA") -> tuple[str, date, str]:
    return (ticker, date(2026, 9, day), "all")


def test_a_split_shows_as_a_settled_close_that_moved() -> None:
    """ETHA, 2026-09-30: stored at the old basis, restated at three times it."""

    before = {_key(29): 20.275, _key(30): 20.12}
    after = {_key(29): 60.83, _key(30): 60.36}

    assert restated_tickers(before, after, {"ETHA": date(2026, 9, 30)}) == ("ETHA",)


def test_the_newest_day_may_change_without_meaning_anything() -> None:
    """A partial mid-session bar is finalised later; that is not a restatement."""

    before = {_key(29): 20.0, _key(30): 20.0}
    after = {_key(29): 20.0, _key(30): 21.0}

    assert restated_tickers(before, after, {"ETHA": date(2026, 9, 30)}) == ()


def test_a_dividend_sized_restatement_is_caught_and_float_noise_is_not() -> None:
    last = {"ETHA": date(2026, 9, 30)}

    assert restated_tickers({_key(29): 100.0}, {_key(29): 99.6}, last) == ("ETHA",)
    assert restated_tickers({_key(29): 100.0}, {_key(29): 100.00001}, last) == ()


async def test_a_restated_ticker_has_its_whole_history_refetched() -> None:
    settled = {"ticker": "ETHA", "session_date": date(2026, 7, 28), "adjustment": "all"}
    pool = FakePool(
        [{"ticker": "ETHA", "last_session": date(2026, 7, 29)}],
        closes=[[{**settled, "close": 20.0}], [{**settled, "close": 60.0}]],
    )
    client = FakeBarsClient()

    await run_sweep(
        PostgresDailyBarStore(pool),
        client,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        ["ETHA"],
        TODAY,
        restate_days=5,
        bootstrap_days=1825,
        inter_ticker_delay_seconds=0.0,
    )

    assert client.calls == [
        ("ETHA", "2026-07-24", "2026-07-31"),  # the normal restate window
        ("ETHA", (TODAY - timedelta(days=1825)).isoformat(), "2026-07-31"),  # the repair
    ]


async def test_an_unchanged_history_costs_no_extra_request() -> None:
    settled = {"ticker": "ETHA", "session_date": date(2026, 7, 28), "adjustment": "all"}
    pool = FakePool(
        [{"ticker": "ETHA", "last_session": date(2026, 7, 29)}],
        closes=[[{**settled, "close": 20.0}], [{**settled, "close": 20.0}]],
    )
    client = FakeBarsClient()

    await run_sweep(
        PostgresDailyBarStore(pool),
        client,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        ["ETHA"],
        TODAY,
        restate_days=5,
        bootstrap_days=1825,
        inter_ticker_delay_seconds=0.0,
    )

    assert len(client.calls) == 1


# --- the alarm ------------------------------------------------------------------


def test_daily_bars_has_a_freshness_target_naming_this_producer() -> None:
    """A trigger without an alarm reproduces KI-024 with extra steps."""

    target = next(t for t in DEFAULT_TARGETS if t.name == "market_data.daily_bars")

    assert target.producer == "market-data-trigger"
    assert target.timestamp_column == "fetched_at"
    # Must clear more than one missed sweep so a single blip is not an alert,
    # and must be well under a session so a real stall is caught same-day.
    assert timedelta(hours=12) <= target.max_age <= timedelta(hours=24)
