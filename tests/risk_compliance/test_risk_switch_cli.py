"""``shrap-risk-switch``: a human's set and clear, against fakes.

The command exists because the Risk Officer's ``set_switch`` and
``clear_switch`` had no caller, so these tests drive the real
:class:`RiskOfficer` through it rather than a stand-in: what is under test is
that a human transition is persisted, mirrored, published and reported, and
that a no-op is reported as one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from shrap.risk_compliance import pre_trade_checker_agent, risk_switch_cli
from shrap.risk_compliance.risk_officer.limits import PortfolioLimits
from shrap.risk_compliance.risk_officer.officer import RiskOfficer
from shrap.risk_compliance.risk_officer.switches import (
    ACTOR_HUMAN,
    ACTOR_MONITOR,
    SWITCH_DAILY_LOSS,
    SWITCH_MANUAL,
    SwitchBoard,
    SwitchState,
    strategy_switch,
)
from shrap.risk_compliance.risk_switch_cli import (
    change_switch,
    render_board,
    validate_switch_name,
)

NOW = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
STRATEGY = "01KYNH9VKXVQXJ48T4MF306PHE"


class FakeStore:
    def __init__(self) -> None:
        self.switches: list[SwitchState] = []

    async def record_switch(self, state: SwitchState) -> None:
        self.switches.append(state)


class FakeSwitchStore:
    def __init__(self, board: SwitchBoard | None = None) -> None:
        self.board = board or SwitchBoard()
        self.saved: list[SwitchState] = []

    async def load(self) -> SwitchBoard:
        return self.board

    async def save(self, state: SwitchState) -> None:
        self.saved.append(state)


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    async def publish(
        self, stream: str, produced_by: str, schema_version: str, payload: dict[str, Any]
    ) -> None:
        self.events.append((stream, produced_by, payload))


def _state(name: str, active: bool = True, actor: str = ACTOR_MONITOR) -> SwitchState:
    return SwitchState(
        name=name, active=active, actor=actor, reason="r", at=NOW - timedelta(hours=30)
    )


def _officer(board: SwitchBoard | None = None) -> tuple[RiskOfficer, FakeStore, FakeSwitchStore]:
    store, switches = FakeStore(), FakeSwitchStore(board)
    officer = RiskOfficer(
        store=store,  # type: ignore[arg-type]
        switch_store=switches,  # type: ignore[arg-type]
        registry=object(),  # type: ignore[arg-type]
        limits=PortfolioLimits(),
    )
    return officer, store, switches


async def test_clearing_a_strategy_switch_is_recorded_mirrored_and_published() -> None:
    name = strategy_switch(STRATEGY)
    officer, store, switches = _officer(SwitchBoard([_state(name)]))
    publisher = FakePublisher()

    message = await change_switch(
        officer, publisher, action="clear", name=name, reason=" reviewed ", now=NOW
    )

    assert [(s.name, s.active, s.actor, s.reason) for s in store.switches] == [
        (name, False, ACTOR_HUMAN, "reviewed")
    ]
    assert switches.saved == store.switches
    assert [(stream, payload["active"]) for stream, _, payload in publisher.events] == [
        (risk_switch_cli.STREAM_KILL_SWITCH_CLEAR, False)
    ]
    assert "cleared by human" in message and "measured from this moment" in message


async def test_setting_the_manual_switch_halts_and_is_stamped_human() -> None:
    officer, store, switches = _officer()
    publisher = FakePublisher()

    await change_switch(
        officer, publisher, action="set", name=SWITCH_MANUAL, reason="broker migration", now=NOW
    )

    assert switches.board.is_active(SWITCH_MANUAL)
    assert store.switches[0].actor == ACTOR_HUMAN
    assert publisher.events[0][0] == risk_switch_cli.STREAM_KILL_SWITCH_SET


async def test_clearing_a_switch_that_is_not_active_says_so_and_writes_nothing() -> None:
    """A no-op must not read as a halt having been lifted."""

    officer, store, switches = _officer()
    publisher = FakePublisher()

    message = await change_switch(
        officer, publisher, action="clear", name=SWITCH_DAILY_LOSS, reason="r", now=NOW
    )

    assert "nothing changed" in message
    assert store.switches == [] and switches.saved == [] and publisher.events == []


async def test_a_reason_is_required() -> None:
    officer, store, _ = _officer(SwitchBoard([_state(SWITCH_MANUAL)]))

    with pytest.raises(ValueError, match="reason"):
        await change_switch(officer, None, action="clear", name=SWITCH_MANUAL, reason="  ", now=NOW)

    assert store.switches == []


@pytest.mark.parametrize(
    "name", ["daily-loss", "strategy:", "strategy: 01ABC", "strategy:01A BC", "", "Manual"]
)
def test_a_misspelled_switch_is_refused_rather_than_created(name: str) -> None:
    """A switch nobody reads would be audited and published and halt nothing."""

    with pytest.raises(ValueError, match="not a switch"):
        validate_switch_name(name)


def test_a_well_formed_switch_name_passes_with_whitespace_trimmed() -> None:
    assert validate_switch_name(f" {strategy_switch(STRATEGY)} ") == strategy_switch(STRATEGY)
    assert validate_switch_name("manual") == SWITCH_MANUAL
    assert validate_switch_name("daily_loss") == SWITCH_DAILY_LOSS


def test_the_board_lists_active_switches_first_with_who_and_why() -> None:
    states = [
        _state(SWITCH_DAILY_LOSS, active=False),
        _state(strategy_switch(STRATEGY), active=True),
    ]

    text = render_board(states, states, NOW)

    assert text.index("ACTIVE") < text.index("clear ")
    assert "30.0h ago" in text and ACTOR_MONITOR in text
    assert "DISAGREEMENT" not in text


def test_the_board_reports_redis_disagreeing_with_postgres() -> None:
    """Redis is what the order path enforces, so the two differing means the
    firm is running a board that is not the recorded one."""

    recorded = [_state(SWITCH_DAILY_LOSS, active=False)]
    live = [_state(SWITCH_DAILY_LOSS, active=True)]

    text = render_board(recorded, live, NOW)

    assert "DISAGREEMENT on daily_loss: Postgres says clear, Redis says active" in text


def test_the_board_says_when_redis_cannot_be_read() -> None:
    assert "REDIS UNREADABLE" in render_board([_state(SWITCH_MANUAL)], None, NOW)


def test_an_empty_log_is_reported_as_empty() -> None:
    assert "no switch has ever been set" in render_board([], [], NOW)


def test_the_streams_match_the_monitors() -> None:
    """Named twice so reading the board does not import the order-path agent;
    this is what stops the two copies drifting."""

    assert risk_switch_cli.STREAM_KILL_SWITCH_SET == pre_trade_checker_agent.STREAM_KILL_SWITCH_SET
    assert (
        risk_switch_cli.STREAM_KILL_SWITCH_CLEAR == pre_trade_checker_agent.STREAM_KILL_SWITCH_CLEAR
    )
    assert risk_switch_cli.SCHEMA_VERSION == pre_trade_checker_agent.SCHEMA_VERSION
