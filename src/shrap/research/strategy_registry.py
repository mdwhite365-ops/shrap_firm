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
    account_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, version)
)
""".strip()

# Lineage (Mike, 2026-07-29). A strategy revised in response to a verdict records
# what it came from and why.
#
# The reason it exists is not bookkeeping. The firm is meant to read a verdict,
# form a better hypothesis and try again — and an iterating proposer that is not
# counted is a machine for manufacturing false positives. Test twenty variants
# and keep the one clearing IR 0.5 and you have not found edge, you have found
# the best of twenty draws. A human doing that leaves a trail of memory and
# doubt; an agent doing it at 3am leaves a promoted strategy.
#
# So `lineage_root_id` makes "how many attempts has this idea burned" a single
# indexed query rather than a recursive walk, and `revision_reason` forces the
# proposer to state a rationale that a person can later read and call a
# parameter sweep. Denormalising the root is safe because parents never change:
# the registry is append-only and a strategy's ancestry is fixed at insert.
ALTER_STRATEGIES_ADD_LINEAGE_SQL = """
ALTER TABLE research.strategies
ADD COLUMN IF NOT EXISTS parent_strategy_id TEXT,
ADD COLUMN IF NOT EXISTS lineage_root_id TEXT,
ADD COLUMN IF NOT EXISTS derived_from_evaluation_id TEXT,
ADD COLUMN IF NOT EXISTS revision_reason TEXT
""".strip()

# Backfills pre-lineage rows as their own roots. Without this every strategy
# registered before this column existed reads as lineage-less, and the first
# revision of one would start a lineage that excluded its own parent —
# undercounting the search from the very first use.
BACKFILL_STRATEGIES_LINEAGE_ROOT_SQL = """
UPDATE research.strategies
SET lineage_root_id = strategy_id
WHERE lineage_root_id IS NULL
""".strip()

CREATE_STRATEGIES_LINEAGE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS strategies_lineage_root_idx
ON research.strategies (lineage_root_id)
""".strip()

SELECT_LINEAGE_SQL = """
SELECT
    strategy_id, name, version, archetype, status, source, thesis,
    anchor, tickers, spec, spec_hash, regime_sizing_modifier, kill_criteria,
    code_ref, account_id, created_at, updated_at,
    parent_strategy_id, lineage_root_id, derived_from_evaluation_id,
    revision_reason
FROM research.strategies
WHERE lineage_root_id = $1
ORDER BY created_at, strategy_id
""".strip()

CREATE_STRATEGIES_SPEC_HASH_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS strategies_spec_hash_idx
ON research.strategies (spec_hash)
""".strip()

CREATE_STRATEGIES_STATUS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS strategies_status_idx
ON research.strategies (status, updated_at DESC)
""".strip()

# ADR-0017: one strategy per broker account, so a strategy has to record which
# account it trades. Nullable — an unassigned strategy is a real state (nothing
# has been allocated to it yet), and inventing an account for one would route
# its orders to a book nobody chose.
ALTER_STRATEGIES_ADD_ACCOUNT_ID_SQL = """
ALTER TABLE research.strategies
ADD COLUMN IF NOT EXISTS account_id TEXT
""".strip()

# One strategy per account is the whole point of ADR-0017 — it is what makes the
# account's equity curve that strategy's P&L. A partial unique index enforces it
# in the database rather than in whichever code path happens to run next; NULLs
# are excluded so any number of strategies may be unassigned.
CREATE_STRATEGIES_ACCOUNT_UNIQUE_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS strategies_account_id_idx
ON research.strategies (account_id)
WHERE account_id IS NOT NULL
""".strip()

UPDATE_STRATEGY_ACCOUNT_SQL = """
UPDATE research.strategies SET account_id = $2, updated_at = now() WHERE strategy_id = $1
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
    updated_at,
    parent_strategy_id,
    lineage_root_id,
    derived_from_evaluation_id,
    revision_reason
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7,
    $8::jsonb, $9::jsonb, $10::jsonb, $11, $12::jsonb, $13::jsonb, $14, $15, $15,
    $16, $17, $18, $19
)
ON CONFLICT (strategy_id) DO NOTHING
""".strip()

SELECT_STRATEGY_SQL = """
SELECT
    strategy_id, name, version, archetype, status, source, thesis,
    anchor, tickers, spec, spec_hash, regime_sizing_modifier, kill_criteria,
    code_ref, account_id, created_at, updated_at,
    parent_strategy_id, lineage_root_id, derived_from_evaluation_id,
    revision_reason
FROM research.strategies
WHERE strategy_id = $1
""".strip()

SELECT_STRATEGIES_BY_STATUS_SQL = """
SELECT
    strategy_id, name, version, archetype, status, source, thesis,
    anchor, tickers, spec, spec_hash, regime_sizing_modifier, kill_criteria,
    code_ref, account_id, created_at, updated_at,
    parent_strategy_id, lineage_root_id, derived_from_evaluation_id,
    revision_reason
