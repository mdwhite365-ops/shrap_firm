"""``shrap-risk-switch`` — read the kill switches, and set or clear one by hand.

    shrap-risk-switch show
    shrap-risk-switch clear strategy:<id> --reason "reviewed: one bad week, not the rule"
    shrap-risk-switch set manual --reason "halting while the broker migrates"

**Why this exists.** ``docs/risk/policy.md`` says Mike can clear any switch, and
the Risk Officer has carried ``set_switch`` and ``clear_switch`` since it
shipped. Nothing called either. The only way to release a strategy's drawdown
switch was an ``INSERT`` into ``risk.kill_switches`` and an ``HSET`` in Redis,
in that order, by hand, and the board's ``manual`` switch could not be set at
all. A switch that "waits for a human ruling" needs a way for the human to rule.

It goes through the same :class:`RiskOfficer` methods the monitor uses, so a
transition is written to Postgres first and Redis second, exactly once, and is
published on ``risk.kill_switch.set`` / ``.clear`` like any other. The running
Pre-Trade Checker reads the Redis board on every intent, so a change here takes
effect on the next order with no restart.

Every transition is stamped ``actor=human``. That matters twice: the sweep never
auto-clears a ``daily_loss`` switch a human set, and a cleared strategy switch
restarts that strategy's drawdown measurement at the clear (see
:func:`~shrap.risk_compliance.risk_officer.officer.drawdown_released_at`).

Run it where the service runs, so it reads the same Redis and Postgres::

    docker compose exec pre-trade-checker shrap-risk-switch show
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from shrap.risk_compliance.risk_officer.switch_store import SwitchStateUnavailable
from shrap.risk_compliance.risk_officer.switches import (
    ACTOR_HUMAN,
    SWITCH_DAILY_LOSS,
    SWITCH_MANUAL,
    SwitchState,
    SwitchTransition,
    is_strategy_switch,
    strategy_id_of,
)

PRODUCED_BY = "risk/risk-switch-cli"
SCHEMA_VERSION = "1.0.0"
# The same streams `monitor_once` publishes to. Named here rather than imported
# so reading the board does not import the whole order-path agent.
STREAM_KILL_SWITCH_SET = "risk.kill_switch.set"
STREAM_KILL_SWITCH_CLEAR = "risk.kill_switch.clear"


class SwitchOfficer(Protocol):
    """The slice of :class:`RiskOfficer` this command drives."""

    async def set_switch(
        self, name: str, *, actor: str, reason: str, now: datetime
    ) -> SwitchTransition | None: ...

    async def clear_switch(
        self, name: str, *, actor: str, reason: str, now: datetime
    ) -> SwitchTransition | None: ...


class Publisher(Protocol):
    async def publish(
        self, stream: str, produced_by: str, schema_version: str, payload: dict[str, Any]
    ) -> Any: ...


def validate_switch_name(name: str) -> str:
    """The switch name as the board spells it, or ``ValueError``.

    A typo must not create a switch. ``strategy:01ABC`` with a stray space, or
    ``daily-loss`` for ``daily_loss``, would be written, audited and published,
    and would halt nothing — which reads as a halt that is in force.
    """

    cleaned = name.strip()
    if cleaned in (SWITCH_MANUAL, SWITCH_DAILY_LOSS):
        return cleaned
    strategy_id = strategy_id_of(cleaned)
    if strategy_id and not any(ch.isspace() for ch in strategy_id):
        return cleaned
    raise ValueError(
        f"{name!r} is not a switch. Use '{SWITCH_MANUAL}', '{SWITCH_DAILY_LOSS}' or "
        "'strategy:<strategy_id>'."
    )


def _utc(at: datetime) -> datetime:
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


def render_board(
    log_states: Sequence[SwitchState],
    live_states: Sequence[SwitchState] | None,
    now: datetime,
) -> str:
    """The board as Postgres records it, and whether Redis agrees.

    Postgres is the authority and Redis is what the order path actually reads,
    so a disagreement between them is the one thing here worth shouting about:
    it means the firm is enforcing a board that is not the recorded one.
    ``live_states`` is ``None`` when Redis could not be read, which the order
    path treats as a halt.
    """

    lines: list[str] = []
    if not log_states:
        lines.append("no switch has ever been set (risk.kill_switches is empty)")
    for state in sorted(log_states, key=lambda s: (not s.active, s.name)):
        age_hours = (_utc(now) - _utc(state.at)).total_seconds() / 3600.0
        lines.append(
            f"{'ACTIVE ' if state.active else 'clear  '} {state.name}\n"
            f"         {'set' if state.active else 'cleared'} "
            f"{_utc(state.at).isoformat(timespec='seconds')} ({age_hours:.1f}h ago) "
            f"by {state.actor}\n"
            f"         {state.reason}"
        )
    if live_states is None:
        lines.append(
            "\nREDIS UNREADABLE: the order path cannot read risk:switches and is "
            "refusing every intent (RISK_STATE_UNAVAILABLE)."
        )
        return "\n".join(lines)
    recorded = {s.name: s.active for s in log_states}
    live = {s.name: s.active for s in live_states}
    for name in sorted(set(recorded) | set(live)):
        if recorded.get(name, False) != live.get(name, False):
            lines.append(
                f"\nDISAGREEMENT on {name}: Postgres says "
                f"{'active' if recorded.get(name, False) else 'clear'}, Redis says "
                f"{'active' if live.get(name, False) else 'clear'}. Redis is what is "
                "enforced. Restarting pre-trade-checker rebuilds it from Postgres."
            )
    return "\n".join(lines)


async def change_switch(
    officer: SwitchOfficer,
    publisher: Publisher | None,
    *,
    action: str,
    name: str,
    reason: str,
    now: datetime,
) -> str:
    """Set or clear one switch as a human, and say what happened.

    A no-op is reported as one rather than as success: clearing a switch that
    is not active changes nothing, writes nothing, and must not read as though
    a halt was just lifted.
    """

    switch = validate_switch_name(name)
    if not reason.strip():
        raise ValueError("--reason is required: it is the audit row's only explanation")
    if action == "set":
        transition = await officer.set_switch(
            switch, actor=ACTOR_HUMAN, reason=reason.strip(), now=now
        )
    elif action == "clear":
        transition = await officer.clear_switch(
            switch, actor=ACTOR_HUMAN, reason=reason.strip(), now=now
        )
    else:
        raise ValueError(f"unknown action {action!r}")
    if transition is None:
        state = "already active" if action == "set" else "not active"
        return f"{switch} is {state}; nothing changed and nothing was written."
    if publisher is not None:
        await publisher.publish(
            stream=STREAM_KILL_SWITCH_SET if transition.was_set else STREAM_KILL_SWITCH_CLEAR,
            produced_by=PRODUCED_BY,
            schema_version=SCHEMA_VERSION,
            payload=transition.state.to_payload(),
        )
    verb = "set" if transition.was_set else "cleared"
    note = ""
    if not transition.was_set and is_strategy_switch(switch):
        note = (
            "\nThe drawdown limit for this strategy is now measured from this "
            "moment: the full limit again, from the equity it has now."
        )
    return f"{switch} {verb} by {ACTOR_HUMAN}: {reason.strip()}{note}"


async def _run(args: argparse.Namespace) -> str:
    # Imported here so `--help` and the pure functions above need neither a
    # database driver nor the service's settings.
    from redis.asyncio import Redis

    from shrap.agents.risk_compliance.pre_trade_checker.config import Settings
    from shrap.common.db import create_asyncpg_pool
    from shrap.events import EventPublisher, RedisPublisher
    from shrap.research.strategy_registry import PostgresStrategyRegistry
    from shrap.risk_compliance.risk_officer.officer import RiskOfficer
    from shrap.risk_compliance.risk_officer.store import RiskStore
    from shrap.risk_compliance.risk_officer.switch_store import RedisSwitchStore, SwitchRedis

    settings = Settings()
    redis: Redis = Redis.from_url(
        args.redis_url or settings.redis_url, decode_responses=True, socket_timeout=30
    )
    pool = await create_asyncpg_pool(args.dsn or settings.postgres_dsn_value())
    try:
        store = RiskStore(pool)
        switch_store = RedisSwitchStore(cast(SwitchRedis, redis))
        now = datetime.now(UTC)
        if args.action == "show":
            log_states = await store.load_switch_states()
            try:
                live: Sequence[SwitchState] | None = tuple(
                    (await switch_store.load()).states.values()
                )
            except SwitchStateUnavailable:
                live = None
            return render_board(log_states, live, now)
        officer = RiskOfficer(
            store=store,
            switch_store=switch_store,
            registry=PostgresStrategyRegistry(pool),
        )
        publisher = None if args.no_publish else EventPublisher(cast(RedisPublisher, redis))
        return await change_switch(
            officer,
            publisher,
            action=args.action,
            name=args.switch,
            reason=args.reason,
            now=now,
        )
    finally:
        await redis.aclose()
        await pool.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read the Risk Officer's kill switches, or set/clear one by hand. "
            "Stamped actor=human and written to risk.kill_switches. Clearing a "
            "switch lets new buys through on the next order."
        )
    )
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: the service's)")
    parser.add_argument("--redis-url", default=None, help="Redis URL (default: the service's)")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("show", help="Every switch: state, who, when, why; and whether Redis agrees")
    for action, text in (
        ("set", "Activate a switch (halts new positions, never exits)"),
        ("clear", "Deactivate a switch"),
    ):
        cmd = sub.add_parser(action, help=text)
        cmd.add_argument("switch", help="manual | daily_loss | strategy:<strategy_id>")
        cmd.add_argument(
            "--reason", required=True, help="Why. Recorded permanently in risk.kill_switches."
        )
        cmd.add_argument(
            "--no-publish",
            action="store_true",
            help="Skip the risk.kill_switch event (the audit row is still written)",
        )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.action != "show":
        # Before anything connects: a typo should cost nothing.
        try:
            validate_switch_name(args.switch)
        except ValueError as exc:
            parser.error(str(exc))
    try:
        print(asyncio.run(_run(args)))
    except (ValueError, SwitchStateUnavailable) as exc:
        raise SystemExit(f"refused: {exc}") from exc


if __name__ == "__main__":
    main()


__all__ = [
    "PRODUCED_BY",
    "STREAM_KILL_SWITCH_CLEAR",
    "STREAM_KILL_SWITCH_SET",
    "change_switch",
    "main",
    "render_board",
    "validate_switch_name",
]
