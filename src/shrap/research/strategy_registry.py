"""Strategy registry: the middle loop's system of record.

This is the persistence seam for strategy lifecycle state (ADR-0007 research
funnel). Every strategy the firm ever considers gets exactly one row in
``research.strategies``; every lifecycle decision gets an append-only row in
``research.strategy_transitions`` carrying the reasoning at decision time, per
the architecture's append-only record rule (docs/02-architecture.md §10).

All writers — Hypothesis Generator, Strategy Evaluator, Strategy Librarian,
Mike via on-demand tooling — go through :class:`PostgresStrategyRegistry`,
which enforces the promotion state machine. The ``real`` stage is deliberately
absent from the status set: real-money execution is post-sprint and requires
its own ADR, so the registry cannot even represent it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from ulid import ULID

SCHEMA_VERSION = "1.0.0"

# Lifecycle statuses. Promotion stages per the Strategy Evaluator spec
# (docs/agents/research/strategy-evaluator.md §Promotion stages); kill states
# per its kill-review pipeline. `real` is intentionally unrepresentable.
STATUS_HYPOTHESIS = "hypothesis"
STATUS_PAPER = "paper"
STATUS_SMALL_SIZE_PAPER = "small-size-paper"
STATUS_LIVE_PAPER = "live-paper"
STATUS_KILL_REVIEW = "kill-review"
STATUS_KILL_REVIEW_MIKE = "kill-review-mike"
STATUS_KILLED = "killed"
STATUS_RETIRED = "retired"

PROMOTION_PATH: tuple[str, ...] = (
    STATUS_HYPOTHESIS,
    STATUS_PAPER,
    STATUS_SMALL_SIZE_PAPER,
    STATUS_LIVE_PAPER,
)
TERMINAL_STATUSES: frozenset[str] = frozenset({STATUS_KILLED, STATUS_RETIRED})
ALL_STATUSES: frozenset[str] = (
    frozenset(PROMOTION_PATH)
    | TERMINAL_STATUSES
    | {
        STATUS_KILL_REVIEW,
        STATUS_KILL_REVIEW_MIKE,
    }
)

_ACTIVE_STAGES = frozenset(PROMOTION_PATH)

# Restoration from kill-review back to an active stage is the false-alarm path;
# the state machine allows any active stage as the target because "the prior
# stage" is history-dependent — callers restore using the transition log.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_HYPOTHESIS: frozenset({STATUS_PAPER, STATUS_KILL_REVIEW, STATUS_KILLED, STATUS_RETIRED}),
    STATUS_PAPER: frozenset(
        {STATUS_SMALL_SIZE_PAPER, STATUS_KILL_REVIEW, STATUS_KILLED, STATUS_RETIRED}
    ),
    STATUS_SMALL_SIZE_PAPER: frozenset(
        {STATUS_LIVE_PAPER, STATUS_KILL_REVIEW, STATUS_KILLED, STATUS_RETIRED}
    ),
    STATUS_LIVE_PAPER: frozenset({STATUS_KILL_REVIEW, STATUS_KILLED, STATUS_RETIRED}),
    STATUS_KILL_REVIEW: frozenset({STATUS_KILLED, STATUS_KILL_REVIEW_MIKE}) | _ACTIVE_STAGES,
    STATUS_KILL_REVIEW_MIKE: frozenset({STATUS_KILLED}) | _ACTIVE_STAGES,
    STATUS_KILLED: frozenset(),
    STATUS_RETIRED: frozenset(),
}

# Lifecycle event streams (ADR-0006 envelopes). Names align with the Strategy
# Evaluator spec's research.strategy.* streams.
STREAM_STRATEGY_REGISTERED = "research.strategy.registered"
STREAM_STRATEGY_PROMOTED = "research.strategy.promoted"
STREAM_STRATEGY_DEMOTED = "research.strategy.demoted"
STREAM_STRATEGY_KILLED = "research.strategy.killed"
STREAM_STRATEGY_RETIRED = "research.strategy.retired"

CREATE_RESEARCH_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS research"

CREATE_STRATEGIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.strategies (
    strategy_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    archetype TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    thesis TEXT NOT NULL,
    anchor JSONB,
    tickers JSONB NOT NULL,
    spec JSONB NOT NULL,
    spec_hash TEXT NOT NULL,
    regime_sizing_modifier JSONB,
    kill_criteria JSONB NOT NULL,
    code_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, version)
)
""".strip()

