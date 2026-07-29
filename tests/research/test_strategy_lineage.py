"""Lineage: what a strategy was revised from, why, and how many attempts an idea
has burned.

The firm is meant to read a verdict, form a better hypothesis and try again
(Mike, 2026-07-29). An iterating proposer that is not counted is a machine for
manufacturing false positives: test twenty variants, keep the one clearing an
information ratio of 0.5, and you have found the best of twenty draws rather
than edge. A human doing that leaves a trail of memory and doubt; an agent doing
it overnight leaves a promoted strategy.

These tests pin the two things that make the count trustworthy — the root is
resolved from the parent row rather than supplied by the caller, and a revision
has to say why it exists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from shrap.research.strategy_registry import (
    INSERT_STRATEGY_SQL,
    SELECT_LINEAGE_SQL,
    SELECT_STRATEGY_SQL,
    STATUS_HYPOTHESIS,
    LineageError,
    PostgresStrategyRegistry,
    StrategyRecord,
    UnknownParentError,
)
from shrap.research.strategy_stage_cli import render_lineage


class FamilyConn:
    """A fake connection that can hold a whole lineage, not just one row."""

    def __init__(self) -> None:
        self.by_id: dict[str, dict[str, Any]] = {}
        self.inserts: list[tuple[object, ...]] = []

    async def execute(self, sql: str, *args: object) -> object:
        if sql == INSERT_STRATEGY_SQL:
            self.inserts.append(args)
            self.by_id[str(args[0])] = {
                "strategy_id": str(args[0]),
                "name": str(args[1]),
                "version": int(args[2]),  # type: ignore[arg-type]
                "archetype": str(args[3]),
                "status": str(args[4]),
                "source": str(args[5]),
                "thesis": str(args[6]),
                "anchor": None,
                "tickers": "{}",
                "spec": "{}",
                "spec_hash": str(args[10]),
                "regime_sizing_modifier": None,
                "kill_criteria": "[]",
                "code_ref": None,
                "account_id": None,
                "created_at": args[14],
                "updated_at": args[14],
                "parent_strategy_id": args[15],
                "lineage_root_id": args[16],
                "derived_from_evaluation_id": args[17],
                "revision_reason": args[18],
            }
            return "INSERT 0 1"
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> Mapping[str, Any] | None:
        if sql == SELECT_STRATEGY_SQL:
            return self.by_id.get(str(args[0]))
        return None

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]:
        if sql == SELECT_LINEAGE_SQL:
            return [r for r in self.by_id.values() if r["lineage_root_id"] == str(args[0])]
        return []

    def transaction(self) -> FamilyConn:
        return self

    async def __aenter__(self) -> FamilyConn:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class FamilyPool:
    def __init__(self) -> None:
        self.conn = FamilyConn()

    def acquire(self) -> FamilyConn:
        return self.conn


def _record(
    strategy_id: str,
    *,
    parent: str | None = None,
    reason: str | None = None,
    evaluation: str | None = None,
    root: str | None = None,
) -> StrategyRecord:
    return StrategyRecord(
        strategy_id=strategy_id,
        name=f"strategy-{strategy_id}",
        version=1,
        archetype="technical-catalyst",
        status=STATUS_HYPOTHESIS,
        source="hypothesis-generator",
        thesis="Cross-sectional momentum over the launch universe.",
        anchor={},
        tickers={"long": ["SPY"], "short": []},
        spec={"rule": "cross-sectional-momentum"},
        spec_hash=f"hash-{strategy_id}",
        regime_sizing_modifier=None,
        kill_criteria=["momentum crashes"],
        code_ref=None,
        created_at=None,
        updated_at=None,
        parent_strategy_id=parent,
        lineage_root_id=root,
        derived_from_evaluation_id=evaluation,
        revision_reason=reason,
    )


def _registry() -> tuple[PostgresStrategyRegistry, FamilyPool]:
    pool = FamilyPool()
    return PostgresStrategyRegistry(pool), pool  # type: ignore[arg-type]


async def _register(registry: PostgresStrategyRegistry, record: StrategyRecord) -> bool:
    return await registry.register(record, reason="test", actor="test")


# --- the root is resolved, never supplied ------------------------------------


async def test_an_original_is_its_own_root() -> None:
    registry, pool = _registry()

    await _register(registry, _record("01A"))

    assert pool.conn.by_id["01A"]["lineage_root_id"] == "01A"
    assert pool.conn.by_id["01A"]["parent_strategy_id"] is None


async def test_a_revision_inherits_its_parents_root() -> None:
    registry, pool = _registry()
    await _register(registry, _record("01A"))

    await _register(registry, _record("01B", parent="01A", reason="momentum crashed in 2022"))

    assert pool.conn.by_id["01B"]["lineage_root_id"] == "01A"


async def test_a_grandchild_keeps_the_ORIGINAL_root_not_its_parents_id() -> None:
    """The assertion the whole count rests on.

    If each generation rooted itself at its parent, a lineage twelve revisions
    deep would report an attempt count of two — and a promote decision would be
    read as the second try rather than the twelfth.
    """

    registry, pool = _registry()
    await _register(registry, _record("01A"))
    await _register(registry, _record("01B", parent="01A", reason="crash protection"))
    await _register(registry, _record("01C", parent="01B", reason="tighter drawdown standdown"))

    assert pool.conn.by_id["01C"]["lineage_root_id"] == "01A"
    assert await registry.attempts("01C") == 3


async def test_a_proposer_cannot_choose_its_own_root() -> None:
    """The one number a proposer must not be able to influence.

    A generator that could nominate a fresh root would reset its own attempt
    count — laundering the twelfth try as the first, which defeats the entire
    purpose of recording lineage.
    """

    registry, pool = _registry()
    await _register(registry, _record("01A"))

    await _register(
        registry,
        _record("01B", parent="01A", reason="crash protection", root="01B-LAUNDERED"),
    )

    assert pool.conn.by_id["01B"]["lineage_root_id"] == "01A"


# --- attempt counting --------------------------------------------------------


async def test_attempts_counts_the_whole_family_from_any_member() -> None:
    """The useful question is asked from whichever attempt is in hand, so it must
    not require already knowing the root."""

    registry, _ = _registry()
    await _register(registry, _record("01A"))
    for i, child in enumerate(("01B", "01C", "01D")):
        await _register(registry, _record(child, parent="01A", reason=f"revision {i}"))

    assert await registry.attempts("01A") == 4
    assert await registry.attempts("01C") == 4  # asked from a leaf


async def test_an_untried_idea_counts_as_one_attempt_not_zero() -> None:
    """The original IS an attempt. Counting from zero would make the first
    promotion look like it came from nowhere."""

    registry, _ = _registry()
    await _register(registry, _record("01A"))

    assert await registry.attempts("01A") == 1


async def test_an_unknown_strategy_has_no_lineage() -> None:
    registry, _ = _registry()

    assert await registry.lineage("01MISSING") == []
    assert await registry.attempts("01MISSING") == 0


# --- invariants --------------------------------------------------------------


async def test_a_revision_must_say_why_it_exists() -> None:
    """`revision_reason` is the only field separating "momentum crashed, so this
    one stands down after a drawdown" from "lookback 126 -> 100". Without it the
    two rows are identical."""

    registry, _ = _registry()
    await _register(registry, _record("01A"))

    with pytest.raises(LineageError, match="revision_reason"):
        await _register(registry, _record("01B", parent="01A"))


async def test_a_blank_reason_is_no_reason() -> None:
    registry, _ = _registry()
    await _register(registry, _record("01A"))

    with pytest.raises(LineageError, match="revision_reason"):
        await _register(registry, _record("01B", parent="01A", reason="   "))


async def test_a_revision_of_a_strategy_that_does_not_exist_is_refused() -> None:
    """Fail closed rather than registering it as an original: a revision whose
    parent silently vanishes becomes a fresh lineage with an attempt count of
    one, which is exactly the number this exists to get right."""

    registry, _ = _registry()

    with pytest.raises(UnknownParentError, match="01GHOST"):
        await _register(registry, _record("01B", parent="01GHOST", reason="crash protection"))


async def test_a_strategy_cannot_be_its_own_parent() -> None:
    registry, _ = _registry()

    with pytest.raises(LineageError, match="own parent"):
        await _register(registry, _record("01A", parent="01A", reason="ouroboros"))


async def test_an_original_may_not_carry_a_revision_reason() -> None:
    """A reason with nothing to be a revision of is a proposer bug, and silently
    accepting it would put unfalsifiable narrative in the audit trail."""

    registry, _ = _registry()

    with pytest.raises(LineageError, match="require a parent"):
        await _register(registry, _record("01A", reason="revised from nothing"))


# --- reading it --------------------------------------------------------------


def test_the_lineage_render_leads_with_the_attempt_count() -> None:
    """A promote decision on attempt 12 reads very differently from the same
    numbers on attempt 1, and nothing else the firm prints carries that."""

    records = [
        _record("01A", root="01A"),
        _record(
            "01B",
            parent="01A",
            reason="stand down after a drawdown",
            evaluation="01EVAL",
            root="01A",
        ),
    ]

    out = render_lineage(records, "01B")

    assert "Attempts: 2" in out
    assert "01A" in out and "01B" in out
    assert "revised because: stand down after a drawdown" in out
    assert "evidence: evaluation 01EVAL" in out
    # The multiple-testing warning appears once there is more than one attempt.
    assert "best of N draws" in out


def test_a_single_attempt_gets_no_multiple_testing_warning() -> None:
    """Noise on the common case would train the reader to ignore it."""

    out = render_lineage([_record("01A", root="01A")], "01A")

    assert "Attempts: 1" in out
    assert "best of N draws" not in out


def test_a_member_whose_parent_is_missing_is_surfaced_not_dropped() -> None:
    """A lineage that silently hides a member miscounts the search."""

    orphan = _record("01B", parent="01GONE", reason="r", root="01A")
    out = render_lineage([_record("01A", root="01A"), orphan], "01A")

    assert "01B" in out
    assert "parent 01GONE not found" in out


def test_an_unknown_strategy_renders_a_refusal_not_an_empty_tree() -> None:
    assert render_lineage([], "01MISSING") == "no strategy '01MISSING'"
