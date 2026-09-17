"""Reading back the belief the Evaluator persisted.

The bug this file exists to prevent was found by running the query against the
real database rather than by reading it: Postgres names an un-aliased operator
expression ``?column?``, so ``SELECT active_metrics->'posterior'`` followed by
``row["posterior"]`` returns nothing for every strategy, forever. A fake pool
cannot catch it because the fake picks its own column names — which is exactly
the #212-#214 shape, where three PRs were needed for one working backup because
each failure was only visible on the next real run.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from shrap.risk_compliance.risk_officer.posterior import posterior_from_backtest
from shrap.risk_compliance.risk_officer.posterior_reader import (
    SELECT_LATEST_POSTERIOR_SQL,
    PosteriorUnavailable,
    PostgresPosteriorReader,
    parse_posterior,
)

PROTOCOL = "eval-1"


class FakeConn:
    def __init__(self, row: dict[str, Any] | None, *, boom: bool = False) -> None:
        self.row = row
        self.boom = boom
        self.calls: list[tuple[object, ...]] = []

    async def fetchrow(self, sql: str, *args: object) -> dict[str, Any] | None:
        if self.boom:
            raise RuntimeError("connection reset")
        self.calls.append(args)
        return self.row


class FakePool:
    def __init__(self, row: dict[str, Any] | None, *, boom: bool = False) -> None:
        self.conn = FakeConn(row, boom=boom)

    def acquire(self) -> Any:
        conn = self.conn

        class Ctx:
            async def __aenter__(self) -> FakeConn:
                return conn

            async def __aexit__(self, *_: object) -> None:
                return None

        return Ctx()


# --- the query -----------------------------------------------------------------


def test_the_json_column_is_aliased() -> None:
    """The bug that unit tests structurally cannot find.

    Without ``AS posterior`` the column arrives as ``?column?`` and every
    strategy reads as having no posterior — which, since the caller falls back
    to the stage fraction on ``None``, would silently restore flat sizing while
    appearing to work.
    """

    assert "AS posterior" in SELECT_LATEST_POSTERIOR_SQL


def test_the_query_skips_rows_written_before_posteriors_existed() -> None:
    """Otherwise a pre-#228 row returns SQL NULL and hides the newest belief."""

    assert "active_metrics ? 'posterior'" in SELECT_LATEST_POSTERIOR_SQL


def test_the_query_takes_the_newest_evaluation() -> None:
    assert "ORDER BY created_at DESC" in SELECT_LATEST_POSTERIOR_SQL
    assert "LIMIT 1" in SELECT_LATEST_POSTERIOR_SQL


def test_the_query_is_scoped_to_one_protocol_version() -> None:
    """A belief from a different eval protocol is not about the same quantity."""

    assert "protocol_version = $2" in SELECT_LATEST_POSTERIOR_SQL


# --- parsing -------------------------------------------------------------------


def _payload() -> str:
    return json.dumps(
        posterior_from_backtest(
            information_ratio=0.448, standard_error=0.47, attempts=4
        ).to_payload()
    )


def test_a_persisted_posterior_round_trips() -> None:
    """asyncpg hands back jsonb as a string, so this is the real shape."""

    restored = parse_posterior(_payload())

    assert restored is not None
    assert restored.mean_ir == pytest.approx(0.293, abs=0.005)
    assert restored.fraction == pytest.approx(0.147, abs=0.005)
    assert restored.backtest_ir == 0.448


def test_an_already_decoded_mapping_is_accepted() -> None:
    """Some drivers and every in-process caller hand over a dict."""

    decoded = parse_posterior(json.loads(_payload()))

    assert decoded is not None
    assert decoded.backtest_sd == pytest.approx(0.726, abs=0.005)


def test_derived_fields_are_not_taken_from_the_payload() -> None:
    """`fraction` is recomputed, so a tampered or stale one cannot size anything.

    The payload carries it for human readers; trusting it would let a row
    written by an older build dictate today's position size.
    """

    tampered = json.loads(_payload())
    tampered["fraction"] = 0.50
    restored = parse_posterior(tampered)

    assert restored is not None
    assert restored.fraction == pytest.approx(0.147, abs=0.005)


@pytest.mark.parametrize(
    "payload",
    [None, "", "   ", "{not json", '{"sd_ir": 0.5}', '{"mean_ir": 0.3}', "[1, 2, 3]", '"a string"'],
)
def test_nothing_usable_returns_none_rather_than_raising(payload: object) -> None:
    """A garbled row must not be able to size a position, or to crash the path."""

    assert parse_posterior(payload) is None  # type: ignore[arg-type]


def test_a_boolean_session_count_is_refused() -> None:
    """`isinstance(True, int)` is True in Python.

    A JSON `true` would otherwise become one session and make a prior-only
    belief report itself as evidence-based.
    """

    payload = json.loads(_payload())
    payload["n_sessions"] = True

    assert parse_posterior(payload) is None


# --- the reader ----------------------------------------------------------------


async def test_it_reads_the_latest_posterior() -> None:
    reader = PostgresPosteriorReader(FakePool({"posterior": _payload()}), protocol_version=PROTOCOL)

    got = await reader.latest_posterior("strat-1")

    assert got is not None
    assert got.fraction == pytest.approx(0.147, abs=0.005)


async def test_it_passes_the_strategy_and_protocol_as_parameters() -> None:
    pool = FakePool({"posterior": _payload()})
    await PostgresPosteriorReader(pool, protocol_version=PROTOCOL).latest_posterior("strat-1")

    assert pool.conn.calls == [("strat-1", PROTOCOL)]


async def test_no_evaluation_yet_is_none_not_an_error() -> None:
    """An unevaluated strategy has no measurement; the caller uses its stage."""

    reader = PostgresPosteriorReader(FakePool(None), protocol_version=PROTOCOL)

    assert await reader.latest_posterior("strat-1") is None


async def test_a_database_error_raises_rather_than_returning_none() -> None:
    """The distinction the whole fail-closed design rests on.

    ``None`` means "no belief exists" and the caller sizes by stage. A database
    failure returning ``None`` would look identical and silently size UP, since
    every measured posterior here is below the flat fraction.
    """

    reader = PostgresPosteriorReader(FakePool(None, boom=True), protocol_version=PROTOCOL)

    with pytest.raises(PosteriorUnavailable, match="strat-1"):
        await reader.latest_posterior("strat-1")


async def test_an_unexpected_column_type_is_a_fault_not_an_absence() -> None:
    """A driver handing back something other than JSON is a wiring problem."""

    reader = PostgresPosteriorReader(FakePool({"posterior": 42}), protocol_version=PROTOCOL)

    with pytest.raises(PosteriorUnavailable, match="int"):
        await reader.latest_posterior("strat-1")


def test_the_pinned_protocol_version_matches_the_evaluator() -> None:
    """A local copy, because importing the engine pulls numpy into this path.

    Same reason `posterior.PRIOR_IR` copies the promote floor. If these drift,
    the reader silently matches no evaluation row and every strategy reads as
    unevaluated — which falls back to the flat stage fraction and looks like
    nothing is wrong.
    """

    from shrap.research.strategy_evaluator.engine import PROTOCOL_VERSION
    from shrap.risk_compliance.risk_officer.posterior_reader import EVAL_PROTOCOL_VERSION

    assert EVAL_PROTOCOL_VERSION == PROTOCOL_VERSION
