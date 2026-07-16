"""Tests for the strategy registry state machine and repository."""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from shrap.research.strategy_registry import (
    ALL_STATUSES,
    ALLOWED_TRANSITIONS,
    INSERT_STRATEGY_SQL,
    INSERT_TRANSITION_SQL,
    PROMOTION_PATH,
    SELECT_STATUS_FOR_UPDATE_SQL,
    STATUS_HYPOTHESIS,
    STATUS_KILL_REVIEW,
    STATUS_KILL_REVIEW_MIKE,
    STATUS_KILLED,
    STATUS_LIVE_PAPER,
    STATUS_PAPER,
    STATUS_RETIRED,
    STATUS_SMALL_SIZE_PAPER,
    STREAM_STRATEGY_DEMOTED,
    STREAM_STRATEGY_KILLED,
    STREAM_STRATEGY_PROMOTED,
    STREAM_STRATEGY_REGISTERED,
    STREAM_STRATEGY_RETIRED,
    TERMINAL_STATUSES,
    UPDATE_STRATEGY_STATUS_SQL,
    InvalidTransitionError,
    PostgresStrategyRegistry,
    StrategyNotFoundError,
    StrategyRecord,
    StrategyTransition,
    stream_for_transition,
    transition_event_payload,
)


class FakeTransaction:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> object:
        self._conn.transactions_entered += 1
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.status_row: dict[str, Any] | None = None
        self.rows: list[dict[str, Any]] = []
        self.insert_result = "INSERT 0 1"
        self.transactions_entered = 0

    async def execute(self, sql: str, *args: object) -> object:
        self.executed.append((sql, args))
        if sql == INSERT_STRATEGY_SQL:
            return self.insert_result
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> Mapping[str, Any] | None:
        if sql == SELECT_STATUS_FOR_UPDATE_SQL:
            return self.status_row
        return self.rows[0] if self.rows else None

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]:
        return self.rows

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)


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


def _record(status: str = STATUS_HYPOTHESIS) -> StrategyRecord:
    return StrategyRecord(
        strategy_id="01TESTSTRATEGY",
        name="nvda-hbm-bottleneck",
        version=1,
        archetype="bottleneck-rotation",
        status=status,
        source="hypothesis-generator",
        thesis="HBM supply is the binding constraint on accelerator shipments.",
        anchor={"bottleneck_id": "hbm-2026", "replacement_layer_id": "hbm4"},
        tickers={"long": ["MU"], "short": []},
        spec={"entry_rules": "...", "exit_rules": "..."},
        spec_hash="abc123",
        regime_sizing_modifier={"late-cycle-melt-up": 1.0},
        kill_criteria=["bottleneck no longer binding event from Bottleneck Scout"],
        code_ref=None,
        created_at=None,
        updated_at=None,
    )


# --- state machine -----------------------------------------------------------


def test_promotion_path_transitions_are_allowed() -> None:
    for from_status, to_status in itertools.pairwise(PROMOTION_PATH):
        assert to_status in ALLOWED_TRANSITIONS[from_status]


def test_stage_skipping_is_not_allowed() -> None:
    assert STATUS_SMALL_SIZE_PAPER not in ALLOWED_TRANSITIONS[STATUS_HYPOTHESIS]
    assert STATUS_LIVE_PAPER not in ALLOWED_TRANSITIONS[STATUS_PAPER]


def test_terminal_statuses_have_no_exits() -> None:
    for status in TERMINAL_STATUSES:
        assert ALLOWED_TRANSITIONS[status] == frozenset()


def test_every_active_stage_can_be_demoted_killed_and_retired() -> None:
    for status in PROMOTION_PATH:
        assert STATUS_KILL_REVIEW in ALLOWED_TRANSITIONS[status]
        assert STATUS_KILLED in ALLOWED_TRANSITIONS[status]
        assert STATUS_RETIRED in ALLOWED_TRANSITIONS[status]


def test_kill_review_can_restore_escalate_or_kill() -> None:
    assert STATUS_KILLED in ALLOWED_TRANSITIONS[STATUS_KILL_REVIEW]
    assert STATUS_KILL_REVIEW_MIKE in ALLOWED_TRANSITIONS[STATUS_KILL_REVIEW]
    for status in PROMOTION_PATH:
        assert status in ALLOWED_TRANSITIONS[STATUS_KILL_REVIEW]


