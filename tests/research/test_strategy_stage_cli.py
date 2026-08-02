"""Manual lifecycle moves: the state machine, the audit stamp, and the two refusals.

The load-bearing tests are the refusals. This CLI is the only way a human can put
a strategy into a stage the Strategy Runner trades from, and the Runner never
asks how it got there — so whatever this tool permits, the firm will do.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from shrap.research.strategy_evaluator.pipeline import DEFERRED_RULES
from shrap.research.strategy_registry import (
    STATUS_HYPOTHESIS,
    STATUS_KILLED,
    STATUS_PAPER,
    STATUS_SMALL_SIZE_PAPER,
    StrategyRecord,
    StrategyTransition,
)
from shrap.research.strategy_stage_cli import (
    MANUAL_ACTOR,
    MANUAL_TRIGGER_KIND,
    TRADING_STAGES,
    _build_parser,
    move_stage,
    render_show,
)

_NOW = datetime(2026, 7, 28, tzinfo=UTC)

# A rule that exists only for these tests. The gate is what is under test, not
# whichever rules happen to be deferred today — depending on a real one made
# this file go red the moment #112 emptied DEFERRED_RULES, which is a test
# coupled to a table's contents rather than to the behaviour it checks.
_FUTURE_RULE = "some-future-rule"
_FUTURE_REASON = "its dependency does not exist yet"


@contextmanager
def deferred_rule(rule: str = _FUTURE_RULE, reason: str = _FUTURE_REASON) -> Iterator[None]:
    """Temporarily list a rule as deferred, restoring the real table after."""

    DEFERRED_RULES[rule] = reason
    try:
        yield
    finally:
        DEFERRED_RULES.pop(rule, None)


def _record(
    *, status: str = STATUS_HYPOTHESIS, spec: dict[str, Any] | None = None
) -> StrategyRecord:
    return StrategyRecord(
        strategy_id="01STRAT",
        name="test strategy",
        version=1,
        archetype="technical-catalyst",
        status=status,
        source="mike-seed",
        thesis="t",
        anchor={},
        tickers={"long": ["SPY"], "short": []},
        spec=spec if spec is not None else {"params": {"fast": 5, "slow": 20}},
        spec_hash="hash",
        regime_sizing_modifier=None,
        kill_criteria=["k"],
        code_ref=None,
        created_at=None,
        updated_at=None,
    )


class FakeRegistry:
    def __init__(self, record: StrategyRecord | None) -> None:
        self._record = record
        self.calls: list[dict[str, Any]] = []

    async def ensure_schema(self) -> None: ...

    async def get(self, strategy_id: str) -> StrategyRecord | None:
        return self._record

    async def transitions(self, strategy_id: str) -> list[StrategyTransition]:
        return []

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
        self.calls.append(
            {
                "to": to_status,
                "reason": reason,
                "trigger_kind": trigger_kind,
                "actor": actor,
                "expected_from": expected_from,
            }
        )
        return StrategyTransition(
            transition_id="01T",
            strategy_id=strategy_id,
            from_status=expected_from,
            to_status=to_status,
            reason=reason,
            trigger_kind=trigger_kind,
            trigger_ref=trigger_ref,
            actor=actor,
            occurred_at=_NOW,
        )


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(
        self, *, stream: str, produced_by: str, schema_version: str, payload: dict[str, object]
    ) -> object:
        self.published.append(stream)
        return None


# --- the audit stamp ---------------------------------------------------------


async def test_a_manual_move_is_stamped_as_human_not_as_a_verdict() -> None:
    """The whole reason this tool exists rather than a raw SQL update.

    A later reader must be able to separate "the firm decided this" from "a
    person decided this" by filtering one column.
    """

    registry = FakeRegistry(_record())
    await move_stage(
        registry,  # type: ignore[arg-type]
        None,
        "01STRAT",
        to_stage=STATUS_PAPER,
        reason="live systems test, not an edge claim",
    )
    call = registry.calls[0]
    assert call["actor"] == MANUAL_ACTOR
    assert call["trigger_kind"] == MANUAL_TRIGGER_KIND
    assert call["reason"] == "live systems test, not an edge claim"
    assert call["expected_from"] == STATUS_HYPOTHESIS


async def test_a_manual_move_is_visible_on_the_bus() -> None:
    """Principle 8 has no carve-out for decisions a human made.

    The registry does not publish and the Librarian only reacts to verdicts, so
    without this a manual move would be the one lifecycle change with no event.
    """

    publisher = FakePublisher()
    await move_stage(
        FakeRegistry(_record()),  # type: ignore[arg-type]
        publisher,  # type: ignore[arg-type]
        "01STRAT",
        to_stage=STATUS_PAPER,
        reason="r",
    )
    assert publisher.published == ["research.strategy.promoted"]


# --- refusals ----------------------------------------------------------------


async def test_an_illegal_transition_is_refused_with_the_legal_options() -> None:
    registry = FakeRegistry(_record(status=STATUS_HYPOTHESIS))
    with pytest.raises(SystemExit, match="not a legal transition"):
        await move_stage(
            registry,  # type: ignore[arg-type]
            None,
            "01STRAT",
            to_stage=STATUS_SMALL_SIZE_PAPER,  # hypothesis -> small-size skips paper
            reason="r",
        )
    assert registry.calls == []


async def test_a_terminal_strategy_cannot_be_resurrected() -> None:
    """`killed` has no outbound transitions; the message must say so plainly."""

    with pytest.raises(SystemExit, match="terminal"):
        await move_stage(
            FakeRegistry(_record(status=STATUS_KILLED)),  # type: ignore[arg-type]
            None,
            "01STRAT",
            to_stage=STATUS_PAPER,
            reason="r",
        )


async def test_a_missing_strategy_is_refused() -> None:
    with pytest.raises(SystemExit, match="no strategy"):
        await move_stage(
            FakeRegistry(None),  # type: ignore[arg-type]
            None,
            "01STRAT",
            to_stage=STATUS_PAPER,
            reason="r",
        )


async def test_moving_to_the_current_stage_is_a_no_op_not_an_error() -> None:
    registry = FakeRegistry(_record(status=STATUS_PAPER))
    out = await move_stage(
        registry,  # type: ignore[arg-type]
        None,
        "01STRAT",
        to_stage=STATUS_PAPER,
        reason="r",
    )
    assert "no-op" in out
    assert registry.calls == []


# --- the deferred-rule gate --------------------------------------------------


def _deferred_record() -> StrategyRecord:
    return _record(spec={"rule": _FUTURE_RULE, "params": {}})


async def test_a_deferred_rule_cannot_be_promoted_into_trading_by_default() -> None:
    """The asymmetry this gate closes.

    The Evaluator refuses to measure a deferred rule, but the Runner does not
    check deferred rules at all — it trades whatever sits at a trading stage. So
    a promotion would put an unevaluable rule into live orders, and this CLI is
    the only place a human sees that happening.
    """

    registry = FakeRegistry(_deferred_record())
    with deferred_rule(), pytest.raises(SystemExit, match="which spec hygiene defers"):
        await move_stage(
            registry,  # type: ignore[arg-type]
            None,
            "01STRAT",
            to_stage=STATUS_PAPER,
            reason="r",
        )
    assert registry.calls == []


async def test_the_acknowledgement_is_recorded_in_the_reason() -> None:
    """An override that leaves no trace is indistinguishable from not knowing."""

    registry = FakeRegistry(_deferred_record())
    with deferred_rule():
        await move_stage(
            registry,  # type: ignore[arg-type]
            None,
            "01STRAT",
            to_stage=STATUS_PAPER,
            reason="deliberate systems test",
            acknowledge_unevaluated=True,
        )
    reason = registry.calls[0]["reason"]
    assert reason.startswith("deliberate systems test")
    assert "acknowledged unevaluated" in reason
    assert _FUTURE_RULE in reason
    assert _FUTURE_REASON in reason


async def test_a_deferred_rule_may_still_move_to_a_non_trading_stage() -> None:
    """The gate guards trading, not bookkeeping — killing one must stay possible."""

    registry = FakeRegistry(_deferred_record())
    with deferred_rule():
        await move_stage(
            registry,  # type: ignore[arg-type]
            None,
            "01STRAT",
            to_stage=STATUS_KILLED,
            reason="superseded",
        )
    assert registry.calls[0]["to"] == STATUS_KILLED


def test_every_trading_stage_is_covered_by_the_gate() -> None:
    """A stage added to the Runner but not here would bypass the check silently."""

    from shrap.agents.research.strategy_runner.runner import ACTIVE_PAPER_STAGES

    assert set(ACTIVE_PAPER_STAGES) == set(TRADING_STAGES)


# --- dry run and rendering ---------------------------------------------------


async def test_dry_run_writes_nothing_and_says_so() -> None:
    registry = FakeRegistry(_record())
    publisher = FakePublisher()
    out = await move_stage(
        registry,  # type: ignore[arg-type]
        publisher,  # type: ignore[arg-type]
        "01STRAT",
        to_stage=STATUS_PAPER,
        reason="r",
        dry_run=True,
    )
    assert "DRY RUN" in out and "Nothing written" in out
    assert registry.calls == []
    assert publisher.published == []


async def test_a_trading_move_carries_the_trading_warning() -> None:
    out = await move_stage(
        FakeRegistry(_record()),  # type: ignore[arg-type]
        None,
        "01STRAT",
        to_stage=STATUS_PAPER,
        reason="r",
    )
    assert "This stage TRADES" in out


def test_show_renders_the_stage_and_the_legal_next_moves() -> None:
    out = render_show(_record(), [])
    assert "stage     : hypothesis" in out
    assert "Allowed next:" in out
    assert STATUS_PAPER in out


def test_show_marks_a_terminal_strategy_as_terminal() -> None:
    assert "(terminal)" in render_show(_record(status=STATUS_KILLED), [])


def test_move_requires_a_reason() -> None:
    """An unexplained stage change is not auditable, so argparse enforces it."""

    with pytest.raises(SystemExit):
        _build_parser().parse_args(["move", "01STRAT", "--to", "paper"])


# --- assign-account (ADR-0017) ------------------------------------------------


class _AssignRegistry:
    def __init__(self, record: StrategyRecord | None, assigned: str | None = None) -> None:
        self._record = record
        self._assigned = assigned
        self.calls: list[tuple[str, str | None]] = []

    async def get(self, strategy_id: str) -> StrategyRecord | None:
        return self._record

    async def assign_account(self, strategy_id: str, account_id: str | None) -> StrategyRecord:
        self.calls.append((strategy_id, account_id))
        assert self._record is not None
        return replace(self._record, account_id=account_id)


async def test_assign_account_reports_the_move_and_the_operator_step() -> None:
    from shrap.research.strategy_stage_cli import assign_account

    registry = _AssignRegistry(_record(status=STATUS_PAPER))
    out = await assign_account(registry, "01S", account_id="PA3ABCDEF")  # type: ignore[arg-type]

    assert registry.calls == [("01S", "PA3ABCDEF")]
    assert "(unassigned) -> PA3ABCDEF" in out
    # This asserted the presence of "set STRATEGY_RUNNER_ACCOUNT_ID", and was
    # right when one runner served one account. ADR-0017 made the runner read
    # each strategy's account from its registry row and nothing reads that
    # variable now — so the test had gone from pinning the operator's next step
    # to pinning a dead one. Inverted rather than deleted: the CLI must still
    # say what happens next, it just must not name a variable that does nothing.
    assert "STRATEGY_RUNNER_ACCOUNT_ID" not in out
    assert "ADR-0017" in out


async def test_clearing_says_positions_outlive_the_assignment() -> None:
    """The dangerous misreading: that freeing an account also flattens it."""

    from shrap.research.strategy_stage_cli import assign_account

    registry = _AssignRegistry(replace(_record(status=STATUS_PAPER), account_id="PA3ABCDEF"))
    out = await assign_account(registry, "01S", account_id=None)

    assert registry.calls == [("01S", None)]
    assert "still" in out and "held at the broker" in out


async def test_assign_account_dry_run_writes_nothing() -> None:
    from shrap.research.strategy_stage_cli import assign_account

    registry = _AssignRegistry(_record(status=STATUS_PAPER))
    out = await assign_account(registry, "01S", account_id="PA3ABCDEF", dry_run=True)

    assert registry.calls == []
    assert "DRY RUN" in out


async def test_reassigning_to_the_same_account_is_a_no_op() -> None:
    from shrap.research.strategy_stage_cli import assign_account

    registry = _AssignRegistry(replace(_record(status=STATUS_PAPER), account_id="PA3ABCDEF"))
    out = await assign_account(registry, "01S", account_id="PA3ABCDEF")

    assert registry.calls == []
    assert "no change" in out


async def test_assigning_a_missing_strategy_refuses() -> None:
    from shrap.research.strategy_stage_cli import assign_account

    registry = _AssignRegistry(None)
    with pytest.raises(SystemExit, match="no strategy"):
        await assign_account(registry, "01NOPE", account_id="PA3ABCDEF")  # type: ignore[arg-type]


def test_show_displays_the_assigned_account() -> None:
    record = replace(_record(status=STATUS_PAPER), account_id="PA3ABCDEF")
    out = render_show(record, [])
    assert "account   : PA3ABCDEF" in out


def test_show_says_an_unassigned_strategy_will_not_trade() -> None:
    """A strategy at a trading stage with no account is silently inert — the
    Runner logs it once a session and drops it. `show` must not leave that to
    be inferred from a blank field."""

    out = render_show(_record(status=STATUS_PAPER), [])
    assert "unassigned" in out and "will NOT trade" in out
