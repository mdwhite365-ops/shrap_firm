"""Tests for the promotion workflow: promote, kill, and the Mike-seed path."""

from __future__ import annotations

import json
from typing import Any

import pytest

from shrap.research.tech_watcher.candidates import INSERT_CANDIDATE_SQL
from shrap.research.tech_watcher.promotion import (
    SOURCE_CLASS_MIKE_SEED,
    STATUS_KILLED,
    STATUS_PROMOTED,
    STREAM_WORLD_CHANGER_CRITERIA_AMENDED,
    STREAM_WORLD_CHANGER_KILLED,
    STREAM_WORLD_CHANGER_PROMOTED,
    UPDATE_CRITERIA_SQL,
    UPDATE_DECISION_SQL,
    DecisionError,
    amend_criteria,
    kill_candidate,
    promote_candidate,
    seed_candidate,
)
from shrap.research.tech_watcher.synthesis import (
    STATUS_PROPOSED,
    STREAM_WORLD_CHANGER_PROPOSED,
)


class FakeTransaction:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> object:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakeConn:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, sql: str, *args: object) -> object:
        self.executed.append((sql, args))
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> dict[str, Any] | None:
        return self.row

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        return []

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
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.conn = FakeConn(row)

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)


class FakeRedis:
    def __init__(self) -> None:
        self.streams: list[str] = []
        self.fields: list[dict[str, str]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.streams.append(stream)
        self.fields.append(fields)
        return f"{len(self.streams)}-0"


def _candidate_row(status: str) -> dict[str, Any]:
    return {
        "candidate_id": "01TESTULID",
        "name": "Mass-manufactured fission",
        "archetype": "cost-curve",
        "status": status,
        "source_classes": json.dumps(["usaspending", "doe-newsroom"]),
    }


def _published_payload(redis: FakeRedis, index: int = 0) -> dict[str, Any]:
    payload = json.loads(redis.fields[index]["payload"])
    assert isinstance(payload, dict)
    return payload


async def test_promote_updates_status_and_publishes_mapper_trigger() -> None:
    pool = FakePool(_candidate_row(STATUS_PROPOSED))
    redis = FakeRedis()

    decision = await promote_candidate(pool, redis, "01TESTULID", note="strong PPA leg")  # type: ignore[arg-type]

    assert decision.status == STATUS_PROMOTED
    assert redis.streams == [STREAM_WORLD_CHANGER_PROMOTED]
    payload = _published_payload(redis)
    assert payload["candidate_id"] == "01TESTULID"
    assert payload["previous_status"] == STATUS_PROPOSED
    assert payload["note"] == "strong PPA leg"
    update_calls = [args for sql, args in pool.conn.executed if sql == UPDATE_DECISION_SQL]
    assert len(update_calls) == 1
    assert update_calls[0][1] == STATUS_PROMOTED


async def test_promote_missing_candidate_refuses() -> None:
    pool = FakePool(row=None)
    redis = FakeRedis()

    with pytest.raises(DecisionError, match="does not exist"):
        await promote_candidate(pool, redis, "nope")  # type: ignore[arg-type]
    assert redis.streams == []


async def test_promote_from_killed_refuses() -> None:
    pool = FakePool(_candidate_row(STATUS_KILLED))
    redis = FakeRedis()

    with pytest.raises(DecisionError, match="killed"):
        await promote_candidate(pool, redis, "01TESTULID")  # type: ignore[arg-type]
    assert redis.streams == []


async def test_kill_requires_reason() -> None:
    pool = FakePool(_candidate_row(STATUS_PROPOSED))
    redis = FakeRedis()

    with pytest.raises(DecisionError, match="reason"):
        await kill_candidate(pool, redis, "01TESTULID", reason="  ")  # type: ignore[arg-type]
    assert redis.streams == []


async def test_kill_from_promoted_preserves_reason_and_publishes() -> None:
    pool = FakePool(_candidate_row(STATUS_PROMOTED))
    redis = FakeRedis()

    decision = await kill_candidate(
        pool,
        redis,  # type: ignore[arg-type]
        "01TESTULID",
        reason="cost curve flattened 4 consecutive quarters",
    )

    assert decision.status == STATUS_KILLED
    assert redis.streams == [STREAM_WORLD_CHANGER_KILLED]
    payload = _published_payload(redis)
    assert payload["reason"] == "cost curve flattened 4 consecutive quarters"
    assert payload["previous_status"] == STATUS_PROMOTED
    update_calls = [args for sql, args in pool.conn.executed if sql == UPDATE_DECISION_SQL]
    assert update_calls[0][3] == "cost curve flattened 4 consecutive quarters"


async def test_seed_inserts_mike_seed_candidate_and_publishes_proposed() -> None:
    pool = FakePool()
    redis = FakeRedis()

    decision = await seed_candidate(
        pool,  # type: ignore[arg-type]
        redis,  # type: ignore[arg-type]
        name="Mass-manufactured fission",
        archetype="cost-curve",
        thesis="Factory-built small reactors push fission $/kWh down a learning curve.",
        kill_criteria=["No unsubsidized PPA signed by horizon", "  "],
        falsifier_horizon="2028-06",
    )

    assert decision.status == STATUS_PROPOSED
    assert redis.streams == [STREAM_WORLD_CHANGER_PROPOSED]
    payload = _published_payload(redis)
    assert payload["source_classes"] == [SOURCE_CLASS_MIKE_SEED]
    insert_calls = [args for sql, args in pool.conn.executed if sql == INSERT_CANDIDATE_SQL]
    assert len(insert_calls) == 1
    args = insert_calls[0]
    assert args[3] == STATUS_PROPOSED  # status
    assert json.loads(str(args[7])) == ["No unsubsidized PPA signed by horizon"]
    assert json.loads(str(args[10])) == [SOURCE_CLASS_MIKE_SEED]


async def test_seed_refuses_unknown_archetype_and_empty_criteria() -> None:
    pool = FakePool()
    redis = FakeRedis()

    with pytest.raises(DecisionError, match="unknown archetype"):
        await seed_candidate(
            pool,  # type: ignore[arg-type]
            redis,  # type: ignore[arg-type]
            name="x",
            archetype="vibes",
            thesis="t",
            kill_criteria=["k"],
            falsifier_horizon="2027",
        )

    with pytest.raises(DecisionError, match="kill criterion"):
        await seed_candidate(
            pool,  # type: ignore[arg-type]
            redis,  # type: ignore[arg-type]
            name="x",
            archetype="cost-curve",
            thesis="t",
            kill_criteria=["   "],
            falsifier_horizon="2027",
        )
    assert redis.streams == []


# --- amend-criteria -------------------------------------------------------
#
# The falsifier set is the only thing standing between a thesis and
# unfalsifiability, so these tests are mostly about what the verb REFUSES.


def _thesis_row(status: str, criteria: list[str]) -> dict[str, Any]:
    return {
        "candidate_id": "01TESTULID",
        "name": "Mass-manufactured fission",
        "archetype": "cost-curve",
        "status": status,
        "source_classes": json.dumps(["usaspending", "doe-newsroom"]),
        "kill_criteria": json.dumps(criteria),
    }


async def test_amend_appends_and_leaves_existing_indices_untouched() -> None:
    pool = FakePool(_thesis_row(STATUS_PROMOTED, ["first", "second"]))
    redis = FakeRedis()

    amendment = await amend_criteria(
        pool,  # type: ignore[arg-type]
        redis,  # type: ignore[arg-type]
        "01TESTULID",
        added=["third"],
        reason="no criterion measured a competing technology",
    )

    # Recorded observations point at criteria by index. If an append ever
    # reordered or dropped one, existing evidence would silently repoint.
    assert amendment.criteria == ("first", "second", "third")
    written = [args for sql, args in pool.conn.executed if sql == UPDATE_CRITERIA_SQL]
    assert len(written) == 1
    assert json.loads(str(written[0][1])) == ["first", "second", "third"]


async def test_amend_publishes_before_and_after_counts() -> None:
    pool = FakePool(_thesis_row(STATUS_PROMOTED, ["first"]))
    redis = FakeRedis()

    await amend_criteria(
        pool,  # type: ignore[arg-type]
        redis,  # type: ignore[arg-type]
        "01TESTULID",
        added=["second", "third"],
        reason="blind spot",
    )

    assert redis.streams == [STREAM_WORLD_CHANGER_CRITERIA_AMENDED]
    payload = _published_payload(redis)
    # A thesis that keeps gaining criteria was less falsifiable than claimed.
    # The rate of amendment has to be queryable, not buried in a column diff.
    assert payload["criteria_before"] == 1
    assert payload["criteria_after"] == 3
    assert payload["added"] == ["second", "third"]
    assert payload["reason"] == "blind spot"


async def test_amend_refuses_a_killed_thesis() -> None:
    pool = FakePool(_thesis_row(STATUS_KILLED, ["first"]))
    redis = FakeRedis()

    with pytest.raises(DecisionError, match="killed"):
        await amend_criteria(
            pool,  # type: ignore[arg-type]
            redis,  # type: ignore[arg-type]
            "01TESTULID",
            added=["second"],
            reason="r",
        )
    assert redis.streams == []


async def test_amend_refuses_duplicate_criterion_case_insensitively() -> None:
    pool = FakePool(_thesis_row(STATUS_PROMOTED, ["No unsubsidized PPA signed"]))
    redis = FakeRedis()

    # A duplicate would hold its own index while meaning the same thing, so an
    # observation could bear on one and leave its twin reading UNTOUCHED.
    with pytest.raises(DecisionError, match="already a kill criterion"):
        await amend_criteria(
            pool,  # type: ignore[arg-type]
            redis,  # type: ignore[arg-type]
            "01TESTULID",
            added=["  no unsubsidized ppa signed  "],
            reason="r",
        )
    assert redis.streams == []
    assert not [args for sql, args in pool.conn.executed if sql == UPDATE_CRITERIA_SQL]


async def test_amend_requires_a_reason_and_a_criterion() -> None:
    pool = FakePool(_thesis_row(STATUS_PROMOTED, ["first"]))
    redis = FakeRedis()

    with pytest.raises(DecisionError, match="requires a reason"):
        await amend_criteria(
            pool,  # type: ignore[arg-type]
            redis,  # type: ignore[arg-type]
            "01TESTULID",
            added=["second"],
            reason="   ",
        )
    with pytest.raises(DecisionError, match="kill criterion"):
        await amend_criteria(
            pool,  # type: ignore[arg-type]
            redis,  # type: ignore[arg-type]
            "01TESTULID",
            added=["  "],
            reason="r",
        )
    assert redis.streams == []


async def test_amend_missing_candidate_refuses() -> None:
    pool = FakePool(row=None)
    redis = FakeRedis()

    with pytest.raises(DecisionError, match="does not exist"):
        await amend_criteria(
            pool,  # type: ignore[arg-type]
            redis,  # type: ignore[arg-type]
            "nope",
            added=["second"],
            reason="r",
        )
    assert redis.streams == []


async def test_amend_render_marks_only_the_new_criteria() -> None:
    pool = FakePool(_thesis_row(STATUS_PROMOTED, ["first", "second"]))
    redis = FakeRedis()

    amendment = await amend_criteria(
        pool,  # type: ignore[arg-type]
        redis,  # type: ignore[arg-type]
        "01TESTULID",
        added=["third"],
        reason="r",
    )

    rendered = amendment.render()
    assert "[0]     first" in rendered
    assert "[2] NEW third" in rendered
    assert "NEW first" not in rendered