def test_real_money_stage_is_unrepresentable() -> None:
    assert "real" not in ALL_STATUSES
    for targets in ALLOWED_TRANSITIONS.values():
        assert "real" not in targets


def test_stream_for_transition_mapping() -> None:
    assert stream_for_transition(None, STATUS_HYPOTHESIS) == STREAM_STRATEGY_REGISTERED
    assert stream_for_transition(STATUS_HYPOTHESIS, STATUS_PAPER) == STREAM_STRATEGY_PROMOTED
    assert stream_for_transition(STATUS_PAPER, STATUS_KILL_REVIEW) == STREAM_STRATEGY_DEMOTED
    assert stream_for_transition(STATUS_KILL_REVIEW, STATUS_KILLED) == STREAM_STRATEGY_KILLED
    assert stream_for_transition(STATUS_PAPER, STATUS_RETIRED) == STREAM_STRATEGY_RETIRED
    assert stream_for_transition(STATUS_KILL_REVIEW, STATUS_PAPER) == STREAM_STRATEGY_PROMOTED


# --- register ----------------------------------------------------------------


async def test_register_inserts_strategy_and_first_transition() -> None:
    pool = FakePool()
    registry = PostgresStrategyRegistry(pool)  # type: ignore[arg-type]

    inserted = await registry.register(
        _record(), reason="proposed by nightly batch", actor="hypothesis-generator"
    )

    assert inserted is True
    sqls = [sql for sql, _ in pool.conn.executed]
    assert sqls == [INSERT_STRATEGY_SQL, INSERT_TRANSITION_SQL]
    _, transition_args = pool.conn.executed[1]
    assert transition_args[2] is None  # from_status
    assert transition_args[3] == STATUS_HYPOTHESIS
    assert pool.conn.transactions_entered == 1


async def test_register_is_idempotent_on_strategy_id() -> None:
    pool = FakePool()
    pool.conn.insert_result = "INSERT 0 0"
    registry = PostgresStrategyRegistry(pool)  # type: ignore[arg-type]

    inserted = await registry.register(_record(), reason="replayed", actor="hypothesis-generator")

    assert inserted is False
    sqls = [sql for sql, _ in pool.conn.executed]
    assert INSERT_TRANSITION_SQL not in sqls


async def test_register_rejects_non_hypothesis_status() -> None:
    registry = PostgresStrategyRegistry(FakePool())  # type: ignore[arg-type]
    with pytest.raises(InvalidTransitionError):
        await registry.register(_record(status=STATUS_PAPER), reason="nope", actor="test")


# --- transition --------------------------------------------------------------


async def test_transition_applies_update_and_appends_history() -> None:
    pool = FakePool()
    pool.conn.status_row = {"status": STATUS_HYPOTHESIS}
    registry = PostgresStrategyRegistry(pool)  # type: ignore[arg-type]

    result = await registry.transition(
        "01TESTSTRATEGY",
        STATUS_PAPER,
        reason="walk-forward passed, PBO 0.31",
        trigger_kind="evaluation",
        trigger_ref="eval-42",
        actor="strategy-evaluator",
    )

    sqls = [sql for sql, _ in pool.conn.executed]
    assert sqls == [UPDATE_STRATEGY_STATUS_SQL, INSERT_TRANSITION_SQL]
    assert result.from_status == STATUS_HYPOTHESIS
    assert result.to_status == STATUS_PAPER
    assert result.trigger_ref == "eval-42"
    assert result.transition_id


async def test_transition_rejects_disallowed_move_without_writes() -> None:
    pool = FakePool()
    pool.conn.status_row = {"status": STATUS_HYPOTHESIS}
    registry = PostgresStrategyRegistry(pool)  # type: ignore[arg-type]

    with pytest.raises(InvalidTransitionError):
        await registry.transition(
            "01TESTSTRATEGY",
            STATUS_LIVE_PAPER,
            reason="skip stages",
            trigger_kind="evaluation",
            actor="strategy-evaluator",
        )
    assert pool.conn.executed == []


