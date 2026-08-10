"""The Risk Officer end to end, against fakes for Postgres and Redis.

The property under test throughout is **failing closed**. The spec calls a Risk
Officer that fails open "among the firm's worst failure modes", and one that
fails closed "halts trading but is safe". Every read on the order path — switch
state, positions, NAV, price — has a test here proving that losing it refuses
the order rather than waving it through.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from shrap.risk_compliance.risk_officer.exposure import Position
from shrap.risk_compliance.risk_officer.limits import PortfolioLimits
from shrap.risk_compliance.risk_officer.monitor import EquityPoint
from shrap.risk_compliance.risk_officer.officer import (
    REASON_NO_ACCOUNT,
    REASON_RISK_STATE_UNAVAILABLE,
    REASON_UNKNOWN_STRATEGY,
    RiskOfficer,
)
from shrap.risk_compliance.risk_officer.switch_store import SwitchStateUnavailable
from shrap.risk_compliance.risk_officer.switches import (
    SWITCH_DAILY_LOSS,
    SwitchBoard,
    SwitchState,
    strategy_switch,
)

NOW = datetime(2026, 7, 29, 15, 0, tzinfo=UTC)
STRATEGY = "01KYNH9VKXVQXJ48T4MF306PHE"
ACCOUNT = "PA3XXXXX"


@dataclass
class FakeRecord:
    strategy_id: str = STRATEGY
    account_id: str | None = ACCOUNT
    status: str = "paper"


class FakeRegistry:
    def __init__(self, record: Any = None, raises: bool = False) -> None:
        self._record = record if record is not None else FakeRecord()
        self._raises = raises

    async def get(self, strategy_id: str) -> Any:
        if self._raises:
            raise RuntimeError("postgres down")
        return self._record


class FakeStore:
    """Stands in for RiskStore. Every read can be made to fail."""

    def __init__(
        self,
        positions: tuple[Position, ...] = (),
        observed_at: datetime | None = NOW,
        equity: tuple[float, ...] = (10_000.0, 10_000.0),
        price: float | None = 100.0,
        fail: str | None = None,
    ) -> None:
        self._positions = positions
        self._observed_at = observed_at
        self._equity = equity
        self._price = price
        self._fail = fail
        self.decisions: list[Any] = []
        self.switches: list[SwitchState] = []

    def _maybe_fail(self, name: str) -> None:
        if self._fail == name:
            raise RuntimeError(f"{name} unavailable")

    async def latest_positions(self, account_id: str) -> Any:
        self._maybe_fail("positions")
        return self._positions, self._observed_at

    async def equity_series(self, account_id: str, since: Any) -> tuple[EquityPoint, ...]:
        self._maybe_fail("equity")
        return tuple(
            EquityPoint(at=NOW - timedelta(minutes=5 * (len(self._equity) - i)), equity=e)
            for i, e in enumerate(self._equity)
        )

    async def latest_close(self, ticker: str) -> float | None:
        self._maybe_fail("price")
        return self._price

    async def price_history(self, tickers: Any, limit: int = 90) -> dict[str, tuple[float, ...]]:
        self._maybe_fail("history")
        series = tuple(100.0 * (1.001**i) for i in range(120))
        return dict.fromkeys(tickers, series)

    async def record_switch(self, state: SwitchState) -> None:
        self.switches.append(state)

    async def load_switch_states(self) -> tuple[SwitchState, ...]:
        return tuple(self.switches)


class FakeSwitchStore:
    def __init__(self, board: SwitchBoard | None = None, fail: bool = False) -> None:
        self._board = board or SwitchBoard()
        self._fail = fail
        self.saved: list[SwitchState] = []

    async def load(self) -> SwitchBoard:
        if self._fail:
            raise SwitchStateUnavailable("redis down")
        return self._board

    async def save(self, state: SwitchState) -> None:
        self.saved.append(state)

    async def rebuild(self, states: Any) -> None:
        self._board = SwitchBoard(states)


def _officer(store: FakeStore, switches: FakeSwitchStore, registry: Any = None) -> RiskOfficer:
    return RiskOfficer(
        store=store,  # type: ignore[arg-type]
        switch_store=switches,  # type: ignore[arg-type]
        registry=registry or FakeRegistry(),
        limits=PortfolioLimits(),
    )


async def _assess(officer: RiskOfficer, quantity: int = 4, side: str = "buy") -> Any:
    return await officer.assess(
        ticker="AAPL",
        side=side,
        quantity=quantity,
        strategy_ids=[STRATEGY],
        regime_label="late-cycle-melt-up",
        now=NOW,
    )


# --- the happy path -----------------------------------------------------------


async def test_an_intent_inside_every_limit_is_approved() -> None:
    officer = _officer(FakeStore(), FakeSwitchStore())

    assessment = await _assess(officer, quantity=8)

    assert assessment.approved
    assert assessment.account_id == ACCOUNT
    # 8 x 0.25 paper stage x 0.75 regime. This asserted 1 until 2026-08-09 —
    # the floor threw away a third of the position and did it silently.
    assert assessment.approved_quantity == 1.5


async def test_a_small_order_is_scaled_rather_than_vetoed() -> None:
    """The 26-of-89 case, inverted.

    4 shares x 0.25 x 0.75 is 0.75 of a share. That was floored to zero and
    vetoed SIZED_TO_ZERO — which is how 26 of 89 live decisions died, every one
    of them a request under six shares. It is a real position now.
    """

    officer = _officer(FakeStore(), FakeSwitchStore())

    assessment = await _assess(officer, quantity=4)

    assert assessment.approved
    assert assessment.approved_quantity == 0.75


async def test_an_order_too_small_to_be_a_position_is_still_refused() -> None:
    """SIZED_TO_ZERO survives, with a meaning worth having.

    It now fires only when the scaled order is worth less than a dollar — the
    order being meaningless, rather than integer arithmetic eating it. At $100 a
    share, 0.01 shares scales to 0.001875 and is worth 19 cents.
    """

    officer = _officer(FakeStore(), FakeSwitchStore())

    assessment = await _assess(officer, quantity=0.01)

    assert not assessment.approved
    assert assessment.reason_code == "SIZED_TO_ZERO"


async def test_the_stage_fraction_and_regime_both_apply() -> None:
    """paper stage is 25%, late-cycle-melt-up is 75%, so 100 -> 18.75."""

    officer = _officer(FakeStore(), FakeSwitchStore())

    assessment = await officer.assess(
        ticker="AAPL",
        side="buy",
        quantity=100,
        strategy_ids=[STRATEGY],
        regime_label="late-cycle-melt-up",
        now=NOW,
    )

    assert assessment.sizing is not None
    assert assessment.sizing.stage_fraction == 0.25
    assert assessment.regime_multiplier == 0.75
    assert assessment.sizing.approved_quantity == 18.75  # was 18; the 0.75 was floored away


async def test_the_kelly_slot_is_present_and_empty() -> None:
    """There is no Bayesian Updater in the firm, so there is no posterior. The
    slot stays visible and unset rather than being filled with backtest Sharpe,
    which the spec explicitly forbids."""

    officer = _officer(FakeStore(), FakeSwitchStore())

    assessment = await _assess(officer)

    assert assessment.sizing is not None
    assert assessment.sizing.kelly_posterior is None


# --- failing closed -----------------------------------------------------------


async def test_unreadable_switch_state_refuses_the_intent() -> None:
    officer = _officer(FakeStore(), FakeSwitchStore(fail=True))

    assessment = await _assess(officer)

    assert not assessment.approved
    assert assessment.reason_code == REASON_RISK_STATE_UNAVAILABLE


async def test_an_unmeasured_book_refuses_the_intent() -> None:
    """No position snapshot at all — the Reconciliation Agent has never run or
    cannot reach the broker. Not the same as a flat account."""

    officer = _officer(FakeStore(observed_at=None), FakeSwitchStore())

    assessment = await _assess(officer)

    assert not assessment.approved
    assert assessment.reason_code == REASON_RISK_STATE_UNAVAILABLE


async def test_a_stale_book_refuses_the_intent() -> None:
    officer = _officer(FakeStore(observed_at=NOW - timedelta(hours=4)), FakeSwitchStore())

    assessment = await _assess(officer)

    assert not assessment.approved
    assert assessment.reason_code == REASON_RISK_STATE_UNAVAILABLE


async def test_missing_equity_refuses_the_intent() -> None:
    officer = _officer(FakeStore(equity=()), FakeSwitchStore())

    assessment = await _assess(officer)

    assert not assessment.approved
    assert assessment.reason_code == REASON_RISK_STATE_UNAVAILABLE


async def test_an_unreachable_registry_refuses_the_intent() -> None:
    officer = _officer(FakeStore(), FakeSwitchStore(), FakeRegistry(raises=True))

    assessment = await _assess(officer)

    assert not assessment.approved
    assert assessment.reason_code == REASON_RISK_STATE_UNAVAILABLE


async def test_a_missing_price_refuses_the_intent() -> None:
    officer = _officer(FakeStore(price=None), FakeSwitchStore())

    assessment = await _assess(officer)

    assert not assessment.approved


async def test_an_unknown_strategy_is_refused() -> None:
    class MissingRegistry:
        async def get(self, strategy_id: str) -> Any:
            return None

    officer = _officer(FakeStore(), FakeSwitchStore(), MissingRegistry())

    assessment = await _assess(officer)

    assert not assessment.approved
    assert assessment.reason_code == REASON_UNKNOWN_STRATEGY


async def test_a_strategy_with_no_account_is_refused() -> None:
    """ADR-0017: without an account there is no book to measure the order
    against."""

    officer = _officer(
        FakeStore(), FakeSwitchStore(), FakeRegistry(record=FakeRecord(account_id=None))
    )

    assessment = await _assess(officer)

    assert not assessment.approved
    assert assessment.reason_code == REASON_NO_ACCOUNT


async def test_an_intent_with_no_strategy_id_is_refused() -> None:
    officer = _officer(FakeStore(), FakeSwitchStore())

    assessment = await officer.assess(
        ticker="AAPL", side="buy", quantity=10, strategy_ids=[], now=NOW
    )

    assert not assessment.approved
    assert assessment.reason_code == REASON_UNKNOWN_STRATEGY


# --- switches on the order path -----------------------------------------------


async def test_an_active_switch_blocks_a_new_position() -> None:
    board = SwitchBoard()
    board.set(SWITCH_DAILY_LOSS, actor="m", reason="r", at=NOW)
    officer = _officer(FakeStore(), FakeSwitchStore(board))

    assessment = await _assess(officer)

    assert not assessment.approved
    assert assessment.reason_code == "KILL_SWITCH_ACTIVE"


async def test_an_active_switch_still_permits_an_exit() -> None:
    board = SwitchBoard()
    board.set(SWITCH_DAILY_LOSS, actor="m", reason="r", at=NOW)
    officer = _officer(
        FakeStore(positions=(Position("AAPL", 10.0, 1_000.0),)),
        FakeSwitchStore(board),
    )

    assessment = await _assess(officer, quantity=4, side="sell")

    assert assessment.approved


async def test_an_exit_is_never_scaled_by_the_stage_fraction() -> None:
    """Sizing scales how much risk is TAKEN. Scaling an exit sells a quarter of
    the position, then a quarter of the remainder, and eventually rounds to
    zero — leaving a position the strategy asked to close and cannot.
    """

    officer = _officer(
        FakeStore(positions=(Position("AAPL", 10.0, 1_000.0),)),
        FakeSwitchStore(),
    )

    assessment = await _assess(officer, quantity=10, side="sell")

    assert assessment.approved
    assert assessment.approved_quantity == 10  # not 10 x 0.25 x 0.75
    assert assessment.sizing is not None
    assert assessment.sizing.stage_fraction == 1.0


async def test_a_buy_is_still_scaled_when_a_position_is_already_held() -> None:
    """The reduction exemption must not leak into increases."""

    officer = _officer(
        FakeStore(positions=(Position("AAPL", 10.0, 1_000.0),)),
        FakeSwitchStore(),
    )

    assessment = await _assess(officer, quantity=8, side="buy")

    assert assessment.sizing is not None
    assert assessment.sizing.stage_fraction == 0.25


# --- the heartbeat ------------------------------------------------------------


async def test_a_drawdown_breach_sets_the_strategy_switch() -> None:
    """The 53.88% case: the sweep is what would have caught it."""

    store = FakeStore(equity=(10_000.0, 12_000.0, 5_536.0))
    switches = FakeSwitchStore()
    officer = _officer(store, switches)

    result = await officer.sweep([(STRATEGY, ACCOUNT)], now=NOW)

    assert any(t.state.name == strategy_switch(STRATEGY) for t in result.transitions)
    assert store.switches  # persisted to risk.kill_switches
    assert switches.saved  # mirrored to Redis


async def test_a_daily_loss_breach_sets_the_firm_wide_switch() -> None:
    store = FakeStore(equity=(10_000.0, 9_700.0))
    officer = _officer(store, FakeSwitchStore())

    result = await officer.sweep([(STRATEGY, ACCOUNT)], now=NOW)

    assert any(t.state.name == SWITCH_DAILY_LOSS for t in result.transitions)


async def test_a_healthy_account_sets_nothing() -> None:
    officer = _officer(FakeStore(equity=(10_000.0, 10_050.0)), FakeSwitchStore())

    result = await officer.sweep([(STRATEGY, ACCOUNT)], now=NOW)

    assert result.transitions == ()


async def test_a_repeated_breach_does_not_write_a_second_transition() -> None:
    """The sweep runs every five minutes and the breach persists. One audit row,
    not one per pass."""

    store = FakeStore(equity=(10_000.0, 9_700.0))
    switches = FakeSwitchStore()
    officer = _officer(store, switches)

    await officer.sweep([(STRATEGY, ACCOUNT)], now=NOW)
    second = await officer.sweep([(STRATEGY, ACCOUNT)], now=NOW)

    assert second.transitions == ()
    assert len(store.switches) == 1


async def test_the_sweep_does_nothing_when_switch_state_is_unreadable() -> None:
    """It cannot know what is already set, so setting anything risks a duplicate
    transition on a switch that was already active."""

    officer = _officer(FakeStore(equity=(10_000.0, 5_000.0)), FakeSwitchStore(fail=True))

    result = await officer.sweep([(STRATEGY, ACCOUNT)], now=NOW)

    assert result.transitions == ()
    assert result.observations == ()


async def test_postgres_is_written_before_redis() -> None:
    """Postgres is the authority and Redis the cache. A crash between them
    leaves a switch recorded but not enforced, which the next rebuild corrects;
    the reverse leaves an enforced halt with no audit row."""

    calls: list[str] = []

    class OrderedStore(FakeStore):
        async def record_switch(self, state: SwitchState) -> None:
            calls.append("postgres")
            await super().record_switch(state)

    class OrderedSwitches(FakeSwitchStore):
        async def save(self, state: SwitchState) -> None:
            calls.append("redis")
            await super().save(state)

    officer = _officer(OrderedStore(equity=(10_000.0, 9_700.0)), OrderedSwitches())
    await officer.sweep([(STRATEGY, ACCOUNT)], now=NOW)

    assert calls == ["postgres", "redis"]


@pytest.mark.parametrize("label,expected", [("wartime", 0.25), ("stagflation", 0.5), (None, 0.25)])
async def test_the_regime_reaches_the_assessment(label: str | None, expected: float) -> None:
    officer = _officer(FakeStore(), FakeSwitchStore())

    assessment = await officer.assess(
        ticker="AAPL",
        side="buy",
        quantity=100,
        strategy_ids=[STRATEGY],
        regime_label=label,
        now=NOW,
    )

    assert assessment.regime_multiplier == expected


# --- Exits are capped at the position, never scaled (2026-08-07 live) ---------
#
# `reduces_position` exempts a risk-reducing trade from sizing, but answers
# False whenever the Officer's book does not contain the ticker, or contains
# less than the sell. Both fell through to size_intent and scaled the exit.
#
# Live proof: a 6-share UUP sell was approved at 1 and stranded 5, while RIOT
# and RIVN sells in the same second were exempt. Same predicate, two answers,
# decided by whether a position snapshot happened to be current.


def _held(ticker: str, quantity: float, price: float = 100.0) -> tuple[Position, ...]:
    return (Position(ticker=ticker, quantity=quantity, market_value=quantity * price),)


async def test_an_exit_is_capped_at_the_position_not_scaled_below_it() -> None:
    """The UUP case. Selling 6 against 5 held must sell 5, not 1.5."""

    officer = _officer(FakeStore(positions=_held("AAPL", 5.0)), FakeSwitchStore())

    assessment = await _assess(officer, quantity=6, side="sell")

    assert assessment.approved
    assert assessment.approved_quantity == 5.0  # the position, not 6 x 0.25 x 0.75


async def test_an_exit_smaller_than_the_position_is_not_scaled_at_all() -> None:
    """A partial exit is still an exit. Stage fraction must not touch it."""

    officer = _officer(FakeStore(positions=_held("AAPL", 20.0)), FakeSwitchStore())

    assessment = await _assess(officer, quantity=8, side="sell")

    assert assessment.approved
    assert assessment.approved_quantity == 8.0
    assert assessment.sizing is not None
    assert assessment.sizing.stage_fraction == 1.0  # exempt, not scaled


async def test_a_sell_with_no_position_is_refused_rather_than_scaled_into_a_short() -> None:
    """The severe form, and the reason this is not merely a stranding bug.

    Scaling a sell the account cannot cover does not reduce anything — it opens
    a short of the scaled size. Approving 1 of a 6-share sell against an empty
    book is a 1-share short nobody asked for, and needs no veto to happen.
    """

    officer = _officer(FakeStore(positions=()), FakeSwitchStore())

    assessment = await _assess(officer, quantity=6, side="sell")

    assert not assessment.approved
    assert assessment.reason_code == "SELL_WITHOUT_POSITION"
    assert assessment.approved_quantity == 0


async def test_a_sell_against_an_existing_short_is_refused_not_deepened() -> None:
    """A negative book is not a position to reduce. It is one to escalate."""

    officer = _officer(FakeStore(positions=_held("AAPL", -4.0)), FakeSwitchStore())

    assessment = await _assess(officer, quantity=3, side="sell")

    assert not assessment.approved
    assert assessment.reason_code == "SELL_WITHOUT_POSITION"


async def test_a_buy_is_still_scaled_by_stage_and_regime() -> None:
    """The exemption is for exits only. Entries keep taking risk deliberately."""

    officer = _officer(FakeStore(positions=_held("AAPL", 5.0)), FakeSwitchStore())

    assessment = await _assess(officer, quantity=8, side="buy")

    assert assessment.approved
    assert assessment.approved_quantity == 1.5  # 8 x 0.25 x 0.75, unchanged
