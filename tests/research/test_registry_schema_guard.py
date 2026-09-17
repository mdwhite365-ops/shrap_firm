"""A reader that cannot read should refuse to start.

#215 added `regime_fit`/`regime_kill` to every SELECT on `research.strategies`.
The migration adding them runs only inside `PostgresStrategyRegistry.ensure_schema`,
which the Strategy Runner never calls — deliberately, since the Runner owns none
of that table and the repo's convention is "reader, never owner".

The result found on the Dell:

    asyncpg.exceptions.UndefinedColumnError: column "regime_fit" does not exist

The Runner escaped only because its container still ran pre-#215 code. On the
next rebuild it would have failed to load any strategy, once per pass, logging
and continuing — and a firm that has silently stopped trading looks exactly like
a firm with no signals. That is the same shape as the momentum-account bug and
the five trading-path fixes: the failure is invisible at the point it happens.

Cards #2 and #3 both add registry columns, so this would otherwise recur twice.
"""

from __future__ import annotations

from typing import Any

import pytest

from shrap.research.strategy_registry import (
    SELECT_STRATEGIES_BY_STATUS_SQL,
    PostgresStrategyRegistry,
    RegistrySchemaError,
    _selected_columns,
)

# Every column the Runner's read path names, as of this build.
EXPECTED = _selected_columns(SELECT_STRATEGIES_BY_STATUS_SQL)


class FakeConn:
    def __init__(self, columns: list[str]) -> None:
        self.columns = columns

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        return [{"column_name": name} for name in self.columns]


class FakeAcquire:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakePool:
    def __init__(self, columns: list[str]) -> None:
        self.conn = FakeConn(columns)

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)


# --- the column parser --------------------------------------------------------


def test_it_reads_the_real_statement_not_a_fixture() -> None:
    """Derived from the query itself, so adding a column cannot forget to add it here."""

    assert "regime_fit" in EXPECTED
    assert "regime_kill" in EXPECTED
    assert "strategy_id" in EXPECTED
    assert "account_id" in EXPECTED
    # Keywords and the table name must not be mistaken for columns.
    assert "SELECT" not in EXPECTED
    assert "FROM" not in EXPECTED
    assert not any("research.strategies" in name for name in EXPECTED)


def test_it_does_not_invent_columns_from_the_where_clause() -> None:
    """Only the SELECT list, which is what a missing-column error comes from."""

    assert not any("status = $1" in name for name in EXPECTED)


# --- the guard ----------------------------------------------------------------


async def test_a_migrated_database_starts() -> None:
    registry = PostgresStrategyRegistry(FakePool(sorted(EXPECTED)))

    assert await registry.missing_columns() == set()
    await registry.verify_schema()


async def test_the_exact_dell_failure_is_caught_at_startup() -> None:
    """A pre-#215 database against a post-#215 build."""

    pre_215 = sorted(EXPECTED - {"regime_fit", "regime_kill"})
    registry = PostgresStrategyRegistry(FakePool(pre_215))

    assert await registry.missing_columns() == {"regime_fit", "regime_kill"}
    with pytest.raises(RegistrySchemaError) as caught:
        await registry.verify_schema()

    message = str(caught.value)
    assert "regime_fit" in message
    assert "regime_kill" in message


async def test_the_error_names_the_command_that_fixes_it() -> None:
    """An error a tired operator can act on without reading the source.

    The failure happens at 4am on a box where the fix is one command, and the
    command is not guessable — it is a CLI whose name has nothing to do with
    migrations, chosen because it calls ensure_schema before anything else.
    """

    registry = PostgresStrategyRegistry(FakePool(sorted(EXPECTED - {"regime_fit"})))

    with pytest.raises(RegistrySchemaError, match="shrap-strategy-stage"):
        await registry.verify_schema()


async def test_a_missing_table_is_not_reported_as_fifty_missing_columns() -> None:
    """Different fault, different fix — and it must not bury the real one."""

    registry = PostgresStrategyRegistry(FakePool([]))

    assert await registry.missing_columns() == set()
    await registry.verify_schema()


async def test_extra_columns_in_the_database_are_fine() -> None:
    """A database ahead of the build is not this check's problem.

    A column the queries do not select cannot produce an UndefinedColumnError,
    and refusing to start over one would block every rollback.
    """

    ahead = sorted(EXPECTED | {"some_future_column"})
    registry = PostgresStrategyRegistry(FakePool(ahead))

    await registry.verify_schema()