CREATE_STRATEGIES_SPEC_HASH_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS strategies_spec_hash_idx
ON research.strategies (spec_hash)
""".strip()

CREATE_STRATEGIES_STATUS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS strategies_status_idx
ON research.strategies (status, updated_at DESC)
""".strip()

CREATE_TRANSITIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.strategy_transitions (
    transition_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL REFERENCES research.strategies (strategy_id),
    from_status TEXT,
    to_status TEXT NOT NULL,
    reason TEXT NOT NULL,
    trigger_kind TEXT NOT NULL,
    trigger_ref TEXT,
    actor TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip()

CREATE_TRANSITIONS_STRATEGY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS strategy_transitions_strategy_idx
ON research.strategy_transitions (strategy_id, occurred_at)
""".strip()

INSERT_STRATEGY_SQL = """
INSERT INTO research.strategies (
    strategy_id,
    name,
    version,
    archetype,
    status,
    source,
    thesis,
    anchor,
    tickers,
    spec,
    spec_hash,
    regime_sizing_modifier,
    kill_criteria,
    code_ref,
    created_at,
    updated_at
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7,
    $8::jsonb, $9::jsonb, $10::jsonb, $11, $12::jsonb, $13::jsonb, $14, $15, $15
)
ON CONFLICT (strategy_id) DO NOTHING
""".strip()

SELECT_STRATEGY_SQL = """
SELECT
    strategy_id, name, version, archetype, status, source, thesis,
    anchor, tickers, spec, spec_hash, regime_sizing_modifier, kill_criteria,
    code_ref, created_at, updated_at
FROM research.strategies
WHERE strategy_id = $1
""".strip()

SELECT_STRATEGIES_BY_STATUS_SQL = """
SELECT
    strategy_id, name, version, archetype, status, source, thesis,
    anchor, tickers, spec, spec_hash, regime_sizing_modifier, kill_criteria,
    code_ref, created_at, updated_at
FROM research.strategies
WHERE status = $1
ORDER BY updated_at DESC
""".strip()

SELECT_STATUS_FOR_UPDATE_SQL = """
SELECT status FROM research.strategies WHERE strategy_id = $1 FOR UPDATE
""".strip()

UPDATE_STRATEGY_STATUS_SQL = """
UPDATE research.strategies SET status = $2, updated_at = $3 WHERE strategy_id = $1
""".strip()

INSERT_TRANSITION_SQL = """
INSERT INTO research.strategy_transitions (
    transition_id, strategy_id, from_status, to_status,
    reason, trigger_kind, trigger_ref, actor, occurred_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
""".strip()

SELECT_TRANSITIONS_SQL = """
SELECT
    transition_id, strategy_id, from_status, to_status,
    reason, trigger_kind, trigger_ref, actor, occurred_at
FROM research.strategy_transitions
WHERE strategy_id = $1
ORDER BY occurred_at, transition_id
""".strip()


class StrategyRegistryError(Exception):
    """Base error for registry operations."""


class StrategyNotFoundError(StrategyRegistryError):
    """The strategy_id has no row in research.strategies."""


class InvalidTransitionError(StrategyRegistryError):
    """The requested lifecycle transition is not allowed by the state machine."""


@dataclass(frozen=True, slots=True)
class StrategyRecord:
    """One strategy in the registry — the full proposal plus lifecycle state."""

    strategy_id: str
    name: str
    version: int
    archetype: str
    status: str
    source: str
    thesis: str
    anchor: dict[str, Any] | None
    tickers: dict[str, Any]
    spec: dict[str, Any]
    spec_hash: str
    regime_sizing_modifier: dict[str, Any] | None
    kill_criteria: list[Any]
    code_ref: str | None
    created_at: object
    updated_at: object


@dataclass(frozen=True, slots=True)
class StrategyTransition:
    """One append-only lifecycle decision, with the reasoning at decision time."""

    transition_id: str
    strategy_id: str
    from_status: str | None
    to_status: str
    reason: str
    trigger_kind: str
    trigger_ref: str | None
    actor: str
    occurred_at: object


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetchrow(self, sql: str, *args: object) -> Mapping[str, Any] | None: ...

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]: ...

    def transaction(self) -> AbstractAsyncContextManager[object]: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


class PostgresStrategyRegistry:
    """State-machine-enforcing repository over research.strategies."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_RESEARCH_SCHEMA_SQL)
            await conn.execute(CREATE_STRATEGIES_TABLE_SQL)
            await conn.execute(CREATE_STRATEGIES_SPEC_HASH_INDEX_SQL)
            await conn.execute(CREATE_STRATEGIES_STATUS_INDEX_SQL)
            await conn.execute(CREATE_TRANSITIONS_TABLE_SQL)
            await conn.execute(CREATE_TRANSITIONS_STRATEGY_INDEX_SQL)

    async def register(
        self,
        record: StrategyRecord,
        *,
        reason: str,
        actor: str,
        trigger_kind: str = "registration",
        trigger_ref: str | None = None,
    ) -> bool:
        """Insert a new strategy at status=hypothesis with its first transition.

        Returns False when the strategy_id already exists (idempotent
        re-delivery); a duplicate spec_hash under a new strategy_id raises the
        unique-violation from the driver — that is a proposer bug, not
        re-delivery.
        """

        if record.status != STATUS_HYPOTHESIS:
            raise InvalidTransitionError(
                f"strategies register at status={STATUS_HYPOTHESIS!r}, got {record.status!r}"
            )
        occurred_at = datetime.now(UTC)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                result = await conn.execute(
                    INSERT_STRATEGY_SQL,
                    record.strategy_id,
                    record.name,
                    record.version,
                    record.archetype,
                    record.status,
                    record.source,
                    record.thesis,
                    _json_or_none(record.anchor),
                    json.dumps(record.tickers, separators=(",", ":")),
                    json.dumps(record.spec, separators=(",", ":")),
                    record.spec_hash,
                    _json_or_none(record.regime_sizing_modifier),
                    json.dumps(record.kill_criteria, separators=(",", ":")),
                    record.code_ref,
                    occurred_at,
                )
                if not str(result).endswith(" 1"):
                    return False
                await conn.execute(
                    INSERT_TRANSITION_SQL,
                    str(ULID()),
                    record.strategy_id,
                    None,
                    STATUS_HYPOTHESIS,
                    reason,
                    trigger_kind,
                    trigger_ref,
                    actor,
                    occurred_at,
                )
        return True

    async def get(self, strategy_id: str) -> StrategyRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_STRATEGY_SQL, strategy_id)
        if row is None:
            return None
        return _record_from_row(row)

    async def list_by_status(self, status: str) -> list[StrategyRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_STRATEGIES_BY_STATUS_SQL, status)
        return [_record_from_row(row) for row in rows]

    async def transitions(self, strategy_id: str) -> list[StrategyTransition]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_TRANSITIONS_SQL, strategy_id)
        return [_transition_from_row(row) for row in rows]

    async def transition(
        self,
        strategy_id: str,
        to_status: str,
        *,
        reason: str,
        trigger_kind: str,
        actor: str,
        trigger_ref: str | None = None,
        expected_from: str | None = None,
    ) -> StrategyTransition:
        """Apply one lifecycle transition atomically, or raise.

        ``expected_from`` makes the call conditional for callers acting on
        possibly-stale reads (optimistic concurrency); the row lock makes the
        check-then-write race-free either way.
        """

        if to_status not in ALL_STATUSES:
            raise InvalidTransitionError(f"unknown target status {to_status!r}")
        occurred_at = datetime.now(UTC)
        transition_id = str(ULID())
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(SELECT_STATUS_FOR_UPDATE_SQL, strategy_id)
                if row is None:
                    raise StrategyNotFoundError(strategy_id)
                from_status = str(row["status"])
                if expected_from is not None and from_status != expected_from:
                    raise InvalidTransitionError(
                        f"{strategy_id}: expected status {expected_from!r}, found {from_status!r}"
                    )
                if to_status not in ALLOWED_TRANSITIONS.get(from_status, frozenset()):
                    raise InvalidTransitionError(
                        f"{strategy_id}: {from_status!r} -> {to_status!r} is not allowed"
                    )
                await conn.execute(UPDATE_STRATEGY_STATUS_SQL, strategy_id, to_status, occurred_at)
                await conn.execute(
                    INSERT_TRANSITION_SQL,
                    transition_id,
                    strategy_id,
                    from_status,
                    to_status,
                    reason,
                    trigger_kind,
                    trigger_ref,
                    actor,
                    occurred_at,
                )
        return StrategyTransition(
            transition_id=transition_id,
            strategy_id=strategy_id,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            trigger_kind=trigger_kind,
            trigger_ref=trigger_ref,
            actor=actor,
            occurred_at=occurred_at,
        )


