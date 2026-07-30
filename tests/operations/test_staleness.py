"""Unit tests for output-staleness checks (shrap.operations.staleness)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from shrap.operations.staleness import (
    DEFAULT_TARGETS,
    FreshnessReading,
    FreshnessTarget,
    PostgresStalenessStore,
    classify,
    sweep,
)

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)

TARGET = FreshnessTarget(
    name="research.raw_source_items",
    schema="research",
    table="raw_source_items",
    timestamp_column="fetched_at",
    producer="tech-watcher",
    max_age=timedelta(hours=6),
    rationale="test",
)


def _reading(**kwargs: Any) -> FreshnessReading:
    base: dict[str, Any] = {"table_exists": True, "has_rows": True, "last_row_at": NOW}
    base.update(kwargs)
    return FreshnessReading(**base)


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


def test_fresh_table_is_ok() -> None:
    v = classify(TARGET, _reading(last_row_at=NOW - timedelta(hours=1)), NOW)
    assert v.status == "ok"
    assert v.reason == "fresh"
    assert v.age_seconds == pytest.approx(3600.0)


def test_row_older_than_max_age_is_degraded() -> None:
    v = classify(TARGET, _reading(last_row_at=NOW - timedelta(hours=7)), NOW)
    assert (v.status, v.reason) == ("degraded", "stale")
    assert v.age_seconds == pytest.approx(25200.0)


def test_empty_table_is_down_not_degraded() -> None:
    """The News Analyzer case: deployed for days, table empty, service healthy.

    Never-produced is strictly worse than produced-then-stopped, so it gets the
    louder status — that difference is the whole point of the check.
    """

    v = classify(TARGET, _reading(has_rows=False, last_row_at=None), NOW)
    assert (v.status, v.reason) == ("down", "no-rows")
    assert v.age_seconds is None


def test_missing_table_is_degraded() -> None:
    v = classify(TARGET, _reading(table_exists=False, has_rows=False, last_row_at=None), NOW)
    assert (v.status, v.reason) == ("degraded", "table-missing")


def test_rows_without_timestamps_are_not_read_as_fresh() -> None:
    v = classify(TARGET, _reading(last_row_at=None), NOW)
    assert (v.status, v.reason) == ("degraded", "no-timestamp")


def test_query_failure_is_degraded_not_ok() -> None:
    v = classify(TARGET, _reading(error="connection refused"), NOW)
    assert (v.status, v.reason) == ("degraded", "query-failed")
    assert v.evidence()["error"] == "connection refused"


def test_boundary_age_is_still_fresh() -> None:
    """Exactly at max_age is not yet stale — the threshold is an upper bound."""

    v = classify(TARGET, _reading(last_row_at=NOW - timedelta(hours=6)), NOW)
    assert v.status == "ok"


def test_evidence_carries_the_rationale() -> None:
    """An alert that cannot justify its own threshold trains you to ignore it."""

    ev = classify(TARGET, _reading(last_row_at=NOW - timedelta(hours=7)), NOW).evidence()
    assert ev["rationale"] == "test"
    assert ev["max_age_seconds"] == 21600.0
    assert ev["table"] == "research.raw_source_items"
    assert ev["producer"] == "tech-watcher"


# ---------------------------------------------------------------------------
# target registry
# ---------------------------------------------------------------------------


def test_identifiers_are_validated_at_construction() -> None:
    with pytest.raises(ValueError, match="unsafe SQL identifier"):
        FreshnessTarget(
            name="evil",
            schema="research",
            table="items; DROP TABLE research.strategies --",
            timestamp_column="fetched_at",
            producer="nobody",
            max_age=timedelta(hours=1),
            rationale="",
        )


def test_max_age_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_age must be positive"):
        FreshnessTarget(
            name="zero",
            schema="research",
            table="raw_source_items",
            timestamp_column="fetched_at",
            producer="tech-watcher",
            max_age=timedelta(0),
            rationale="",
        )


def test_default_targets_are_distinct_and_explained() -> None:
    names = [t.name for t in DEFAULT_TARGETS]
    assert len(names) == len(set(names))
    for target in DEFAULT_TARGETS:
        assert target.rationale.strip(), f"{target.name} has no threshold rationale"
        assert target.producer.strip()


def _source_text() -> str:
    root = Path(__file__).resolve().parents[2] / "src" / "shrap"
    return "\n".join(p.read_text() for p in root.rglob("*.py"))


def test_every_target_names_a_table_the_firm_actually_creates() -> None:
    """A typo here reads as ``table-missing`` forever and nobody would notice.

    The check is only as good as its table names, and nothing else in the
    system references them — so pin them to the CREATE TABLE statements that
    own them.
    """

    source = _source_text()
    for target in DEFAULT_TARGETS:
        pattern = re.compile(
            rf"CREATE TABLE IF NOT EXISTS {re.escape(target.qualified_table)}\s*\((.*?)\n\)",
            re.DOTALL,
        )
        match = pattern.search(source)
        assert match is not None, f"no CREATE TABLE found for {target.qualified_table}"
        body = match.group(1)
        assert re.search(rf"\b{re.escape(target.timestamp_column)}\b\s+TIMESTAMPTZ", body), (
            f"{target.qualified_table} has no TIMESTAMPTZ column named {target.timestamp_column}"
        )


def test_backtest_corpus_is_deliberately_not_a_target() -> None:
    """market_data.daily_bars is filled on demand and is meant to sit still."""

    assert "market_data.daily_bars" not in {t.qualified_table for t in DEFAULT_TARGETS}


# ---------------------------------------------------------------------------
# sweep
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self, readings: dict[str, FreshnessReading | Exception]) -> None:
        self._readings = readings

    async def read(self, target: FreshnessTarget) -> FreshnessReading:
        value = self._readings[target.name]
        if isinstance(value, Exception):
            raise value
        return value


async def test_sweep_classifies_every_target() -> None:
    store = _FakeStore({t.name: _reading() for t in DEFAULT_TARGETS})
    verdicts = await sweep(store, DEFAULT_TARGETS, NOW)
    assert len(verdicts) == len(DEFAULT_TARGETS)
    assert all(v.status == "ok" for v in verdicts)


async def test_one_failing_target_does_not_abort_the_sweep() -> None:
    readings: dict[str, FreshnessReading | Exception] = {
        t.name: _reading() for t in DEFAULT_TARGETS
    }
    broken = DEFAULT_TARGETS[0].name
    readings[broken] = RuntimeError("connection reset")

    verdicts = await sweep(_FakeStore(readings), DEFAULT_TARGETS, NOW)

    assert len(verdicts) == len(DEFAULT_TARGETS)
    by_name = {v.target.name: v for v in verdicts}
    assert by_name[broken].reason == "query-failed"
    assert all(v.status == "ok" for name, v in by_name.items() if name != broken)


# ---------------------------------------------------------------------------
# Postgres store
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)
        self.queries: list[str] = []

    async def fetchrow(self, sql: str, *args: object) -> Any:
        self.queries.append(sql)
        return self._rows.pop(0)


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        conn = self._conn

        class _Ctx:
            async def __aenter__(self) -> _FakeConn:
                return conn

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()


async def test_store_reports_missing_table_without_querying_it() -> None:
    conn = _FakeConn([{"table_exists": False}])
    reading = await PostgresStalenessStore(_FakePool(conn)).read(TARGET)
    assert reading.table_exists is False
    assert len(conn.queries) == 1, "must not query a table that does not exist"


async def test_store_distinguishes_empty_table_from_null_timestamps() -> None:
    empty = _FakeConn([{"table_exists": True}, {"last_row_at": None, "has_rows": False}])
    reading = await PostgresStalenessStore(_FakePool(empty)).read(TARGET)
    assert (reading.has_rows, reading.last_row_at) == (False, None)

    nulls = _FakeConn([{"table_exists": True}, {"last_row_at": None, "has_rows": True}])
    reading = await PostgresStalenessStore(_FakePool(nulls)).read(TARGET)
    assert (reading.has_rows, reading.last_row_at) == (True, None)


async def test_store_returns_the_newest_timestamp() -> None:
    conn = _FakeConn([{"table_exists": True}, {"last_row_at": NOW, "has_rows": True}])
    reading = await PostgresStalenessStore(_FakePool(conn)).read(TARGET)
    assert reading.last_row_at == NOW
    assert "max(fetched_at)" in conn.queries[1]
    assert "research.raw_source_items" in conn.queries[1]


async def test_store_coerces_a_naive_timestamp_to_utc() -> None:
    """Comparing naive to aware raises; the monitor must not die of it."""

    naive = datetime(2026, 7, 30, 11, 0)
    conn = _FakeConn([{"table_exists": True}, {"last_row_at": naive, "has_rows": True}])
    reading = await PostgresStalenessStore(_FakePool(conn)).read(TARGET)
    assert reading.last_row_at == datetime(2026, 7, 30, 11, 0, tzinfo=UTC)
    assert classify(TARGET, reading, NOW).status == "ok"
