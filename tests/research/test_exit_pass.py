"""The exit pass, where the production failures would actually live.

Every defect in this firm's trading path was silent (#192-#199, KI-030). So the
properties pinned here are the ones whose failure would look like nothing
happening: an unarmed rule that reads the database anyway, an account whose book
has never been reconciled, a sell re-emitted every tick while the fill is in
flight, and one broken account taking the others down with it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from shrap.agents.research.strategy_runner.runner import run_exit_pass
from shrap.research.strategy_runner.engine import RunnerSignalConfig
from shrap.risk_compliance.risk_officer.exits import ExitRule, PositionPnL

NOW = datetime(2026, 9, 17, 14, 30, tzinfo=UTC)
CONFIG = RunnerSignalConfig()


class FakeRecord:
    def __init__(self, strategy_id: str, account_id: str | None) -> None:
        self.strategy_id = strategy_id
        self.account_id = account_id
        self.status = "paper"
        self.spec: dict[str, Any] = {}


class FakeRegistry:
    def __init__(self, records: list[FakeRecord]) -> None:
        self._records = records

    async def list_by_status(self, status: str) -> list[FakeRecord]:
        return self._records if status == "paper" else []


class FakeStateStore:
    """Only `latest_positions_pnl` matters here; it can be made to fail."""

    def __init__(
        self,
        by_account: dict[str, tuple[list[PositionPnL], datetime | None]],
        *,
        boom: set[str] | None = None,
    ) -> None:
        self.by_account = by_account
        self.boom = boom or set()
        self.asked: list[str] = []

    async def latest_positions_pnl(
        self, account_id: str
    ) -> tuple[list[PositionPnL], datetime | None]:
        if account_id in self.boom:
            raise RuntimeError(f"postgres down for {account_id}")
        self.asked.append(account_id)
        return self.by_account.get(account_id, ([], None))


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def xadd(self, stream: str, fields: dict[str, Any], **_: Any) -> str:
        self.published.append((stream, fields))
        return "1-1"


def _pos(
    ticker: str, qty: float, *, plpc: float | None = 0.0, intraday: float | None = 0.0
) -> PositionPnL:
    return PositionPnL(ticker, qty, qty * 100.0, plpc, intraday)


async def _run(
    *,
    rule: ExitRule,
    records: list[FakeRecord],
    store: FakeStateStore,
    redis: FakeRedis | None = None,
    emitted_at: dict[str, float] | None = None,
    now: float = 10_000.0,
) -> tuple[int, FakeRedis, dict[str, float]]:
    redis = redis or FakeRedis()
    emitted_at = emitted_at if emitted_at is not None else {}
    count = await run_exit_pass(
        redis=redis,  # type: ignore[arg-type]
        registry=FakeRegistry(records),  # type: ignore[arg-type]
        state_store=store,  # type: ignore[arg-type]
        config=CONFIG,
        rule=rule,
        emitted_at=emitted_at,
        now=now,
        suppress_seconds=900.0,
    )
    return count, redis, emitted_at


# --- unarmed is a true no-op ---------------------------------------------------


async def test_an_unarmed_rule_does_not_even_read_the_database() -> None:
    """Merging this must not add a query, let alone an order.

    If it read positions anyway, the cost of shipping it dark would be a
    per-tick database round trip per account, forever.
    """

    store = FakeStateStore({"ACC": ([_pos("AAPL", 10.0, plpc=0.9)], NOW)})

    count, redis, _ = await _run(rule=ExitRule(), records=[FakeRecord("s-1", "ACC")], store=store)

    assert count == 0
    assert store.asked == []
    assert redis.published == []


async def test_no_strategies_means_no_reads() -> None:
    store = FakeStateStore({})

    count, _, _ = await _run(rule=ExitRule(stop_loss_pct=0.08), records=[], store=store)

    assert count == 0
    assert store.asked == []


# --- the freshness rule --------------------------------------------------------


async def test_an_account_never_reconciled_is_left_alone() -> None:
    """ "No rows" is not "flat".

    Exiting against a book that has never been observed is the KI-030 shape —
    selling what the account may not hold. The absence of a snapshot must stop
    the exit, not be read as an empty portfolio.
    """

    store = FakeStateStore({"ACC": ([], None)})

    count, redis, _ = await _run(
        rule=ExitRule(stop_loss_pct=0.08), records=[FakeRecord("s-1", "ACC")], store=store
    )

    assert count == 0
    assert redis.published == []


async def test_a_genuinely_flat_account_is_fine() -> None:
    """A pass ran and found nothing. Not an error, just nothing to exit."""

    store = FakeStateStore({"ACC": ([], NOW)})

    count, _, _ = await _run(
        rule=ExitRule(stop_loss_pct=0.08), records=[FakeRecord("s-1", "ACC")], store=store
    )

    assert count == 0


# --- isolation -----------------------------------------------------------------


async def test_one_unreadable_account_does_not_block_the_others() -> None:
    """A book that cannot be read is a reason not to exit THAT account.

    Abandoning the whole pass would leave a justified stop unfired on a
    different account because an unrelated one had a bad connection.
    """

    store = FakeStateStore({"ACC-B": ([_pos("AAPL", 10.0, plpc=-0.20)], NOW)}, boom={"ACC-A"})

    count, redis, _ = await _run(
        rule=ExitRule(stop_loss_pct=0.08),
        records=[FakeRecord("s-1", "ACC-A"), FakeRecord("s-2", "ACC-B")],
        store=store,
    )

    assert count == 1
    assert redis.published[0][0] == "trading.strategy.signal"


# --- it actually fires ---------------------------------------------------------


async def test_a_spike_is_sold() -> None:
    """The case Mike named: up 18% on the day, nothing sold it."""

    store = FakeStateStore({"ACC": ([_pos("NVDA", 7.0, plpc=0.02, intraday=0.18)], NOW)})

    count, redis, emitted_at = await _run(
        rule=ExitRule(intraday_take_profit_pct=0.15),
        records=[FakeRecord("s-1", "ACC")],
        store=store,
    )

    assert count == 1
    stream, fields = redis.published[0]
    assert stream == "trading.strategy.signal"
    # Indistinguishable from an entry: same stream, same producer, same
    # envelope. An exit routed differently is an exit with its own gates to be
    # missing.
    assert fields["h_produced_by"] == "research/strategy-runner"
    payload = json.loads(fields["payload"])
    assert payload["side"] == "sell"
    assert payload["ticker"] == "NVDA"
    # The whole holding, not a partial no rule is left tracking.
    assert payload["quantity"] == 7.0
    assert "intraday-take-profit" in payload["justification_text"]
    assert emitted_at["NVDA"] == 10_000.0


async def test_a_sell_is_not_re_emitted_on_the_next_tick() -> None:
    """The oversell guard, end to end.

    The position still reads as open because snapshots refresh every ~300s. A
    second sell against the same holding would open a short.
    """

    store = FakeStateStore({"ACC": ([_pos("NVDA", 7.0, plpc=0.02, intraday=0.18)], NOW)})
    records = [FakeRecord("s-1", "ACC")]
    rule = ExitRule(intraday_take_profit_pct=0.15)
    emitted_at: dict[str, float] = {}

    first, _, emitted_at = await _run(
        rule=rule, records=records, store=store, emitted_at=emitted_at, now=10_000.0
    )
    second, redis2, _ = await _run(
        rule=rule, records=records, store=store, emitted_at=emitted_at, now=10_060.0
    )

    assert first == 1
    assert second == 0
    assert redis2.published == []


async def test_the_suppression_lifts_once_the_snapshot_can_have_caught_up() -> None:
    """If the sell never filled, the rule must be able to try again."""

    store = FakeStateStore({"ACC": ([_pos("NVDA", 7.0, plpc=0.02, intraday=0.18)], NOW)})
    records = [FakeRecord("s-1", "ACC")]
    rule = ExitRule(intraday_take_profit_pct=0.15)

    _, _, emitted_at = await _run(rule=rule, records=records, store=store, now=10_000.0)
    again, _, _ = await _run(
        rule=rule, records=records, store=store, emitted_at=emitted_at, now=11_000.0
    )

    assert again == 1


async def test_an_unassigned_strategy_produces_nothing() -> None:
    """No account, no book, no exit."""

    store = FakeStateStore({})

    count, _, _ = await _run(
        rule=ExitRule(stop_loss_pct=0.08), records=[FakeRecord("s-1", None)], store=store
    )

    assert count == 0
    assert store.asked == []


# --- configuration -------------------------------------------------------------


def test_the_runner_ships_with_no_exit_thresholds_set() -> None:
    """There is no defensible default stop.

    A guessed threshold is a guess about when to realise a loss, and the firm
    has not calibrated one. Unset means the exit pass does not read the book,
    so deploying this changes nothing until an operator decides.
    """

    from shrap.agents.research.strategy_runner.config import Settings

    settings = Settings(redis_url="redis://redis:6379/0")

    assert settings.exit_take_profit_pct is None
    assert settings.exit_stop_loss_pct is None
    assert settings.exit_intraday_take_profit_pct is None
    assert settings.exit_intraday_stop_loss_pct is None
    assert not settings.exit_rule().is_armed


def test_setting_one_threshold_from_the_environment_arms_it(
    monkeypatch: Any,
) -> None:
    """An operator must be able to arm this without a code change."""

    from shrap.agents.research.strategy_runner.config import Settings

    monkeypatch.setenv("STRATEGY_RUNNER_EXIT_INTRADAY_TAKE_PROFIT_PCT", "0.15")
    settings = Settings(redis_url="redis://redis:6379/0")

    rule = settings.exit_rule()
    assert rule.is_armed
    assert rule.intraday_take_profit_pct == 0.15
    assert rule.stop_loss_pct is None


def test_the_suppression_window_outlasts_the_snapshot_refresh() -> None:
    """Shorter than ~300s and the rule re-fires before the book updates."""

    from shrap.agents.research.strategy_runner.config import Settings

    settings = Settings(redis_url="redis://redis:6379/0")

    assert settings.exit_suppress_seconds > 300.0
