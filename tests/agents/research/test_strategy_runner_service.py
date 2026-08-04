"""Service-loop tests for the Strategy Runner — account routing and the equity gate.

The pure planner is tested in ``tests/research/test_strategy_runner_engine.py``.
What is only observable here is what the *service* does with accounts:

- each strategy is sized against **its own** broker account (ADR-0017), so two
  strategies in two accounts each get their own full book rather than a share of
  a blended one;
- an account whose snapshot is missing or stale defers only *its* strategies —
  the others still trade — and the phase event is left un-acked so the deferred
  ones get another chance this session;
- a strategy with no account at all is dropped rather than deferred, because no
  snapshot will ever arrive for it and retrying forever is a poison loop.

The un-acked deferral and the acked drop are the two load-bearing cases: one
would silently lose a trading session, the other would wedge the consumer.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from shrap.agents.research.strategy_runner.runner import (
    STREAM_MARKET_PHASE,
    PassResult,
    SessionTracker,
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
from shrap.research.strategy_runner.sizing import DEFAULT_MAX_EQUITY_AGE

SESSION = date(2026, 7, 29)
PRICE = 50.0
EQUITY = 10_000.0
ACCOUNT_A = "PA3ACCTONE"
ACCOUNT_B = "PA3ACCTTWO"
UNCAPPED = RunnerSignalConfig(max_quantity=1_000_000)


def _record(strategy_id: str, account_id: str | None) -> StrategyRecord:
    return StrategyRecord(
        strategy_id=strategy_id,
        name=f"strategy-{strategy_id}",
        version=1,
        archetype="infra-graph-play",
        status=STATUS_PAPER,
        source="test",
        thesis="test",
        anchor=None,
        tickers={"long": ["NVDA"]},
        # Rising closes: fast(2) > slow(3) => target 1.0 => a buy.
        spec={"params": {"fast": 2, "slow": 3, "target_weight": 1.0}},
        spec_hash=f"hash-{strategy_id}",
        regime_sizing_modifier=None,
        kill_criteria=["md>0.5"],
        code_ref=None,
        created_at=None,
        updated_at=None,
        account_id=account_id,
    )


class FakeRegistry:
    def __init__(self, records: list[StrategyRecord]) -> None:
        self._records = records

    async def list_by_status(self, status: str) -> list[StrategyRecord]:
        return list(self._records) if status == STATUS_PAPER else []


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
    """Per-account equity, so one account can be stale while another is fresh."""

    def __init__(
        self,
        equity_by_account: dict[str, tuple[float | None, datetime | None]],
        positions: dict[str, tuple[dict[str, float], datetime | None]] | None = None,
    ) -> None:
        self._equity = equity_by_account
        self._positions = positions or {}
        self.writes: list[PlannedStateWrite] = []
        self.equity_lookups: list[str] = []

    async def read_state(self) -> dict[tuple[str, str], TargetState]:
        return {}

    async def latest_positions(self, account_id: str) -> tuple[dict[str, float], datetime | None]:
        """Default: a reconciled but flat account.

        `at` is set and the mapping is empty — the pass ran and found nothing.
        That is deliberately NOT the same as `(({}, None))`, which means no pass
        has run and makes the Runner defer (KI-030).
        """

        return self._positions.get(account_id, ({}, datetime.now(UTC)))

    async def latest_equity(self, account_id: str) -> tuple[float | None, datetime | None]:
        self.equity_lookups.append(account_id)
        return self._equity.get(account_id, (None, None))

    async def upsert(self, write: PlannedStateWrite) -> None:
        self.writes.append(write)


def _fresh(equity: float = EQUITY) -> tuple[float, datetime]:
    return equity, datetime.now(UTC)


def _stale(equity: float = EQUITY) -> tuple[float, datetime]:
    return equity, datetime.now(UTC) - DEFAULT_MAX_EQUITY_AGE - timedelta(minutes=1)


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


async def _run(
    records: list[StrategyRecord],
    store: FakeStateStore,
    redis: FakeRedis | None = None,
) -> PassResult:
    return await run_pass(
        session_date=SESSION,
        redis=redis or FakeRedis(),  # type: ignore[arg-type]
        registry=FakeRegistry(records),  # type: ignore[arg-type]
        reader=FakeReader(),  # type: ignore[arg-type]
        state_store=store,  # type: ignore[arg-type]
        config=UNCAPPED,
        adjustment="all",
        lookback_buffer_days=10,
        lookback_max_days=1200,
    )


def _quantities(redis: FakeRedis) -> list[int]:
    out: list[int] = []
    for _, fields in redis.published:
        payload = Envelope.from_redis_fields(fields).payload
        assert payload is not None
        out.append(int(payload["quantity"]))
    return out


# --- account routing ----------------------------------------------------------


async def test_a_strategy_is_sized_against_its_own_account() -> None:
    """$10,000 fully weighted into a $50 name is 200 shares."""

    redis = FakeRedis()
    store = FakeStateStore({ACCOUNT_A: _fresh()})
    result = await _run([_record("s1", ACCOUNT_A)], store, redis=redis)

    assert result.emitted == 1
    assert result.is_complete
    assert store.equity_lookups == [ACCOUNT_A]
    assert _quantities(redis) == [200]


async def test_two_accounts_each_get_their_own_full_book() -> None:
    """THE ADR-0017 property.

    Under the old firm-wide budget these two would have split one $10,000
    allocation and traded 100 shares each. They are separate books, so each gets
    its own $10,000 and its own 200 shares.
    """

    redis = FakeRedis()
    store = FakeStateStore({ACCOUNT_A: _fresh(), ACCOUNT_B: _fresh()})
    result = await _run([_record("s1", ACCOUNT_A), _record("s2", ACCOUNT_B)], store, redis=redis)

    assert result.emitted == 2
    assert _quantities(redis) == [200, 200]


async def test_two_strategies_in_one_account_still_share_that_account() -> None:
    """The exposure budget did not disappear — it became per-account."""

    redis = FakeRedis()
    store = FakeStateStore({ACCOUNT_A: _fresh()})
    result = await _run([_record("s1", ACCOUNT_A), _record("s2", ACCOUNT_A)], store, redis=redis)

    assert result.emitted == 2
    assert _quantities(redis) == [100, 100]  # one book split two ways


# --- isolation between accounts -----------------------------------------------


async def test_a_stale_account_defers_only_its_own_strategies() -> None:
    """THE isolation test.

    One account's Reconciliation Agent falling behind must not stop the other
    accounts trading — that would couple three independent books through a
    shared failure.
    """

    redis = FakeRedis()
    store = FakeStateStore({ACCOUNT_A: _stale(), ACCOUNT_B: _fresh()})
    result = await _run([_record("s1", ACCOUNT_A), _record("s2", ACCOUNT_B)], store, redis=redis)

    assert result.emitted == 1  # B traded
    assert result.deferred == ("s1",)  # A did not
    assert not result.is_complete
    assert [w.strategy_id for w in store.writes] == ["s2"]  # only B stamped


async def test_a_missing_snapshot_defers_rather_than_raising() -> None:
    store = FakeStateStore({})
    result = await _run([_record("s1", ACCOUNT_A)], store)
    assert result.emitted == 0
    assert result.deferred == ("s1",)


# --- unassigned strategies ----------------------------------------------------


async def test_an_unassigned_strategy_is_dropped_not_deferred() -> None:
    """No snapshot will ever arrive for a strategy with no account.

    Deferring it would leave the phase event pending forever — a poison loop
    that also blocks every later session. It needs a human, so it is logged and
    dropped and the pass completes.
    """

    redis = FakeRedis()
    store = FakeStateStore({ACCOUNT_A: _fresh()})
    result = await _run([_record("s1", None), _record("s2", ACCOUNT_A)], store, redis=redis)

    assert result.emitted == 1  # only the assigned one traded
    assert result.deferred == ()  # NOT deferred
    assert result.is_complete  # so the event can be acked
    assert [w.strategy_id for w in store.writes] == ["s2"]


async def test_a_pass_with_only_unassigned_strategies_completes() -> None:
    result = await _run([_record("s1", None)], FakeStateStore({}))
    assert result.emitted == 0
    assert result.is_complete


# --- ack discipline -----------------------------------------------------------


async def _poll(records: list[StrategyRecord], store: FakeStateStore, redis: FakeRedis) -> int:
    subscriber = GroupEventSubscriber(redis, group="strategy-runner", start_id="0")  # type: ignore[arg-type]
    return await poll_once(
        redis,  # type: ignore[arg-type]
        subscriber,
        registry=FakeRegistry(records),  # type: ignore[arg-type]
        reader=FakeReader(),  # type: ignore[arg-type]
        state_store=store,  # type: ignore[arg-type]
        config=UNCAPPED,
        adjustment="all",
        lookback_buffer_days=10,
        lookback_max_days=1200,
        count=10,
        block_ms=0,
    )


async def test_a_deferred_pass_is_not_acked_so_the_session_retries() -> None:
    """Acking here would drop a whole trading session for the deferred account
    on a transient failure. The per-session state guard makes the retry safe:
    strategies that already traded are stamped and will not re-emit."""

    redis = FakeRedis(_open_phase_entries())
    store = FakeStateStore({ACCOUNT_A: _stale(), ACCOUNT_B: _fresh()})

    emitted = await _poll([_record("s1", ACCOUNT_A), _record("s2", ACCOUNT_B)], store, redis)

    assert emitted == 1  # B still traded
    assert redis.acked == []  # pending, so A retries


async def test_a_complete_pass_does_ack() -> None:
    """The contrast case — otherwise the test above would pass on a loop that
    never acks anything."""

    redis = FakeRedis(_open_phase_entries())
    store = FakeStateStore({ACCOUNT_A: _fresh()})

    emitted = await _poll([_record("s1", ACCOUNT_A)], store, redis)

    assert emitted == 1
    assert redis.acked == ["1-0"]


async def test_an_unassigned_strategy_does_not_wedge_the_consumer() -> None:
    """The poison-loop guard, at the loop level."""

    redis = FakeRedis(_open_phase_entries())
    store = FakeStateStore({})

    await _poll([_record("s1", None)], store, redis)

    assert redis.acked == ["1-0"]


# --- session tracking and interval firing (2.9) --------------------------------


async def test_the_tracker_remembers_open_between_events() -> None:
    # market-phase publishes transitions, not ticks, so the loop's only way to
    # know it is inside `open` between boundaries is to remember it.
    tracker = SessionTracker()
    assert tracker.open_session is None

    tracker.observe("open", SESSION)
    assert tracker.open_session == SESSION


async def test_any_non_open_phase_stops_interval_firing() -> None:
    tracker = SessionTracker(open_session=SESSION)

    tracker.observe("closed", None)
    assert tracker.open_session is None


async def test_an_unrecognised_phase_reads_as_not_open() -> None:
    # A phase added later must not be able to leave the Runner firing into a
    # market that is shut.
    tracker = SessionTracker(open_session=SESSION)

    tracker.observe("some-future-phase", SESSION)
    assert tracker.open_session is None


async def test_poll_once_updates_the_tracker_from_the_phase_stream() -> None:
    redis = FakeRedis(_open_phase_entries())
    store = FakeStateStore({ACCOUNT_A: _fresh()})
    tracker = SessionTracker()
    subscriber = GroupEventSubscriber(redis, group="strategy-runner", start_id="0")  # type: ignore[arg-type]

    await poll_once(
        redis,  # type: ignore[arg-type]
        subscriber,
        registry=FakeRegistry([_record("s1", ACCOUNT_A)]),  # type: ignore[arg-type]
        reader=FakeReader(),  # type: ignore[arg-type]
        state_store=store,  # type: ignore[arg-type]
        config=UNCAPPED,
        adjustment="all",
        lookback_buffer_days=10,
        lookback_max_days=1200,
        count=10,
        block_ms=0,
        tracker=tracker,
    )

    assert tracker.open_session == SESSION


async def test_a_pass_with_nothing_due_reads_no_bars() -> None:
    """The 'must stay cheap' property from the timeline, measured.

    Under a one-minute tick this pass runs ~390 times a session and is a no-op
    for almost all of them. If the guard ran after `_build_input` the Runner
    would read a trailing window per ticker per tick to compute a skip it had
    already decided.
    """

    class CountingReader(FakeReader):
        def __init__(self) -> None:
            self.reads = 0

        async def read_bars(
            self, ticker: str, start: date, end: date, adjustment: str
        ) -> list[BarSample]:
            self.reads += 1
            return await super().read_bars(ticker, start, end, adjustment)

    class StampedStore(FakeStateStore):
        async def read_state(self) -> dict[tuple[str, str], TargetState]:
            return {
                ("s1", "NVDA"): TargetState(
                    last_target=1.0,
                    last_side="buy",
                    last_session_date=SESSION,
                    last_quantity=10,
                )
            }

    reader = CountingReader()
    result = await run_pass(
        session_date=SESSION,
        redis=FakeRedis(),  # type: ignore[arg-type]
        registry=FakeRegistry([_record("s1", ACCOUNT_A)]),  # type: ignore[arg-type]
        reader=reader,  # type: ignore[arg-type]
        state_store=StampedStore({ACCOUNT_A: _fresh()}),  # type: ignore[arg-type]
        config=UNCAPPED,
        adjustment="all",
        lookback_buffer_days=10,
        lookback_max_days=1200,
    )

    assert result.emitted == 0
    assert reader.reads == 0


# --- positions are as load-bearing as equity (KI-030) --------------------------


async def test_an_account_with_no_reconciliation_pass_defers() -> None:
    """ "No rows" must never be read as "flat".

    That reading is what let the Runner sell stock it did not own. An account
    whose positions cannot be established is deferred on the same terms as one
    whose equity cannot be — it gets another chance next pass rather than
    trading against an assumption.
    """

    store = FakeStateStore({ACCOUNT_A: _fresh()}, positions={ACCOUNT_A: ({}, None)})

    result = await _run([_record("s1", ACCOUNT_A)], store)

    assert result.emitted == 0
    assert result.deferred == ("s1",)
    assert not result.is_complete


async def test_a_reconciled_flat_account_trades() -> None:
    """The contrast case, and the reason the flat marker exists.

    A pass that ran and found nothing is a fact about the book. A pass that
    never ran is an absence of information. They must not behave alike.
    """

    store = FakeStateStore({ACCOUNT_A: _fresh()}, positions={ACCOUNT_A: ({}, datetime.now(UTC))})

    result = await _run([_record("s1", ACCOUNT_A)], store)

    assert result.emitted == 1
    assert result.is_complete
