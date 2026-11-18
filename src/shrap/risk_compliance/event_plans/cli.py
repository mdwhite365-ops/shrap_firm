"""``shrap-event-plan`` — write, read and cancel event plans (ADR-0018).

    shrap-event-plan show
    shrap-event-plan add-window --name "FOMC Dec" --kind fed \\
        --start 2026-12-08T16:00-05:00 --end 2026-12-09T16:00-05:00 \\
        --scale 0.5 --reason "decision at 14:00 ET on the 9th"
    shrap-event-plan add-trigger --name "SPY down 3%" --kind shock \\
        --ticker SPY --threshold -0.03 --hold-hours 48 --pause \\
        --armed-days 180 --reason "stand aside for two days after a 3% fall"
    shrap-event-plan cancel <plan_id>

Plans are hand-written here: no model drafts one (that is card 7). A plan may
only scale new buys down or pause them, and every one has an end time. Run it
where the Pre-Trade Checker runs::

    docker compose exec pre-trade-checker shrap-event-plan show

**A stored plan does nothing unless ``PRE_TRADE_CHECKER_EVENT_PLANS_ENFORCEMENT``
is true.** ``show`` says which is the case it can see, and cannot see the
service's environment: check the service's startup log for
``risk_officer.event_plans_on``.

``scale-buys`` also reaches held positions, indirectly. The Runner resizes
holdings toward the scale the Risk Officer recorded on the account's newest
approved buy (#277), and that recorded scale now includes the plan. So once one
buy is approved under a plan, holdings drift toward the smaller target; once one
is approved after it ends, they drift back. ``pause-buys`` approves no buy and
so moves nothing.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from ulid import ULID

from shrap.risk_compliance.event_plans.plans import (
    ACTION_PAUSE_BUYS,
    ACTION_SCALE_BUYS,
    TRIGGER_DAILY_RETURN,
    TRIGGER_WINDOW,
    Activation,
    Plan,
    PlanError,
    effect_at,
    validate,
)

CREATED_BY = "human"


def _utc(at: datetime) -> datetime:
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


def parse_instant(text: str) -> datetime:
    """An ISO timestamp that must say what zone it is in.

    A naive time would be read as UTC, and "16:00" typed by someone thinking in
    New York would start the plan four or five hours early.
    """

    try:
        value = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{text!r} is not an ISO timestamp") from exc
    if value.tzinfo is None:
        raise argparse.ArgumentTypeError(
            f"{text!r} has no UTC offset; write it as 2026-12-09T16:00-05:00"
        )
    return value


def build_window_plan(
    *,
    name: str,
    kind: str,
    start: datetime,
    end: datetime,
    scale: float | None,
    pause: bool,
    reason: str,
    plan_id: str | None = None,
) -> Plan:
    return validate(
        Plan(
            plan_id=plan_id or str(ULID()),
            name=name,
            event_kind=kind,
            trigger=TRIGGER_WINDOW,
            action=ACTION_PAUSE_BUYS if pause else ACTION_SCALE_BUYS,
            multiplier=_multiplier(scale, pause),
            starts_at=start,
            expires_at=end,
            reason=reason,
            created_by=CREATED_BY,
        )
    )


def build_trigger_plan(
    *,
    name: str,
    kind: str,
    ticker: str,
    threshold: float,
    hold_hours: float,
    armed_days: float,
    scale: float | None,
    pause: bool,
    reason: str,
    now: datetime,
    plan_id: str | None = None,
) -> Plan:
    return validate(
        Plan(
            plan_id=plan_id or str(ULID()),
            name=name,
            event_kind=kind,
            trigger=TRIGGER_DAILY_RETURN,
            action=ACTION_PAUSE_BUYS if pause else ACTION_SCALE_BUYS,
            multiplier=_multiplier(scale, pause),
            starts_at=now,
            expires_at=now + timedelta(days=armed_days),
            reason=reason,
            created_by=CREATED_BY,
            ticker=ticker.strip().upper(),
            threshold=threshold,
            hold=timedelta(hours=hold_hours),
        )
    )


def _multiplier(scale: float | None, pause: bool) -> float:
    if pause and scale is not None:
        raise PlanError("give --scale or --pause, not both")
    if pause:
        return 0.0
    if scale is None:
        raise PlanError("give --scale (for example 0.5) or --pause")
    return scale


def describe(plan: Plan) -> str:
    what = "pause new buys" if plan.pauses else f"scale new buys to {plan.multiplier:.0%}"
    if plan.is_window:
        when = (
            f"from {_utc(plan.starts_at).isoformat(timespec='minutes')} "
            f"to {_utc(plan.expires_at).isoformat(timespec='minutes')}"
        )
    else:
        hours = 0.0 if plan.hold is None else plan.hold.total_seconds() / 3600.0
        direction = "falls" if (plan.threshold or 0) < 0 else "rises"
        when = (
            f"for {hours:g}h after {plan.ticker} {direction} "
            f"{abs(plan.threshold or 0):.1%} or more in a session; armed until "
            f"{_utc(plan.expires_at).date().isoformat()}"
        )
    return f"{what} {when}"


def render(plans: Sequence[Plan], activations: Sequence[Activation], now: datetime) -> str:
    """Every plan, in force first, with what is in force stated at the top."""

    effect = effect_at(plans, activations, now)
    if effect.pause_buys:
        head = "IN FORCE NOW: new buys are PAUSED by " + ", ".join(effect.names)
    elif effect.in_force:
        head = f"IN FORCE NOW: new buys scaled to {effect.multiplier:.0%} by " + ", ".join(
            effect.names
        )
    else:
        head = "Nothing in force now."
    lines = [
        head,
        "(Applies only if PRE_TRADE_CHECKER_EVENT_PLANS_ENFORCEMENT is true on the service.)",
        "",
    ]
    if not plans:
        lines.append("No plans stored.")
        return "\n".join(lines)
    by_plan: dict[str, list[Activation]] = {}
    for activation in activations:
        by_plan.setdefault(activation.plan_id, []).append(activation)
    in_force = set(effect.plan_ids)

    def rank(plan: Plan) -> tuple[int, datetime]:
        if plan.plan_id in in_force:
            return (0, _utc(plan.starts_at))
        if plan.cancelled_at is not None:
            return (3, _utc(plan.starts_at))
        return (1 if _utc(plan.expires_at) > _utc(now) else 2, _utc(plan.starts_at))

    for plan in sorted(plans, key=rank):
        if plan.plan_id in in_force:
            state = "IN FORCE"
        elif plan.cancelled_at is not None:
            state = f"cancelled {_utc(plan.cancelled_at).date()} by {plan.cancelled_by}"
        elif _utc(plan.expires_at) <= _utc(now):
            state = "ended"
        elif plan.is_window:
            state = "scheduled"
        else:
            state = "armed"
        lines.append(f"[{state}] {plan.name} ({plan.event_kind})  {plan.plan_id}")
        lines.append(f"    {describe(plan)}")
        lines.append(f"    why: {plan.reason}")
        for activation in by_plan.get(plan.plan_id, []):
            lines.append(
                f"    fired on {activation.session_date} ({activation.observed:+.1%}), "
                f"in force {_utc(activation.activated_at).isoformat(timespec='minutes')} "
                f"to {_utc(activation.until).isoformat(timespec='minutes')}"
            )
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> str:
    # Imported here so `--help` and the pure functions above need no database
    # driver and no service settings.
    from shrap.agents.risk_compliance.pre_trade_checker.config import Settings
    from shrap.common.db import create_asyncpg_pool
    from shrap.risk_compliance.event_plans.store import EventPlanStore

    pool = await create_asyncpg_pool(args.dsn or Settings().postgres_dsn_value())
    try:
        store = EventPlanStore(pool)
        await store.ensure_schema()
        now = datetime.now(UTC)
        if args.action == "show":
            plans, activations = await store.history(args.limit)
            return render(plans, activations, now)
        if args.action == "cancel":
            if await store.cancel(args.plan_id, by=CREATED_BY, now=now):
                return f"{args.plan_id} cancelled. It has no effect from now."
            return f"{args.plan_id} is not a live plan; nothing changed."
        if args.action == "add-window":
            plan = build_window_plan(
                name=args.name,
                kind=args.kind,
                start=args.start,
                end=args.end,
                scale=args.scale,
                pause=args.pause,
                reason=args.reason,
            )
        else:
            plan = build_trigger_plan(
                name=args.name,
                kind=args.kind,
                ticker=args.ticker,
                threshold=args.threshold,
                hold_hours=args.hold_hours,
                armed_days=args.armed_days,
                scale=args.scale,
                pause=args.pause,
                reason=args.reason,
                now=now,
            )
        if args.dry_run:
            return f"would store: {plan.name}: {describe(plan)}\n(dry run; nothing written)"
        await store.add(plan)
        return f"stored {plan.plan_id}\n{plan.name}: {describe(plan)}"
    finally:
        await pool.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Event plans (ADR-0018): written rules that scale or pause new buys "
            "around an event. A plan can only make the firm smaller, and always ends."
        )
    )
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: the service's)")
    sub = parser.add_subparsers(dest="action", required=True)

    show = sub.add_parser("show", help="What is in force now, and every plan")
    show.add_argument("--limit", type=int, default=50)

    cancel = sub.add_parser("cancel", help="End a plan now. It is kept, stamped cancelled")
    cancel.add_argument("plan_id")

    def common(cmd: argparse.ArgumentParser) -> None:
        cmd.add_argument("--name", required=True)
        cmd.add_argument("--kind", required=True, help="fed, cpi, election, earnings, shock, ...")
        cmd.add_argument("--scale", type=float, default=None, help="Buy at this fraction, e.g. 0.5")
        cmd.add_argument("--pause", action="store_true", help="Approve no new buys")
        cmd.add_argument("--reason", required=True, help="Why. Stored with the plan")
        cmd.add_argument("--dry-run", action="store_true", help="Validate and print; write nothing")

    window = sub.add_parser("add-window", help="A plan for a scheduled event (at most 14 days)")
    common(window)
    window.add_argument("--start", type=parse_instant, required=True, help="ISO, with offset")
    window.add_argument("--end", type=parse_instant, required=True, help="ISO, with offset")

    trigger = sub.add_parser("add-trigger", help="A plan that fires on a ticker's daily move")
    common(trigger)
    trigger.add_argument("--ticker", required=True)
    trigger.add_argument(
        "--threshold", type=float, required=True, help="-0.03 = down 3%% or more; 0.05 = up 5%%"
    )
    trigger.add_argument("--hold-hours", type=float, required=True, help="How long it applies")
    trigger.add_argument(
        "--armed-days", type=float, default=180.0, help="How long it stays armed (max 366)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    try:
        print(asyncio.run(_run(args)))
    except PlanError as exc:
        raise SystemExit(f"refused: {exc}") from exc


if __name__ == "__main__":
    main()


__all__ = [
    "build_trigger_plan",
    "build_window_plan",
    "describe",
    "main",
    "parse_instant",
    "render",
]
