"""Service-loop tests for the Strategy Runner — specifically the equity gate.

The pure planner is tested in ``tests/research/test_strategy_runner_engine.py``.
What is only observable here is what the *service* does with account equity:

- it reads equity before planning and sizes the pass against it;
- unusable equity (missing or stale) emits nothing **and does not ack**, so the
  market-phase event stays pending and the pass retries once the Reconciliation
  Agent writes a fresh snapshot.

The un-acked refusal is the load-bearing one. Acking would silently drop a whole
trading session; falling back to a fixed size would trade a book nobody
evaluated. Refusing and retrying is the only option that does neither.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from shrap.agents.research.strategy_runner.runner import (
    STREAM_MARKET_PHASE,
    poll_once,
    run_pass,
)
from shrap.common.envelope import Envelope
from shrap.events.groups import GroupEventSubscriber
from shrap.research.strategy_evaluator.strategy import BarSample
from shrap.research.strategy_registry import STATUS_PAPER, StrategyRecord
from shrap.research.strategy_runner.engine import (
    PlannedStateWrite,
    RunnerSignalConfig,
    TargetState,
)
from shrap.research.strategy_runner.sizing import DEFAULT_MAX_EQUITY_AGE, SizingRefused

SESSION = date(2026, 7, 28)
PRICE = 50.0
EQUITY = 10_000.0
UNCAPPED = RunnerSignalConfig(max_quantity=1_000_000)


def _record() -> StrategyRecord:
    return StrategyRecord(
        strategy_id="01RUNNERSVC",
        name="reference-ma-crossover",
        version=1,
        archetype="infra-graph-play",
        status=STATUS_PAPER,
        source="test",
        thesis="test",
        anchor=None,
        tickers={"long": ["NVDA"]},
        # Rising closes: fast(2) > slow(3) => target 1.0 => a buy.
        spec={"params": {"fast": 2, "slow": 3, "target_weight": 1.0}},
        spec_hash="hash-runner-svc",
        regime_sizing_modifier=None,
        kill_criteria=["md>0.5"],
        code_ref=None,
        created_at=None,
        updated_at=None,
    )


class FakeRegistry:
    async def list_by_status(self, status: str) -> list[StrategyRecord]:
        return [_record()] if status == STATUS_PAPER else []


class FakeReader:
    """Rising closes ending at PRICE, so the reference rule targets 1.0."""

    async def read_bars(
        self, ticker: str, start: date, end: date, adjustment: str
    ) -> list[BarSample]:
        closes = [PRICE - 4, PRICE - 3, PRICE - 2, PRICE - 1, PRICE]
        return [
            BarSample(
                session_date=date(2026, 1, 1) + timedelta(days=i),
                open=c,
                high=c,
                low=c,
                close=c,
                volume=1000.0,
            )
            for i, c in enumerate(closes)
        ]


class FakeStateStore:
    def __init__(
        self,
        equity: float | None = EQUITY,
        observed_at: datetime | None = None,
    ) -> None:
        self._equity = equity
        self._observed_at = observed_at if observed_at is not None else datetime.now(UTC)
        self.writes: list[PlannedStateWrite] = []

    async def read_state(self) -> dict[tuple[str, str], TargetState]:
        return {}

    async def latest_equity(self) -> tuple[float | None, datetime | None]:
        return self._equity, self._observed_at

    async def upsert(self, write: PlannedStateWrite) -> None:
        self.writes.append(write)


class FakeRedis:
    def __init__(self, entries: list[tuple[str, dict[str, str]]] | None = None) -> None:
        self.entries = entries or []
        self.acked: list[str] = []
        self.published: list[tuple[str, dict[str, str]]] = []

    async def xgroup_create(
        self, name: str, groupname: str, id: str = "$", mkstream: bool = False
    ) -> Any:
        return "OK"

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[Any, Any],
        count: int | None = None,
        block: int | None = None,
    ) -> Any:
        read_id = next(iter(streams.values()))
        if read_id != ">" or not self.entries:
            return []
        batch, self.entries = self.entries, []
        return [(STREAM_MARKET_PHASE, batch)]

    async def xack(self, name: str, groupname: str, *ids: str) -> Any:
        self.acked.extend(ids)
        return len(ids)

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.published.append((stream, fields))
        return f"{len(self.published)}-0"

    async def xrevrange(
        self, name: str, max: str = "+", min: str = "-", count: int | None = None
    ) -> Any:
        return []  # no regime event; regime is informational anyway


def _open_phase_entries() -> list[tuple[str, dict[str, str]]]:
    envelope = Envelope.new(
        produced_by="operations/market-phase",
        schema_version="1.0.0",
        payload={"phase": "open", "session_date": SESSION.isoformat()},
    )
    return [("1-0", envelope.to_redis_fields())]


async def _run(store: FakeStateStore) -> int:
    return await run_pass(
        session_date=SESSION,
        redis=FakeRedis(),  # type: ignore[arg-type]
        registry=FakeRegistry(),  # type: ignore[arg-type]
        reader=FakeReader(),  # type: ignore[arg-type]
        state_store=store,  # type: ignore[arg-type]
        config=UNCAPPED,
        adjustment="all",
        lookback_buffer_days=10,
        lookback_max_days=1200,
    )


# --- the happy path -----------------------------------------------------------


async def test_a_pass_sizes_its_signals_against_the_account_snapshot() -> None:
    """$10,000 fully weighted into a $50 name is 200 shares, read from the
    Reconciliation Agent's snapshot rather than hardcoded."""

    redis = FakeRedis()
    store = FakeStateStore()
    emitted = await run_pass(
        session_date=SESSION,
        redis=redis,  # type: ignore[arg-type]
        registry=FakeRegistry(),  # type: ignore[arg-type]
        reader=FakeReader(),  # type: ignore[arg-type]
        state_store=store,  # type: ignore[arg-type]
        config=UNCAPPED,
        adjustment="all",
        lookback_buffer_days=10,
        lookback_max_days=1200,
    )

    assert emitted == 1
    (stream, fields) = redis.published[0]
    assert stream == "trading.strategy.signal"
    envelope = Envelope.from_redis_fields(fields)
    assert envelope.payload is not None
    assert envelope.payload["quantity"] == 200
    assert store.writes[0].last_quantity == 200


