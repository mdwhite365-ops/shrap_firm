"""The bar tables can hold two feeds, and no reader may forget to pick one.

Changing six queries and several signatures broke exactly **one** test in a
suite of 2,013. That is the finding this file exists to answer: the ``source``
predicate was almost entirely uncovered, so nothing would have caught a reader
that omitted it — and a reader that omits it does not fail, it silently returns
two bars per session and backtests on the average of two markets.

The static sweep below is the important test here. The others pin what this
card changed; the sweep is what catches the *next* query someone writes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from shrap.market_data.store import (
    CREATE_DAILY_BARS_TABLE_SQL,
    CREATE_INTRADAY_BARS_TABLE_SQL,
    DEFAULT_BAR_SOURCE,
    MIGRATE_DAILY_BARS_KEY_SQL,
    MIGRATE_INTRADAY_BARS_KEY_SQL,
    UPSERT_DAILY_BAR_SQL,
    UPSERT_INTRADAY_BAR_SQL,
)

SRC = Path("src/shrap")

# Matched on the FROM clause rather than on the table name appearing anywhere,
# so that `SELECT create_hypertable('market_data.intraday_bars', ...)` — which
# is SELECT-shaped DDL naming the table as a string, not a read of its rows —
# does not register as a query that forgot its feed.
READS_BAR_TABLE = re.compile(r"FROM\s+market_data\.(daily|intraday)_bars", re.IGNORECASE)

# A SQL literal in the source: triple-quoted, which is how every query in this
# repo is written.
SQL_LITERAL = re.compile(r'"""(.*?)"""', re.DOTALL)


def _sql_literals() -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    for path in SRC.rglob("*.py"):
        for match in SQL_LITERAL.finditer(path.read_text()):
            found.append((path, match.group(1)))
    return found


def _selects_from_bar_tables() -> list[tuple[Path, str]]:
    """Every SELECT in the repo that reads a bar table."""

    out: list[tuple[Path, str]] = []
    for path, sql in _sql_literals():
        if "SELECT" not in sql.upper():
            continue
        if not READS_BAR_TABLE.search(sql):
            continue
        out.append((path, sql))
    return out


def test_the_sweep_actually_finds_queries() -> None:
    """Guard the guard: a regex that matches nothing would pass every test."""

    found = _selects_from_bar_tables()
    assert len(found) >= 6, f"expected the known bar readers, found {len(found)}"


def test_every_bar_query_names_its_source() -> None:
    """The one that catches the next reader, not just this card's six.

    A query without a ``source`` predicate is not wrong today only because the
    store happens to hold one feed. That is the "correct by accident" state this
    whole card is about, and it stops being correct the moment a SIP backfill
    runs.
    """

    offenders = [
        f"{path}: {' '.join(sql.split())[:110]}"
        for path, sql in _selects_from_bar_tables()
        if "source" not in sql
    ]
    assert not offenders, "bar queries with no source predicate:\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    ("ddl", "expected"),
    [
        (CREATE_DAILY_BARS_TABLE_SQL, "PRIMARY KEY (ticker, session_date, adjustment, source)"),
        (
            CREATE_INTRADAY_BARS_TABLE_SQL,
            "PRIMARY KEY (ticker, bar_ts, timeframe, adjustment, source)",
        ),
    ],
)
def test_source_is_in_the_primary_key(ddl: str, expected: str) -> None:
    """What the module docstring claimed before this card, now true."""

    assert expected in ddl


@pytest.mark.parametrize(
    ("upsert", "target"),
    [
        (UPSERT_DAILY_BAR_SQL, "(ticker, session_date, adjustment, source)"),
        (UPSERT_INTRADAY_BAR_SQL, "(ticker, bar_ts, timeframe, adjustment, source)"),
    ],
)
def test_upsert_conflict_target_matches_the_key(upsert: str, target: str) -> None:
    assert f"ON CONFLICT {target} DO UPDATE" in upsert


@pytest.mark.parametrize("upsert", [UPSERT_DAILY_BAR_SQL, UPSERT_INTRADAY_BAR_SQL])
def test_upsert_never_reassigns_source(upsert: str) -> None:
    """A key column cannot change in place; saying so would mislead a reader."""

    assert "source = EXCLUDED.source" not in upsert


@pytest.mark.parametrize(
    ("migration", "constraint"),
    [
        (MIGRATE_DAILY_BARS_KEY_SQL, "daily_bars_pkey"),
        (MIGRATE_INTRADAY_BARS_KEY_SQL, "intraday_bars_pkey"),
    ],
)
def test_migration_is_guarded_and_rerunnable(migration: str, constraint: str) -> None:
    """Runs on every startup, so a second run must be a no-op.

    Guarded on the *arity* of the existing key rather than on its presence: the
    constraint exists either way, and only its column count says whether this
    box has been migrated.
    """

    assert f"conname = '{constraint}'" in migration
    assert "array_length(conkey, 1)" in migration
    assert "IF EXISTS" in migration


def test_default_source_is_what_is_already_on_disk() -> None:
    """74k daily and 1.8M intraday rows carry this exact string.

    If this constant ever drifts from the stored value, every reader defaults
    to a feed the store does not have and every panel comes back empty.
    """

    assert DEFAULT_BAR_SOURCE == "alpaca-iex"