def stream_for_transition(from_status: str | None, to_status: str) -> str:
    """Map a lifecycle transition to its ADR-0006 event stream."""

    if from_status is None:
        return STREAM_STRATEGY_REGISTERED
    if to_status == STATUS_KILLED:
        return STREAM_STRATEGY_KILLED
    if to_status == STATUS_RETIRED:
        return STREAM_STRATEGY_RETIRED
    if to_status in (STATUS_KILL_REVIEW, STATUS_KILL_REVIEW_MIKE):
        return STREAM_STRATEGY_DEMOTED
    return STREAM_STRATEGY_PROMOTED


def transition_event_payload(transition: StrategyTransition) -> dict[str, Any]:
    """Payload for the lifecycle event a librarian publishes per transition."""

    return {
        "strategy_id": transition.strategy_id,
        "transition_id": transition.transition_id,
        "from_status": transition.from_status,
        "to_status": transition.to_status,
        "reason": transition.reason,
        "trigger_kind": transition.trigger_kind,
        "trigger_ref": transition.trigger_ref,
        "actor": transition.actor,
        "occurred_at": str(transition.occurred_at),
    }


def _json_or_none(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, separators=(",", ":"))


def _json_loaded(value: object) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _record_from_row(row: Mapping[str, Any]) -> StrategyRecord:
    anchor = _json_loaded(row["anchor"])
    regime = _json_loaded(row["regime_sizing_modifier"])
    return StrategyRecord(
        strategy_id=str(row["strategy_id"]),
        name=str(row["name"]),
        version=int(row["version"]),
        archetype=str(row["archetype"]),
        status=str(row["status"]),
        source=str(row["source"]),
        thesis=str(row["thesis"]),
        anchor=anchor if isinstance(anchor, dict) else None,
        tickers=_json_loaded(row["tickers"]),
        spec=_json_loaded(row["spec"]),
        spec_hash=str(row["spec_hash"]),
        regime_sizing_modifier=regime if isinstance(regime, dict) else None,
        kill_criteria=_json_loaded(row["kill_criteria"]),
        code_ref=None if row["code_ref"] is None else str(row["code_ref"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _transition_from_row(row: Mapping[str, Any]) -> StrategyTransition:
    return StrategyTransition(
        transition_id=str(row["transition_id"]),
        strategy_id=str(row["strategy_id"]),
        from_status=None if row["from_status"] is None else str(row["from_status"]),
        to_status=str(row["to_status"]),
        reason=str(row["reason"]),
        trigger_kind=str(row["trigger_kind"]),
        trigger_ref=None if row["trigger_ref"] is None else str(row["trigger_ref"]),
        actor=str(row["actor"]),
        occurred_at=row["occurred_at"],
    )