# --- the equity gate ----------------------------------------------------------


async def test_a_missing_snapshot_refuses_the_whole_pass() -> None:
    with pytest.raises(SizingRefused, match="no account snapshot"):
        await _run(FakeStateStore(equity=None))


async def test_a_stale_snapshot_refuses_the_whole_pass() -> None:
    stale = datetime.now(UTC) - DEFAULT_MAX_EQUITY_AGE - timedelta(minutes=1)
    with pytest.raises(SizingRefused, match="stale"):
        await _run(FakeStateStore(observed_at=stale))


async def test_refusal_publishes_nothing_and_writes_no_state() -> None:
    """A refused pass must be a no-op, not a partial one."""

    store = FakeStateStore(equity=None)
    redis = FakeRedis()
    with pytest.raises(SizingRefused):
        await run_pass(
            session_date=SESSION,
            redis=redis,  # type: ignore[arg-type]
            registry=FakeRegistry(),  # type: ignore[arg-type]
            reader=FakeReader(),  # type: ignore[arg-type]
            state_store=store,  # type: ignore[arg-type]
            config=UNCAPPED,
            adjustment="all",
            lookback_buffer_days=10,
            lookback_max_days=1200,
        )
    assert redis.published == []
    assert store.writes == []


async def test_a_refused_pass_is_not_acked_so_the_session_retries() -> None:
    """THE test in this file.

    Acking here would drop a whole trading session on a transient dependency
    failure — the snapshot comes back on the Reconciliation Agent's next pass,
    minutes later. Leaving the phase event pending means the session resumes on
    its own, and the per-session state guard makes the retry safe.
    """

    redis = FakeRedis(_open_phase_entries())
    subscriber = GroupEventSubscriber(redis, group="strategy-runner", start_id="0")  # type: ignore[arg-type]

    emitted = await poll_once(
        redis,  # type: ignore[arg-type]
        subscriber,
        registry=FakeRegistry(),  # type: ignore[arg-type]
        reader=FakeReader(),  # type: ignore[arg-type]
        state_store=FakeStateStore(equity=None),  # type: ignore[arg-type]
        config=UNCAPPED,
        adjustment="all",
        lookback_buffer_days=10,
        lookback_max_days=1200,
        count=10,
        block_ms=0,
    )

    assert emitted == 0
    assert redis.acked == []  # pending: the pass will run again
    assert redis.published == []


async def test_a_healthy_pass_does_ack() -> None:
    """The contrast case — otherwise the test above would pass on a loop that
    never acks anything."""

    redis = FakeRedis(_open_phase_entries())
    subscriber = GroupEventSubscriber(redis, group="strategy-runner", start_id="0")  # type: ignore[arg-type]

    emitted = await poll_once(
        redis,  # type: ignore[arg-type]
        subscriber,
        registry=FakeRegistry(),  # type: ignore[arg-type]
        reader=FakeReader(),  # type: ignore[arg-type]
        state_store=FakeStateStore(),  # type: ignore[arg-type]
        config=UNCAPPED,
        adjustment="all",
        lookback_buffer_days=10,
        lookback_max_days=1200,
        count=10,
        block_ms=0,
    )

    assert emitted == 1
    assert redis.acked == ["1-0"]
