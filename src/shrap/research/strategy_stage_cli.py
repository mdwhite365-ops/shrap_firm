"""``shrap-strategy-stage`` — move a strategy through its lifecycle by hand.

Until now the **only** way a strategy could change stage was a verdict: the
Evaluator's ``commit()`` transitions it, or the Librarian applies the verdict
event. There was no human path at all, which meant two things were impossible:
recovering a strategy that a bad run terminated, and putting a strategy into
paper trading as a deliberate systems test rather than as a claim of edge.

Those are different acts and the transition log should say which one happened.
Every move made here is stamped ``actor="mike"`` and ``trigger_kind="manual"``,
against the Evaluator's ``actor="strategy-evaluator"`` /
``trigger_kind="evaluation"``. A later reader can separate "the firm decided
this" from "a person decided this" by filtering one column — which is the whole
reason the transition table is append-only.

**A promotion here is not a verdict.** ``paper``, ``small-size-paper`` and
``live-paper`` are stages the Strategy Runner emits live orders for; the Runner
reads the stage and never asks how the strategy got there. Moving a strategy
into one of them starts real paper trading on the next market open. The CLI says
so, requires an explicit reason, and refuses to guess.

Subcommands::

    shrap-strategy-stage show <strategy_id>
    shrap-strategy-stage move <strategy_id> --to paper --reason "..." [--dry-run]
    shrap-strategy-stage assign-account <strategy_id> --account-id PA3ABCDEF
    shrap-strategy-stage assign-account <strategy_id> --clear

On the ``shrap-universe-promote`` / ``shrap-tech-watcher-promote`` precedent:
plain argparse, env-var DSN default, no long-running loop.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from redis.asyncio import Redis

from shrap.common.db import create_asyncpg_pool
from shrap.events import EventPublisher
from shrap.research.strategy_evaluator.pipeline import DEFERRED_RULES, _rule_name
from shrap.research.strategy_registry import (
    ALLOWED_TRANSITIONS,
    STATUS_LIVE_PAPER,
    STATUS_PAPER,
    STATUS_SMALL_SIZE_PAPER,
    PostgresStrategyRegistry,
    StrategyRecord,
    StrategyTransition,
    stream_for_transition,
    transition_event_payload,
)

MANUAL_ACTOR = "mike"
MANUAL_TRIGGER_KIND = "manual"
PRODUCED_BY = "strategy-stage-cli"
SCHEMA_VERSION = "1.0.0"

# Stages the Strategy Runner emits live orders for. Kept in step with the
# runner's own ACTIVE_PAPER_STAGES; a move into any of them starts trading.
TRADING_STAGES: frozenset[str] = frozenset(
    {STATUS_PAPER, STATUS_SMALL_SIZE_PAPER, STATUS_LIVE_PAPER}
)

UNEVALUATED_REFUSAL = (
    "refused: {sid} uses rule {rule!r}, which spec hygiene defers — the Evaluator "
    "will not measure it, so this strategy has never been and cannot currently be "
    "evaluated.\n  reason it is deferred: {why}\n"
    "The Strategy Runner does NOT check deferred rules; it trades whatever sits at a "
    "trading stage. Promoting this would put an unevaluable rule into live paper "
    "orders.\n"
    "If that is deliberate — a systems test rather than a claim of edge — re-run with "
    "--acknowledge-unevaluated, which records the acknowledgement in the transition "
    "reason so the log says what was known at the time."
)

TRADING_WARNING = (
    "This stage TRADES. The Strategy Runner emits live paper orders for it on the "
    "next market open, and it does not ask why the strategy is at this stage. If "
    "the strategy has not earned a promotion, say so in --reason so the transition "
    "log is honest about what this was."
)


class RegistryPort(Protocol):
    """The registry surface this CLI needs (structural — Postgres or fake)."""

    async def ensure_schema(self) -> None: ...

    async def get(self, strategy_id: str) -> StrategyRecord | None: ...

    async def transitions(self, strategy_id: str) -> Sequence[StrategyTransition]: ...

    async def transition(
        self,
        strategy_id: str,
        to_status: str,
        *,
        reason: str,
        trigger_kind: str,
        actor: str,
        trigger_ref: str | None = ...,
        expected_from: str | None = ...,
    ) -> StrategyTransition: ...


class PublisherPort(Protocol):
    async def publish(
        self,
        *,
        stream: str,
        produced_by: str,
        schema_version: str,
        payload: dict[str, object],
    ) -> object: ...


def render_lineage(records: Sequence[StrategyRecord], asked_for: str) -> str:
    """Every attempt on one idea, oldest first, with the search count.

    The point of showing the whole family rather than one row: a promote
    decision on attempt 12 reads very differently from the same numbers on
    attempt 1, and nothing else in the firm's output carries that context.
    """

    if not records:
        return f"no strategy {asked_for!r}"
    root = records[0].lineage_root_id or records[0].strategy_id
    lines = [
        f"Lineage {root}",
        f"Attempts: {len(records)}",
        "",
    ]
    if len(records) > 1:
        lines += [
            "> An information ratio clearing the floor on attempt N is the best of "
            "N draws, not evidence of edge in the Nth. Read the reasons below and "
            "judge whether these are distinct hypotheses or one hypothesis being "
            "tuned.",
            "",
        ]
    by_parent: dict[str | None, list[StrategyRecord]] = {}
    for record in records:
        by_parent.setdefault(record.parent_strategy_id, []).append(record)

    def walk(parent: str | None, depth: int) -> None:
        for record in by_parent.get(parent, []):
            marker = "  " * depth + ("+- " if depth else "")
            account = record.account_id or "unassigned"
            lines.append(
                f"{marker}{record.strategy_id} [{record.status}] "
                f"<{record.archetype}> account={account}"
            )
            lines.append(f"{'  ' * depth}   {record.name}")
            if record.revision_reason:
                lines.append(f"{'  ' * depth}   revised because: {record.revision_reason}")
            if record.derived_from_evaluation_id:
                lines.append(
                    f"{'  ' * depth}   evidence: evaluation {record.derived_from_evaluation_id}"
                )
            walk(record.strategy_id, depth + 1)

    # Roots first. Anything whose parent is outside this result set would be
    # unreachable from a pure parent==None walk, so orphans are surfaced rather
    # than silently dropped — a lineage that hides a member miscounts the search.
    known = {r.strategy_id for r in records}
    walk(None, 0)
    for parent, children in by_parent.items():
        if parent is not None and parent not in known:
            for record in children:
                lines.append(f"{record.strategy_id} [{record.status}] (parent {parent} not found)")
    return "\n".join(lines)


def render_show(record: StrategyRecord, history: Sequence[StrategyTransition]) -> str:
    """Current stage plus the full transition history, oldest first."""

    # The account is shown even when unset, and named as the reason it will not
    # trade. An assignment you cannot read back is not verifiable, and a strategy
    # at a trading stage with no account is silently inert — the Runner logs it
    # once a session and moves on.
    account = record.account_id or "(unassigned — will NOT trade)"
    lines = [
        f"{record.strategy_id}  {record.name}",
        f"  archetype : {record.archetype}",
        f"  stage     : {record.status}",
        f"  account   : {account}",
        f"  tickers   : {record.tickers}",
        "",
        f"Transitions: {len(history)}",
    ]
    for t in history:
        # StrategyTransition.occurred_at is typed `object` on the dataclass, so
        # this narrows rather than assuming. Widening that annotation is worth
        # doing, but not inside a card that only reads it.
        stamp = t.occurred_at
        when = stamp.isoformat() if isinstance(stamp, datetime) else str(stamp or "?")
        lines.append(
            f"  {when}  {t.from_status or '-'} -> {t.to_status}  "
            f"[{t.trigger_kind} by {t.actor}]  {t.reason}"
        )
    allowed = sorted(ALLOWED_TRANSITIONS.get(record.status, frozenset()))
    lines += ["", f"Allowed next: {', '.join(allowed) if allowed else '(terminal)'}"]
    return "\n".join(lines)


async def move_stage(
    registry: RegistryPort,
    publisher: PublisherPort | None,
    strategy_id: str,
    *,
    to_stage: str,
    reason: str,
    dry_run: bool = False,
    acknowledge_unevaluated: bool = False,
) -> str:
    """Transition one strategy, or explain why it cannot move.

    Reads the record first so the refusal messages name the actual current
    stage. The registry re-checks under a row lock, so this read is for the
    operator's benefit, not for correctness.
    """

    record = await registry.get(strategy_id)
    if record is None:
        raise SystemExit(f"refused: no strategy {strategy_id!r} in research.strategies")
    if record.status == to_stage:
        return f"no-op: {strategy_id} is already at {to_stage!r}"
    allowed = ALLOWED_TRANSITIONS.get(record.status, frozenset())
    if to_stage not in allowed:
        options = ", ".join(sorted(allowed)) if allowed else "(terminal — no moves out)"
        raise SystemExit(
            f"refused: {record.status!r} -> {to_stage!r} is not a legal transition. "
            f"From {record.status!r} the state machine allows: {options}"
        )

    # The Runner reads only the stage, so a rule the Evaluator refuses to measure
    # would still trade once promoted. This is the one place that asymmetry can be
    # caught by a human, so it is caught here rather than left implicit.
    rule = _rule_name(record.spec)
    deferred = DEFERRED_RULES.get(rule)
    if to_stage in TRADING_STAGES and deferred is not None and not acknowledge_unevaluated:
        raise SystemExit(UNEVALUATED_REFUSAL.format(sid=strategy_id, rule=rule, why=deferred))
    if deferred is not None and acknowledge_unevaluated:
        reason = f"{reason} [acknowledged unevaluated: rule {rule!r} is deferred — {deferred}]"

    prefix = "DRY RUN — " if dry_run else ""
    warning = f"\n{TRADING_WARNING}" if to_stage in TRADING_STAGES else ""
    if dry_run:
        return (
            f"{prefix}would move {strategy_id} ({record.name}) "
            f"{record.status} -> {to_stage}\n"
            f"  reason: {reason}\n"
            f"  actor : {MANUAL_ACTOR} (trigger_kind={MANUAL_TRIGGER_KIND})"
            f"{warning}\n"
            "Nothing written."
        )

    transition = await registry.transition(
        strategy_id,
        to_stage,
        reason=reason,
        trigger_kind=MANUAL_TRIGGER_KIND,
        actor=MANUAL_ACTOR,
        expected_from=record.status,
    )

    published = ""
    if publisher is not None:
        # The registry does not publish and the Librarian only reacts to
        # verdicts, so without this a manual move would be invisible on the bus
        # while every automated one is visible. "Audit everything" (principle 8)
        # does not have a carve-out for decisions a human made.
        stream = stream_for_transition(transition.from_status, transition.to_status)
        await publisher.publish(
            stream=stream,
            produced_by=PRODUCED_BY,
            schema_version=SCHEMA_VERSION,
            payload=dict(transition_event_payload(transition)),
        )
        published = f"\n  published: {stream}"

    return (
        f"moved: {strategy_id} ({record.name}) "
        f"{transition.from_status} -> {transition.to_status}\n"
        f"  reason: {reason}\n"
        f"  actor : {MANUAL_ACTOR} (trigger_kind={MANUAL_TRIGGER_KIND}){published}"
        f"{warning}"
    )


def _default_dsn() -> str:
    return (
        os.environ.get("STRATEGY_SEED_POSTGRES_DSN")
        or os.environ.get("STRATEGY_EVALUATOR_POSTGRES_DSN")
        or "postgresql://shrap:shrap@postgres:5432/shrap"
    )


def _default_redis_url() -> str:
    return os.environ.get("STRATEGY_EVALUATOR_REDIS_URL") or "redis://redis:6379/0"


async def assign_account(
    registry: PostgresStrategyRegistry,
    strategy_id: str,
    *,
    account_id: str | None,
    dry_run: bool = False,
) -> str:
    """Bind a strategy to a broker account, or free the account with ``None``.

    ADR-0017: one strategy per account, so the account's equity curve is that
    strategy's P&L. The database enforces uniqueness; this reports the outcome
    in the terms a human is deciding in.
    """

    record = await registry.get(strategy_id)
    if record is None:
        raise SystemExit(f"refused: no strategy {strategy_id!r}")

    current = record.account_id or "(unassigned)"
    target = account_id or "(unassigned)"
    if record.account_id == account_id:
        return f"no change: {record.name} is already {target}"

    if dry_run:
        return (
            f"DRY RUN — would move {record.name} ({strategy_id}) from account {current} to {target}"
        )

    updated = await registry.assign_account(strategy_id, account_id)
    now = updated.account_id or "(unassigned)"
    lines = [f"{record.name} ({strategy_id}): account {current} -> {now}"]
    if updated.account_id is not None:
        # This used to say "set STRATEGY_RUNNER_ACCOUNT_ID to this value", which
        # was correct when one runner served one account and false from ADR-0017
        # onward: the runner groups strategies by the account on their registry
        # row, one instance serves every account, and nothing reads that
        # variable any more. It survived because the CLI's own output is not
        # something a test or a type checker reads — an operator following it
        # would set a dead env var and believe an account had been configured.
        lines.append(
            "The Strategy Runner reads this from research.strategies (ADR-0017) — "
            "one runner serves every account and no env var needs setting. It "
            "takes effect on the next pass."
        )
    else:
        lines.append(
            "Account freed. Positions opened under the old assignment are still "
            "held at the broker — clearing decides future orders, not current "
            "holdings."
        )
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> str:
    pool = await create_asyncpg_pool(args.dsn)
    redis: Redis | None = None
    try:
        registry = PostgresStrategyRegistry(pool)
        await registry.ensure_schema()
        if args.action == "show":
            record = await registry.get(args.strategy_id)
            if record is None:
                raise SystemExit(f"refused: no strategy {args.strategy_id!r}")
            return render_show(record, await registry.transitions(args.strategy_id))

        if args.action == "lineage":
            return render_lineage(await registry.lineage(args.strategy_id), args.strategy_id)

        if args.action == "assign-account":
            return await assign_account(
                registry,
                args.strategy_id,
                account_id=None if args.clear else args.account_id,
                dry_run=args.dry_run,
            )

        publisher: PublisherPort | None = None
        if not args.dry_run and not args.no_publish:
            redis = Redis.from_url(args.redis_url, decode_responses=True)
            publisher = EventPublisher(redis)  # type: ignore[arg-type]
        return await move_stage(
            registry,
            publisher,
            args.strategy_id,
            to_stage=args.to,
            reason=args.reason,
            dry_run=args.dry_run,
            acknowledge_unevaluated=args.acknowledge_unevaluated,
        )
    finally:
        if redis is not None:
            await redis.aclose()
        await pool.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Move a strategy through its lifecycle by hand. Stamped actor=mike / "
            "trigger_kind=manual so the transition log distinguishes a human "
            "decision from a verdict. Moving into paper/small-size-paper/live-paper "
            "starts real paper trading on the next market open."
        )
    )
    parser.add_argument("--dsn", default=_default_dsn(), help="Postgres DSN")
    parser.add_argument("--redis-url", default=_default_redis_url(), help="Redis URL")
    sub = parser.add_subparsers(dest="action", required=True)

    show = sub.add_parser("show", help="Current stage, transition history, legal next moves")
    show.add_argument("strategy_id")

    lineage = sub.add_parser(
        "lineage",
        help="Every attempt on this strategy's idea, and how many there have been",
    )
    lineage.add_argument("strategy_id")

    assign = sub.add_parser(
        "assign-account",
        help="Bind a strategy to a broker account, or free the account with --clear",
    )
    assign.add_argument("strategy_id")
    assign.add_argument(
        "--account-id",
        default=None,
        help="Broker account number (Alpaca account_number). One strategy per account.",
    )
    assign.add_argument(
        "--clear",
        action="store_true",
        help="Unassign, freeing the account for another strategy",
    )
    assign.add_argument(
        "--dry-run", action="store_true", help="Show what would happen; write nothing"
    )

    move = sub.add_parser("move", help="Transition a strategy to another stage")
    move.add_argument("strategy_id")
    move.add_argument(
        "--to",
        required=True,
        choices=sorted(ALLOWED_TRANSITIONS),
        help="Target stage",
    )
    move.add_argument(
        "--reason",
        required=True,
        help="Why. Recorded permanently in research.strategy_transitions.",
    )
    move.add_argument(
        "--dry-run", action="store_true", help="Show what would happen; write nothing"
    )
    move.add_argument(
        "--acknowledge-unevaluated",
        action="store_true",
        help=(
            "Permit a trading-stage move for a rule the Evaluator defers. The "
            "acknowledgement is appended to the recorded reason."
        ),
    )
    move.add_argument(
        "--no-publish",
        action="store_true",
        help="Skip the lifecycle event (transition row is still written)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.action == "show":
        args.dry_run = False
        args.no_publish = True
        args.acknowledge_unevaluated = False
    if args.action == "assign-account" and not args.clear and not args.account_id:
        parser.error("assign-account needs --account-id or --clear")
    print(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()


__all__ = [
    "MANUAL_ACTOR",
    "MANUAL_TRIGGER_KIND",
    "PRODUCED_BY",
    "TRADING_STAGES",
    "TRADING_WARNING",
    "UNEVALUATED_REFUSAL",
    "assign_account",
    "main",
    "move_stage",
    "render_show",
]
