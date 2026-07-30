"""Kill switches: what is halted, why, and who did it.

Three scopes, because a breach is not always firm-wide:

``manual``       Mike's flag. Halts everything. The only switch a human sets.
``daily_loss``   Auto-set when an account breaches the daily loss limit. Halts
                 **new intents firm-wide**, per the spec — one account bleeding
                 is a reason to stop the firm and look, not to keep trading the
                 other two.
``strategy:<id>``Auto-set when one strategy breaches its drawdown limit. Halts
                 that strategy alone.

Two properties this module exists to guarantee:

**Setting is idempotent, clearing is explicit.** Re-setting an active switch
does not produce a second transition, so a monitor that observes the same breach
every heartbeat writes one audit row rather than one per pass. Clearing an
inactive switch is likewise a no-op. Only genuine transitions are returned, and
only genuine transitions are published and persisted.

**A switch halts new positions, never exits.** The spec is explicit: "existing
positions follow each strategy's exit logic". A kill switch that also blocked
sells would trap the firm in the position that triggered it, which is the
opposite of containment. :func:`blocks_intent` therefore takes the trade's
direction into account and never blocks a reduction.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

SWITCH_MANUAL = "manual"
SWITCH_DAILY_LOSS = "daily_loss"
SWITCH_STRATEGY_PREFIX = "strategy:"

ACTOR_MONITOR = "risk-officer/monitor"
ACTOR_HUMAN = "human"

REASON_KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"


def strategy_switch(strategy_id: str) -> str:
    """The switch name that halts one strategy."""

    return f"{SWITCH_STRATEGY_PREFIX}{strategy_id.strip()}"


def is_strategy_switch(name: str) -> bool:
    return name.startswith(SWITCH_STRATEGY_PREFIX)


def strategy_id_of(name: str) -> str | None:
    if not is_strategy_switch(name):
        return None
    return name[len(SWITCH_STRATEGY_PREFIX) :]


@dataclass(frozen=True, slots=True)
class SwitchState:
    """One switch, as it currently stands."""

    name: str
    active: bool
    actor: str
    reason: str
    at: datetime

    def to_payload(self) -> dict[str, object]:
        return {
            "switch": self.name,
            "active": self.active,
            "actor": self.actor,
            "reason": self.reason,
            "at": self.at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SwitchTransition:
    """A genuine state change, worth publishing and persisting."""

    state: SwitchState
    previously_active: bool

    @property
    def was_set(self) -> bool:
        return self.state.active


class SwitchBoard:
    """In-memory switch state with idempotent set/clear.

    The authoritative copy lives in Redis and its history in Postgres (see
    ``store.py``); this class holds the decision logic so it can be tested
    without either.
    """

    def __init__(self, states: Iterable[SwitchState] = ()) -> None:
        self._states: dict[str, SwitchState] = {s.name: s for s in states}

    @property
    def states(self) -> Mapping[str, SwitchState]:
        return dict(self._states)

    def is_active(self, name: str) -> bool:
        state = self._states.get(name)
        return state is not None and state.active

    @property
    def active_names(self) -> tuple[str, ...]:
        return tuple(sorted(n for n, s in self._states.items() if s.active))

    def set(self, name: str, *, actor: str, reason: str, at: datetime) -> SwitchTransition | None:
        """Activate ``name``. Returns ``None`` when it was already active."""

        if self.is_active(name):
            return None
        state = SwitchState(name=name, active=True, actor=actor, reason=reason, at=at)
        self._states[name] = state
        return SwitchTransition(state=state, previously_active=False)

    def clear(self, name: str, *, actor: str, reason: str, at: datetime) -> SwitchTransition | None:
        """Deactivate ``name``. Returns ``None`` when it was not active."""

        if not self.is_active(name):
            return None
        state = SwitchState(name=name, active=False, actor=actor, reason=reason, at=at)
        self._states[name] = state
        return SwitchTransition(state=state, previously_active=True)

    def blocking_switch(self, strategy_ids: Iterable[str] = ()) -> str | None:
        """The switch that halts a new position, or ``None`` if none does.

        Firm-wide switches are checked first so the reported reason names the
        broadest cause rather than an incidental per-strategy one.
        """

        for name in (SWITCH_MANUAL, SWITCH_DAILY_LOSS):
            if self.is_active(name):
                return name
        for strategy_id in strategy_ids:
            name = strategy_switch(strategy_id)
            if self.is_active(name):
                return name
        return None


def reduces_position(current_market_value: float, delta_market_value: float) -> bool:
    """True when a trade moves a position toward flat.

    Used so a kill switch never traps the firm in the position that tripped it.
    Crossing through zero — selling more than is held, which would open a short
    — is not a reduction: it is a new position in the other direction, and a
    halted book may not open one.
    """

    if delta_market_value == 0.0:
        return False
    if current_market_value == 0.0:
        return False
    if (current_market_value > 0.0) == (delta_market_value > 0.0):
        return False
    return abs(delta_market_value) <= abs(current_market_value)


def blocks_intent(
    board: SwitchBoard,
    *,
    strategy_ids: Iterable[str] = (),
    current_market_value: float = 0.0,
    delta_market_value: float = 0.0,
) -> str | None:
    """The switch blocking this specific intent, or ``None``.

    Returns ``None`` for a trade that reduces an existing position even when a
    switch is active — see the module docstring.
    """

    name = board.blocking_switch(strategy_ids)
    if name is None:
        return None
    if reduces_position(current_market_value, delta_market_value):
        return None
    return name


__all__ = [
    "ACTOR_HUMAN",
    "ACTOR_MONITOR",
    "REASON_KILL_SWITCH_ACTIVE",
    "SWITCH_DAILY_LOSS",
    "SWITCH_MANUAL",
    "SWITCH_STRATEGY_PREFIX",
    "SwitchBoard",
    "SwitchState",
    "SwitchTransition",
    "blocks_intent",
    "is_strategy_switch",
    "reduces_position",
    "strategy_id_of",
    "strategy_switch",
]
