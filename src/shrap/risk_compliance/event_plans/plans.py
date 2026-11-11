"""Event plans: what a plan is, and what it does at a given instant.

ADR-0018 cards 3 to 5. A plan is a written rule that makes the firm smaller
around an event: it scales new buys down, or pauses them, either over a fixed
window (a scheduled event) or for a fixed time after an observable condition
is met (a shock).

**A plan's effect is a pure function of the plan rows and the clock.** There is
no ``active`` flag anywhere. A window plan applies while ``now`` is inside its
window; a trigger plan applies while ``now`` is inside one of its recorded
activations, each of which carries its own end time. So there is no code whose
job is to switch a plan off, and therefore no way for that code to not run.

That is deliberate. The ``daily_loss`` switch stayed set for over a week because
the clear its docstring promised had no caller (#291), and the drawdown switch
could not be released at all (#293). Both were stored state waiting on a
release. A plan cannot outlive its end time because nothing has to remember to
end it: :func:`effect_at` simply stops returning it.

Tier A only (ADR-0018 §3). A plan can make the firm smaller than the Risk
Officer's limits allow and never larger: a multiplier outside ``[0, 1]`` is
refused at validation, and again by a ``CHECK`` in the table.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

TRIGGER_WINDOW = "window"
TRIGGER_DAILY_RETURN = "daily-return"
TRIGGERS: frozenset[str] = frozenset({TRIGGER_WINDOW, TRIGGER_DAILY_RETURN})

ACTION_SCALE_BUYS = "scale-buys"
ACTION_PAUSE_BUYS = "pause-buys"
ACTIONS: frozenset[str] = frozenset({ACTION_SCALE_BUYS, ACTION_PAUSE_BUYS})

# "Every plan expires" needs a number, or a plan written for one Fed meeting is
# still cutting exposure at the next. These are first cuts, not rulings.
MAX_WINDOW = timedelta(days=14)
"""Longest a scheduled plan may apply."""
MAX_HOLD = timedelta(days=5)
"""Longest one activation of a trigger plan may last."""
MAX_ARMED = timedelta(days=366)
"""Longest a trigger plan may stay armed before someone has to renew it."""


class PlanError(ValueError):
    """A plan that must not be stored."""


@dataclass(frozen=True, slots=True)
class Plan:
    plan_id: str
    name: str
    event_kind: str
    trigger: str
    action: str
    multiplier: float
    starts_at: datetime
    expires_at: datetime
    reason: str
    created_by: str
    ticker: str | None = None
    threshold: float | None = None
    hold: timedelta | None = None
    cancelled_at: datetime | None = None
    cancelled_by: str | None = None

    @property
    def is_window(self) -> bool:
        return self.trigger == TRIGGER_WINDOW

    @property
    def pauses(self) -> bool:
        return self.action == ACTION_PAUSE_BUYS

    def live(self, now: datetime) -> bool:
        """Inside its own lifetime and not cancelled."""

        return self.cancelled_at is None and _utc(self.starts_at) <= _utc(now) < _utc(
            self.expires_at
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "name": self.name,
            "event_kind": self.event_kind,
            "trigger": self.trigger,
            "action": self.action,
            "multiplier": self.multiplier,
            "starts_at": _utc(self.starts_at).isoformat(),
            "expires_at": _utc(self.expires_at).isoformat(),
            "ticker": self.ticker,
            "threshold": self.threshold,
            "hold_hours": None if self.hold is None else self.hold.total_seconds() / 3600.0,
            "reason": self.reason,
            "created_by": self.created_by,
        }


@dataclass(frozen=True, slots=True)
class Activation:
    """One firing of a trigger plan. It ends at ``until`` with no further action."""

    plan_id: str
    session_date: date
    activated_at: datetime
    until: datetime
    observed: float

    def covers(self, now: datetime) -> bool:
        return _utc(self.activated_at) <= _utc(now) < _utc(self.until)


@dataclass(frozen=True, slots=True)
class DailyReturn:
    """A completed session's close-to-close return for one ticker."""

    ticker: str
    session_date: date
    ret: float


@dataclass(frozen=True, slots=True)
class Effect:
    """What every plan in force adds up to right now."""

    multiplier: float = 1.0
    pause_buys: bool = False
    plan_ids: tuple[str, ...] = ()
    names: tuple[str, ...] = ()

    @property
    def in_force(self) -> bool:
        return bool(self.plan_ids)

    def to_payload(self) -> dict[str, object]:
        return {
            "multiplier": self.multiplier,
            "pause_buys": self.pause_buys,
            "plan_ids": list(self.plan_ids),
            "names": list(self.names),
        }


NO_EFFECT = Effect()


def _utc(at: datetime) -> datetime:
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


