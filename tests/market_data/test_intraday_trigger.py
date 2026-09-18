"""The intraday sweep, and the interval that keeps it ahead of the strategies.

``market_data.intraday_bars`` was written once by a hand-run backfill and sat two
sessions stale (newest bar 2026-09-16 16:45 ET, checked 2026-09-18) while the
daily trigger beside it swept on schedule. That is KI-024 at a finer grain, and
at this grain the consequence is worse: a stale daily panel is one wrong
decision a day, while a stale intraday panel is a strategy waking every interval
to re-decide on a panel that has not moved. The Runner's guard does not catch it
— the slot genuinely changed — so nothing raises and nothing repeats in a log.

Every test here is about a way the table can be quietly wrong rather than
missing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from shrap.market_data.intraday_trigger_service import (
    declared_timeframes,
    resolve_interval,
    run_sweep,
    timeframe_minutes,
)


def test_timeframe_minutes_parses_intraday_tokens() -> None:
    assert timeframe_minutes("15Min") == 15
    assert timeframe_minutes("1Min") == 1
    assert timeframe_minutes("390Min") == 390


def test_a_daily_token_is_not_an_intraday_grain() -> None:
    """1Day must not resolve to 1440 minutes.

    A daily grain is the other trigger's business. A number here would let a
    daily strategy set this service's sweep interval, and 1440/2 minutes is a
    twelve-hour sweep on a table that needs a five-minute one.
    """

    assert timeframe_minutes("1Day") is None
    assert timeframe_minutes("") is None
    assert timeframe_minutes("nonsense") is None


# --- the interval ------------------------------------------------------------


def test_the_interval_is_half_the_finest_grain() -> None:
    assert resolve_interval(["15Min"], ceiling_seconds=3600, floor_seconds=60) == 450.0


def test_the_finest_grain_wins_when_several_are_swept() -> None:
    assert resolve_interval(["15Min", "5Min"], ceiling_seconds=3600, floor_seconds=60) == 150.0


def test_the_configured_interval_is_a_ceiling_never_a_floor() -> None:
    """The failure this service exists to prevent must not be configurable.

    A 15-minute strategy against a table refreshed every 30 minutes re-decides
    on an unchanged panel every other slot. An operator setting a slower
    interval than the data changes gets the faster one anyway.
    """

    assert resolve_interval(["5Min"], ceiling_seconds=1800, floor_seconds=60) == 150.0
    # ...and a ceiling tighter than half the grain is still honoured.
    assert resolve_interval(["15Min"], ceiling_seconds=120, floor_seconds=60) == 120.0


def test_the_floor_stops_a_pointless_sweep() -> None:
    """Below one minute there is no new Alpaca bar to fetch."""

    assert resolve_interval(["1Min"], ceiling_seconds=3600, floor_seconds=60) == 60.0


def test_no_intraday_grain_falls_back_to_the_ceiling() -> None:
    assert resolve_interval(["1Day"], ceiling_seconds=300, floor_seconds=60) == 300.0
    assert resolve_interval([], ceiling_seconds=300, floor_seconds=60) == 300.0


# --- which grains get swept --------------------------------------------------


class FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        return self._rows


class FakePool:
    def __init__(self, rows: list[dict[str, Any]] | None = None, raises: bool = False) -> None:
        self._rows = rows or []
        self._raises = raises

    def acquire(self) -> Any:
        pool = self

        class _Ctx:
            async def __aenter__(self) -> FakeConn:
                if pool._raises:
                    raise RuntimeError("relation research.strategies does not exist")
                return FakeConn(pool._rows)

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()


@pytest.mark.asyncio
async def test_a_declared_intraday_cadence_is_swept() -> None:
    pool = FakePool([{"spec": {"cadence": {"kind": "intraday", "interval_minutes": 5}}}])
    assert await declared_timeframes(pool, "15Min") == ("5Min", "15Min")


@pytest.mark.asyncio
async def test_daily_strategies_contribute_no_grain() -> None:
    """The live book today: two paper strategies, neither declaring a cadence."""

    pool = FakePool([{"spec": {"rule": "cross-sectional-momentum"}}, {"spec": {}}])
    assert await declared_timeframes(pool, "15Min") == ("15Min",)


@pytest.mark.asyncio
async def test_a_spec_stored_as_json_text_is_still_read() -> None:
    """asyncpg returns jsonb as str unless a codec is registered."""

    pool = FakePool([{"spec": '{"cadence": {"kind": "intraday", "interval_minutes": 5}}'}])
    assert await declared_timeframes(pool, "15Min") == ("5Min", "15Min")


@pytest.mark.asyncio
async def test_an_unreadable_registry_sweeps_the_baseline_rather_than_nothing() -> None:
    """A market-data service must not fail to start because a Research table moved.

    Sweeping one grain is strictly better than sweeping none, and the fallback
    logs at warning so it cannot be mistaken for "nobody declared one".
    """

    assert await declared_timeframes(FakePool(raises=True), "15Min") == ("15Min",)


# --- the sweep ---------------------------------------------------------------


class FakeStore:
    def __init__(self, last: dict[str, dict[str, datetime]]) -> None:
        self._last = last
        self.upserts: list[Any] = []

    async def last_bar_by_ticker(self, timeframe: str) -> dict[str, datetime]:
        return self._last.get(timeframe, {})

    async def upsert_bars(self, bars: Any) -> int:
        self.upserts.append(bars)
        return len(bars)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    async def get_intraday_bars(
        self,
        http: Any,
        ticker: str,
        start: str,
        end: str,
        *,
        timeframe: str,
        limit: int,
    ) -> list[Any]:
        self.calls.append((ticker, timeframe, start, end))
        return []


@pytest.mark.asyncio
async def test_every_declared_grain_is_swept_not_just_the_first() -> None:
    """A strategy on a grain nobody fetches reads an empty panel and is skipped.

    No error, no order, no signal — the loudest symptom is a strategy that
    simply never trades.
    """

    store = FakeStore({})
    client = FakeClient()
    await run_sweep(
        store,  # type: ignore[arg-type]
        client,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        ["AMD"],
        date(2026, 9, 18),
        ["5Min", "15Min"],
        restate_days=2,
        bootstrap_days=30,
    )
    assert {tf for _, tf, _, _ in client.calls} == {"5Min", "15Min"}


@pytest.mark.asyncio
async def test_the_window_starts_from_the_last_stored_session_not_its_timestamp() -> None:
    """plan_windows reasons in sessions; a bar timestamp is a moment inside one.

    Truncating to the date re-requests the whole of the last stored session,
    which is what corrects the provisional final bar of an in-progress one. Not
    truncating would start the window mid-session and leave that bar wrong.
    """

    store = FakeStore({"15Min": {"AMD": datetime(2026, 9, 16, 20, 45, tzinfo=UTC)}})
    client = FakeClient()
    await run_sweep(
        store,  # type: ignore[arg-type]
        client,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        ["AMD"],
        date(2026, 9, 18),
        ["15Min"],
        restate_days=2,
        bootstrap_days=30,
    )
    # 2026-09-16 minus 2 restate days.
    assert client.calls[0][2] == "2026-09-14"


@pytest.mark.asyncio
async def test_a_grain_the_store_has_never_held_bootstraps() -> None:
    store = FakeStore({"15Min": {"AMD": datetime(2026, 9, 16, 20, 45, tzinfo=UTC)}})
    client = FakeClient()
    await run_sweep(
        store,  # type: ignore[arg-type]
        client,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        ["AMD"],
        date(2026, 9, 18),
        ["5Min"],
        restate_days=2,
        bootstrap_days=30,
    )
    assert client.calls[0][2] == "2026-08-19"  # today - 30


def test_the_freshness_target_exists_and_names_this_producer() -> None:
    """Shipping the sweep without the alarm reproduces KI-024 with extra steps."""

    from shrap.operations.staleness import DEFAULT_TARGETS

    target = next(t for t in DEFAULT_TARGETS if t.name == "market_data.intraday_bars")
    assert target.producer == "market-data-intraday-trigger"
    assert target.timestamp_column == "fetched_at"
    # Tighter than the daily table's 18h, because the consequence is worse.
    daily = next(t for t in DEFAULT_TARGETS if t.name == "market_data.daily_bars")
    assert target.max_age < daily.max_age


def test_compose_and_pyproject_wire_the_service() -> None:
    from pathlib import Path

    compose = Path("infra/docker-compose.yml").read_text()
    assert "shrap_market_data_intraday_trigger" in compose
    assert "MARKET_DATA_INTRADAY_TRIGGER_BASELINE_TIMEFRAME" in compose
    pyproject = Path("pyproject.toml").read_text()
    assert "shrap-market-data-intraday-trigger" in pyproject