FROM research.strategies
WHERE status = $1
ORDER BY updated_at DESC
""".strip()

SELECT_STRATEGY_BY_SPEC_HASH_SQL = """
SELECT
    strategy_id, name, version, archetype, status, source, thesis,
    anchor, tickers, spec, spec_hash, regime_sizing_modifier, kill_criteria,
    code_ref, account_id, created_at, updated_at,
    parent_strategy_id, lineage_root_id, derived_from_evaluation_id,
    revision_reason
FROM research.strategies
WHERE spec_hash = $1
""".strip()

SELECT_ALL_STRATEGIES_SQL = """
SELECT
    strategy_id, name, version, archetype, status, source, thesis,
    anchor, tickers, spec, spec_hash, regime_sizing_modifier, kill_criteria,
    code_ref, account_id, created_at, updated_at,
    parent_strategy_id, lineage_root_id, derived_from_evaluation_id,
    revision_reason
FROM research.strategies
ORDER BY created_at DESC, strategy_id
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


class UnknownParentError(StrategyRegistryError):
    """A revision named a parent that is not in the registry.

    Fail closed rather than registering it as an original: a revision whose
    parent silently vanishes becomes a fresh lineage with an attempt count of
    one, which is the exact number the search discipline exists to get right.
    """


class LineageError(StrategyRegistryError):
    """A revision is missing something a revision must have."""


def _validate_lineage(record: StrategyRecord) -> None:
    """A revision must say what it came from and why.

    ``revision_reason`` is mandatory, not decorative. It is the only field that
    lets a later reader distinguish "momentum crashed in 2022, so this one stands
    down after a drawdown" from "lookback 126 -> 100" — and those are the same
    row otherwise. If the proposer cannot articulate a reason, the revision has
    not earned a place in the lineage.
    """

    if record.parent_strategy_id is None:
        if record.revision_reason is not None or record.derived_from_evaluation_id is not None:
            raise LineageError(
                "revision_reason/derived_from_evaluation_id require a parent_strategy_id; "
                "an original has nothing to be a revision of"
            )
        return
    if record.parent_strategy_id == record.strategy_id:
        raise LineageError(f"strategy {record.strategy_id!r} cannot be its own parent")
    if not (record.revision_reason or "").strip():
        raise LineageError(
            f"revision {record.strategy_id!r} must state a revision_reason — "
            "an unexplained revision is indistinguishable from a parameter sweep"
        )