def validate(plan: Plan) -> Plan:
    """The plan, or :class:`PlanError` saying why it must not be stored.

    Everything checked here is something that would otherwise fail quietly: a
    multiplier of 1.2 would raise exposure through a control meant to cut it, a
    trigger plan with no ticker would never fire and read as a quiet market, and
    a window with no end is a latch.
    """

    if not plan.name.strip():
        raise PlanError("a plan needs a name")
    if not plan.reason.strip():
        raise PlanError("a plan needs a reason: it is the audit row's only explanation")
    if plan.trigger not in TRIGGERS:
        raise PlanError(f"unknown trigger {plan.trigger!r}; use one of {sorted(TRIGGERS)}")
    if plan.action not in ACTIONS:
        raise PlanError(f"unknown action {plan.action!r}; use one of {sorted(ACTIONS)}")
    if not 0.0 <= plan.multiplier <= 1.0:
        raise PlanError(
            f"multiplier {plan.multiplier} is outside [0, 1]. A plan may only make the "
            "firm smaller (ADR-0018 Tier A)."
        )
    if plan.pauses and plan.multiplier != 0.0:
        raise PlanError("a pause-buys plan has multiplier 0")
    if not plan.pauses and plan.multiplier in (0.0, 1.0):
        raise PlanError(
            "a scale-buys plan needs a multiplier strictly between 0 and 1: "
            "0 is pause-buys, and 1 does nothing"
        )
    life = _utc(plan.expires_at) - _utc(plan.starts_at)
    if life <= timedelta(0):
        raise PlanError("expires_at must be after starts_at")
    if plan.is_window:
        if life > MAX_WINDOW:
            raise PlanError(f"a window plan may apply for at most {MAX_WINDOW.days} days")
        if plan.ticker or plan.threshold is not None or plan.hold is not None:
            raise PlanError("a window plan takes no ticker, threshold or hold")
        return plan
    if life > MAX_ARMED:
        raise PlanError(f"a trigger plan may stay armed for at most {MAX_ARMED.days} days")
    if not (plan.ticker or "").strip():
        raise PlanError("a daily-return plan needs the ticker it watches")
    if plan.threshold is None or plan.threshold == 0.0 or abs(plan.threshold) >= 1.0:
        raise PlanError(
            "a daily-return plan needs a non-zero threshold as a fraction, "
            "for example -0.03 for 'down 3% or more'"
        )
    if plan.hold is None or plan.hold <= timedelta(0) or plan.hold > MAX_HOLD:
        raise PlanError(f"a trigger plan's hold must be positive and at most {MAX_HOLD.days} days")
    return plan


def triggered(plan: Plan, observation: DailyReturn) -> bool:
    """Whether a completed session meets a daily-return plan's condition.

    The threshold's sign is the direction: ``-0.03`` fires on a fall of 3% or
    more, ``+0.05`` on a rise of 5% or more. Equality fires.
    """

    if plan.trigger != TRIGGER_DAILY_RETURN or plan.threshold is None:
        return False
    if (plan.ticker or "").upper() != observation.ticker.upper():
        return False
    if plan.threshold < 0:
        return observation.ret <= plan.threshold
    return observation.ret >= plan.threshold


def due_activations(
    plans: Sequence[Plan],
    existing: Sequence[Activation],
    observations: Mapping[str, DailyReturn],
    now: datetime,
) -> list[Activation]:
    """New activations the sweep should record.

    One per plan per observed session, so a sweep every five minutes over the
    same closed session writes one row and not three hundred. An activation is
    dated from when the firm *saw* the condition, not from the session it
    happened in: the plan was not in force before the firm knew.
    """

    seen = {(a.plan_id, a.session_date) for a in existing}
    out: list[Activation] = []
    for plan in plans:
        if plan.is_window or not plan.live(now) or plan.hold is None:
            continue
        observation = observations.get((plan.ticker or "").upper())
        if observation is None or not triggered(plan, observation):
            continue
        if (plan.plan_id, observation.session_date) in seen:
            continue
        out.append(
            Activation(
                plan_id=plan.plan_id,
                session_date=observation.session_date,
                activated_at=now,
                until=now + plan.hold,
                observed=observation.ret,
            )
        )
    return out


def effect_at(plans: Sequence[Plan], activations: Sequence[Activation], now: datetime) -> Effect:
    """What the plans in force add up to at ``now``.

    The smallest multiplier wins, and any pause pauses. A cancelled plan has no
    effect from the moment it is cancelled, including through an activation
    still inside its hold: a cancel is a ruling, and a plan that kept acting
    after one would be #293 again.
    """

    covered = {a.plan_id for a in activations if a.covers(now)}
    in_force: list[Plan] = []
    for plan in plans:
        if plan.cancelled_at is not None:
            continue
        if plan.is_window:
            if plan.live(now):
                in_force.append(plan)
        elif plan.plan_id in covered:
            in_force.append(plan)
    if not in_force:
        return NO_EFFECT
    in_force.sort(key=lambda p: (p.multiplier, p.plan_id))
    return Effect(
        multiplier=min(p.multiplier for p in in_force),
        pause_buys=any(p.pauses for p in in_force),
        plan_ids=tuple(p.plan_id for p in in_force),
        names=tuple(p.name for p in in_force),
    )


__all__ = [
    "ACTIONS",
    "ACTION_PAUSE_BUYS",
    "ACTION_SCALE_BUYS",
    "MAX_ARMED",
    "MAX_HOLD",
    "MAX_WINDOW",
    "NO_EFFECT",
    "TRIGGERS",
    "TRIGGER_DAILY_RETURN",
    "TRIGGER_WINDOW",
    "Activation",
    "DailyReturn",
    "Effect",
    "Plan",
    "PlanError",
    "due_activations",
    "effect_at",
    "triggered",
    "validate",
]