async def test_transition_rejects_unknown_target_status() -> None:
    registry = PostgresStrategyRegistry(FakePool())  # type: ignore[arg-type]
    with pytest.raises(InvalidTransitionError):
        await registry.transition(
            "01TESTSTRATEGY", "real", reason="never", trigger_kind="mike", actor="mike"
        )


async def test_transition_raises_for_missing_strategy() -> None:
    pool = FakePool()
    pool.conn.status_row = None
    registry = PostgresStrategyRegistry(pool)  # type: ignore[arg-type]

    with pytest.raises(StrategyNotFoundError):
        await registry.transition(
            "01MISSING", STATUS_PAPER, reason="x", trigger_kind="evaluation", actor="evaluator"
        )


async def test_transition_expected_from_guards_stale_replay() -> None:
    pool = FakePool()
    pool.conn.status_row = {"status": STATUS_PAPER}
    registry = PostgresStrategyRegistry(pool)  # type: ignore[arg-type]

    with pytest.raises(InvalidTransitionError):
        await registry.transition(
            "01TESTSTRATEGY",
            STATUS_PAPER,
            reason="replayed verdict",
            trigger_kind="evaluation",
            actor="strategy-librarian",
            expected_from=STATUS_HYPOTHESIS,
        )
    assert pool.conn.executed == []


# --- reads -------------------------------------------------------------------


async def test_get_parses_jsonb_columns() -> None:
    pool = FakePool()
    pool.conn.rows = [
        {
            "strategy_id": "01TESTSTRATEGY",
            "name": "nvda-hbm-bottleneck",
            "version": 1,
            "archetype": "bottleneck-rotation",
            "status": STATUS_HYPOTHESIS,
            "source": "hypothesis-generator",
            "thesis": "HBM binds.",
            "anchor": '{"bottleneck_id":"hbm-2026"}',
            "tickers": '{"long":["MU"],"short":[]}',
            "spec": '{"entry_rules":"..."}',
            "spec_hash": "abc123",
            "regime_sizing_modifier": '{"late-cycle-melt-up":1.0}',
            "kill_criteria": '["bottleneck no longer binding"]',
            "code_ref": None,
            "created_at": datetime(2026, 7, 15, tzinfo=UTC),
            "updated_at": datetime(2026, 7, 15, tzinfo=UTC),
        }
    ]
    registry = PostgresStrategyRegistry(pool)  # type: ignore[arg-type]

    record = await registry.get("01TESTSTRATEGY")

    assert record is not None
    assert record.anchor == {"bottleneck_id": "hbm-2026"}
    assert record.tickers == {"long": ["MU"], "short": []}
    assert record.regime_sizing_modifier == {"late-cycle-melt-up": 1.0}
    assert record.kill_criteria == ["bottleneck no longer binding"]


async def test_get_returns_none_for_missing_row() -> None:
    registry = PostgresStrategyRegistry(FakePool())  # type: ignore[arg-type]
    assert await registry.get("01MISSING") is None


async def test_ensure_schema_creates_schema_tables_and_indexes() -> None:
    pool = FakePool()
    registry = PostgresStrategyRegistry(pool)  # type: ignore[arg-type]

    await registry.ensure_schema()

    executed_sql = "\n".join(sql for sql, _ in pool.conn.executed)
    assert "CREATE SCHEMA IF NOT EXISTS research" in executed_sql
    assert "research.strategies" in executed_sql
    assert "research.strategy_transitions" in executed_sql
    assert "strategies_spec_hash_idx" in executed_sql


# --- event payloads ----------------------------------------------------------


def test_transition_event_payload_carries_decision_context() -> None:
    transition = StrategyTransition(
        transition_id="01TR",
        strategy_id="01TESTSTRATEGY",
        from_status=STATUS_HYPOTHESIS,
        to_status=STATUS_PAPER,
        reason="walk-forward passed",
        trigger_kind="evaluation",
        trigger_ref="eval-42",
        actor="strategy-evaluator",
        occurred_at=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )

    payload = transition_event_payload(transition)

    assert payload["strategy_id"] == "01TESTSTRATEGY"
    assert payload["from_status"] == STATUS_HYPOTHESIS
    assert payload["to_status"] == STATUS_PAPER
    assert payload["reason"] == "walk-forward passed"
    assert payload["trigger_ref"] == "eval-42"
    assert "occurred_at" in payload