class InvalidTransitionError(StrategyRegistryError):
    """The requested lifecycle transition is not allowed by the state machine.

    Carries the statuses involved as structured fields, not only in the message.
    Consumers need to tell two very different rejections apart — a registry that
    is *already at the requested stage* (convergence, expected whenever a second
    consumer replays a verdict its producer already applied) versus a genuinely
    illegal move. The Strategy Librarian branches on exactly that, and doing it
    by parsing the message string would break silently the first time the
    wording changed.
    """

    def __init__(
        self,
        message: str,
        *,
        strategy_id: str | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
        expected_from: str | None = None,
    ) -> None:
        super().__init__(message)
        self.strategy_id = strategy_id
        self.from_status = from_status
        self.to_status = to_status
        self.expected_from = expected_from

    @property
    def already_at_target(self) -> bool:
        """True when the registry already sits at the stage the caller wanted.

        Covers both rejection paths: an ``expected_from`` mismatch where the
        current status is the target, and a state-machine rejection of a
        no-op move out of a terminal status.
        """

        return (
            self.from_status is not None
            and self.to_status is not None
            and self.from_status == self.to_status
        )


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
    account_id: str | None = None
    """Broker account this strategy trades in (ADR-0017). None = unassigned,
    which is a real state: nothing has been allocated to it yet."""

    parent_strategy_id: str | None = None
    """The strategy this was revised from. ``None`` means an original idea."""

    lineage_root_id: str | None = None
    """The originating ancestor. Set by :meth:`register` from the parent, never
    by the caller — a proposer that could choose its own root could reset its
    own attempt count, which is the one number it must not control."""

    derived_from_evaluation_id: str | None = None
    """The evaluation that motivated this revision, so the evidence behind a
    proposal is auditable rather than asserted."""

    revision_reason: str | None = None
    """Why this revision exists, in words. Required for any revision: it is what
    lets a person read the lineage later and recognise a parameter sweep."""

    @property
    def is_revision(self) -> bool:
        return self.parent_strategy_id is not None


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
            await conn.execute(ALTER_STRATEGIES_ADD_ACCOUNT_ID_SQL)
            await conn.execute(ALTER_STRATEGIES_ADD_LINEAGE_SQL)
            # Order matters: add the columns, then adopt every pre-lineage row
            # as its own root, then index. Backfilling after the index would
            # still be correct but rewrites it needlessly.
            await conn.execute(BACKFILL_STRATEGIES_LINEAGE_ROOT_SQL)
            await conn.execute(CREATE_STRATEGIES_LINEAGE_INDEX_SQL)
            await conn.execute(CREATE_STRATEGIES_ACCOUNT_UNIQUE_INDEX_SQL)
            await conn.execute(CREATE_STRATEGIES_SPEC_HASH_INDEX_SQL)
            await conn.execute(CREATE_STRATEGIES_STATUS_INDEX_SQL)
            await conn.execute(CREATE_TRANSITIONS_TABLE_SQL)
            await conn.execute(CREATE_TRANSITIONS_STRATEGY_INDEX_SQL)

    async def lineage(self, strategy_id: str) -> list[StrategyRecord]:
        """Every strategy sharing this one's originating ancestor, oldest first.

        Takes any member and returns the whole family, so a caller does not have
        to already know the root — the useful question is almost always "what
        else has been tried on this idea", asked from whichever attempt is in
        hand.
        """

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_STRATEGY_SQL, strategy_id)
            if row is None:
                return []
            root = str(row["lineage_root_id"] or row["strategy_id"])
            rows = await conn.fetch(SELECT_LINEAGE_SQL, root)
        return [_record_from_row(r) for r in rows]

    async def attempts(self, strategy_id: str) -> int:
        """How many strategies this idea has burned, including the original.

        The multiple-testing denominator. A lineage on attempt 20 that finally
        clears an information ratio of 0.5 has not found edge — it has found the
        best of twenty draws, and the gate cannot know that without this number.
        Reported rather than enforced: what the gate should DO about a long
        lineage is a calibration, and calibrations are Mike's.
        """

        return len(await self.lineage(strategy_id))

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
        _validate_lineage(record)
        occurred_at = datetime.now(UTC)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # The root is resolved from the PARENT ROW, never from the
                # record. A proposer that could nominate its own root could
                # reset its own attempt count, and the attempt count is the one
                # number a proposer must not be able to influence.
                lineage_root = record.strategy_id
                if record.parent_strategy_id is not None:
                    parent = await conn.fetchrow(SELECT_STRATEGY_SQL, record.parent_strategy_id)
                    if parent is None:
                        raise UnknownParentError(
                            f"parent strategy {record.parent_strategy_id!r} does not exist; "
                            "a revision must descend from a registered strategy"
                        )
                    lineage_root = str(parent["lineage_root_id"] or parent["strategy_id"])
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
                    record.parent_strategy_id,
                    lineage_root,
                    record.derived_from_evaluation_id,
                    record.revision_reason,
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

    async def list_all(self) -> list[StrategyRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(SELECT_ALL_STRATEGIES_SQL)
        return [_record_from_row(row) for row in rows]

    async def assign_account(self, strategy_id: str, account_id: str | None) -> StrategyRecord:
        """Assign this strategy to a broker account, or clear it with ``None``.

        One strategy per account (ADR-0017) is enforced by a partial unique index
        rather than by a check here, so a race between two assignments loses at
        the database instead of producing two strategies trading one book —
        which would silently destroy the property that makes the account's
        equity curve a single strategy's P&L.

        Clearing is allowed and is how an account is freed for the next
        promotion: a strategy's positions outlive its assignment, so clearing is
        a decision about *future* orders, not a claim that the account is flat.
        """

        normalized = account_id.strip() if account_id is not None else None
        if normalized == "":
            normalized = None
        async with self._pool.acquire() as conn:
            await conn.execute(UPDATE_STRATEGY_ACCOUNT_SQL, strategy_id, normalized)
            row = await conn.fetchrow(SELECT_STRATEGY_SQL, strategy_id)
        if row is None:
            raise StrategyNotFoundError(f"strategy {strategy_id} does not exist")
        return _record_from_row(row)

    async def get_by_spec_hash(self, spec_hash: str) -> StrategyRecord | None:
        """Look up a strategy by its unique ``spec_hash`` (the dedup key)."""

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_STRATEGY_BY_SPEC_HASH_SQL, spec_hash)
        if row is None:
            return None
        return _record_from_row(row)

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
                        f"{strategy_id}: expected status {expected_from!r}, found {from_status!r}",
                        strategy_id=strategy_id,
                        from_status=from_status,
                        to_status=to_status,
                        expected_from=expected_from,
                    )
                if to_status not in ALLOWED_TRANSITIONS.get(from_status, frozenset()):
                    raise InvalidTransitionError(
                        f"{strategy_id}: {from_status!r} -> {to_status!r} is not allowed",
                        strategy_id=strategy_id,
                        from_status=from_status,
                        to_status=to_status,
                        expected_from=expected_from,
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
        account_id=None if row["account_id"] is None else str(row["account_id"]),
        parent_strategy_id=_opt_str(row, "parent_strategy_id"),
        lineage_root_id=_opt_str(row, "lineage_root_id"),
        derived_from_evaluation_id=_opt_str(row, "derived_from_evaluation_id"),
        revision_reason=_opt_str(row, "revision_reason"),
    )


def _opt_str(row: Mapping[str, Any], key: str) -> str | None:
    """Tolerates a row selected before the lineage columns existed.

    `.get` rather than `[]` because a stale SELECT that omits these would
    otherwise raise at read time on a running deploy rather than simply
    reporting no lineage.
    """

    value = row.get(key)
    return None if value is None else str(value)


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
