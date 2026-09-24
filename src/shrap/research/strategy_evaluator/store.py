"""Persistence for the Evaluator: the ``research.evaluations`` writer and the
read-only readers for the tables it consumes but does not own.

Two responsibilities, deliberately split by ownership (mirrors the Pre-Trade
Checker's "reader, never owner" rule for ``research.universe_tiers``):

- :class:`PostgresEvaluationStore` — sole writer of ``research.evaluations``, an
  append-only metrics ledger. One row per evaluation run: the verdict, the
  per-fold and aggregate metrics as JSONB, the config and protocol version that
  produced them. Never updated; the strategy's *status* lives in
  ``research.strategies`` and its transitions in ``research.strategy_transitions``.
- :class:`PostgresEvaluatorReader` — read-only over ``market_data.daily_bars``
  (backtest data), ``research.world_changers`` (anchor freshness), and
  ``research.universe_tiers`` (Tier-3 eligibility). It never creates or migrates
  those tables; a missing table is an infrastructure fault surfaced by the
  caller, not papered over here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from shrap.market_data.fundamentals_store import PostgresFundamentalsStore
from shrap.market_data.shares_store import PostgresSharesStore
from shrap.market_data.store import DEFAULT_BAR_SOURCE
from shrap.operations.market_phase import (
    DEFAULT_CALENDAR,
    DEFAULT_TIMEZONE,
    is_regular_hours,
    regular_session_bounds,
)
from shrap.research.ledger import EDGE_REASONS
from shrap.research.strategy_evaluator.strategy import BarSample

CREATE_RESEARCH_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS research"

CREATE_EVALUATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research.evaluations (
    evaluation_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    spec_hash TEXT NOT NULL,
    protocol_version TEXT NOT NULL,
    verdict TEXT NOT NULL,
    reason TEXT NOT NULL,
    anchor_fresh BOOLEAN NOT NULL,
    total_trades INTEGER NOT NULL,
    from_stage TEXT NOT NULL,
    to_stage TEXT,
    aggregate_metrics JSONB NOT NULL,
    fold_metrics JSONB NOT NULL,
    stress_metrics JSONB NOT NULL,
    config JSONB NOT NULL,
    card_path TEXT,
    trigger TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip()

CREATE_EVALUATIONS_STRATEGY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS evaluations_strategy_idx
ON research.evaluations (strategy_id, created_at DESC)
""".strip()

# Added with the archetype-conditional gates (ADR-0013). `anchor_fresh` alone is
# ambiguous once an archetype can be anchor-less: False means "the anchor is
# dead" for infra-graph-play and "there was never an anchor" for
# technical-catalyst. DEFAULT TRUE is correct for every pre-existing row —
# infra-graph-play was the only evaluable archetype when they were written — so
# the dead-anchor set stays queryable as (anchor_required AND NOT anchor_fresh).
ADD_EVALUATIONS_ANCHOR_REQUIRED_SQL = """
ALTER TABLE research.evaluations
ADD COLUMN IF NOT EXISTS anchor_required BOOLEAN NOT NULL DEFAULT TRUE
""".strip()

# Added with benchmark-relative evaluation. The verdict can now hinge on the
# information ratio, so the ledger must record it or the row cannot explain its
# own verdict. Defaults to '{}' for rows written before the benchmark existed —
# honestly "not measured", not "measured as zero".
ADD_EVALUATIONS_ACTIVE_METRICS_SQL = """
ALTER TABLE research.evaluations
ADD COLUMN IF NOT EXISTS active_metrics JSONB NOT NULL DEFAULT '{}'::jsonb
""".strip()

# Added 2026-07-30. Fold consistency has been COMPUTED since PR #143 and
# persisted nowhere: it appeared on one terminal line ("folds=3/6") and was
# discarded. So the one measurement that answers "did the edge show up across
# year-sets, or in a couple of them" could never be read back, compared between
# strategies, or audited after the fact — which is most of what it was for.
#
# Defaults to '{}' for rows written before this column existed. Honestly "not
# recorded", never "zero folds".
ADD_EVALUATIONS_CONSISTENCY_METRICS_SQL = """
ALTER TABLE research.evaluations
ADD COLUMN IF NOT EXISTS consistency_metrics JSONB NOT NULL DEFAULT '{}'::jsonb
""".strip()

INSERT_EVALUATION_SQL = """
INSERT INTO research.evaluations (
    evaluation_id, strategy_id, spec_hash, protocol_version, verdict, reason,
    anchor_required, anchor_fresh, total_trades, from_stage, to_stage,
    aggregate_metrics, fold_metrics, stress_metrics, active_metrics,
    consistency_metrics, config, card_path, trigger, created_at
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
    $12::jsonb, $13::jsonb, $14::jsonb, $15::jsonb, $16::jsonb, $17::jsonb,
    $18, $19, $20
)
""".strip()

# The store reads its own table for the trigger's re-evaluation floor. Keyed on
# (strategy_id, spec_hash, protocol_version) rather than strategy_id alone,
# because a changed spec or a bumped protocol is a genuinely different question
# and should be re-asked immediately rather than waiting out the interval.
SELECT_LATEST_EVALUATION_AT_SQL = """
SELECT max(created_at) AS latest
FROM research.evaluations
WHERE strategy_id = $1 AND spec_hash = $2 AND protocol_version = $3
""".strip()

# The parent's most recent measured information ratio, for the worse-than-parent
# gate.
#
# `protocol_version` is in the WHERE clause and is not optional: a 0.1 result and
# a 0.2 result are different measurements (union panel, rebalancing benchmark,
# uncapped lookback), and comparing across them would kill a revision for losing
# to a number that was never comparable to it.
#
# `n_periods > 0` excludes refusals. `_empty_active` writes an information ratio
# of 0.0 when the engine never ran, so filtering on NULL would let a refusal
# stand in as "the parent scored zero" — and every revision would then look like
# an improvement.
SELECT_LATEST_ACTIVE_IR_SQL = """
SELECT (active_metrics->>'information_ratio')::float8 AS information_ratio
FROM research.evaluations
WHERE strategy_id = $1
  AND protocol_version = $2
  AND (active_metrics->>'n_periods')::int > 0
ORDER BY created_at DESC
LIMIT 1
""".strip()

# The multiple-testing denominator, counted as DRAWS rather than as rows.
#
# `registry.attempts` counted every strategy in a lineage, which over-counts:
# a revision killed on `insufficient-trades` or a dead anchor never sampled the
# hypothesis at all — the plumbing failed before the question was asked. Charging
# the survivor for that penalises it for a data problem rather than for a search.
#
# A draw requires BOTH that the engine ran (total_trades > 0) and that the
# verdict was a finding about edge rather than about the setup. The reason set
# is `ledger.EDGE_REASONS`, so the two places that classify an outcome cannot
# drift apart.
#
# DISTINCT because re-evaluating one strategy is the same draw measured again,
# not a new one — otherwise a re-run would raise the firm's own promote bar.
SELECT_DRAW_COUNT_SQL = """
SELECT count(DISTINCT strategy_id) AS draws
FROM research.evaluations
WHERE strategy_id = ANY($1::text[])
  AND total_trades > 0
  AND reason = ANY($2::text[])
""".strip()

# Read-only. The Evaluator consumes these tables; other agents own them.
SELECT_WORLD_CHANGER_STATUS_SQL = """
SELECT status FROM research.world_changers WHERE candidate_id = $1
""".strip()

SELECT_TICKER_TIER_SQL = """
SELECT tier FROM research.universe_tiers WHERE ticker = $1
""".strip()

# `source` is a predicate, not a filter of convenience. Before the feed became
# part of the bar key this query was correct by accident — the table held one
# feed, so "every row for this ticker" and "every IEX row for this ticker" were
# the same set. With two feeds stored they are not, and the difference does not
# announce itself: the panel would simply carry two bars per session and the
# backtest would run on it without complaint.
SELECT_DAILY_BARS_SQL = """
SELECT session_date, open, high, low, close, volume
FROM market_data.daily_bars
WHERE ticker = $1 AND adjustment = $2 AND session_date BETWEEN $3 AND $4
  AND source = $5
ORDER BY session_date
""".strip()

# Half-open on the upper bound, unlike the daily query's BETWEEN. A timestamp
# range closed at both ends would include a bar stamped exactly at the boundary
# instant, which belongs to the next window — the classic double-counted bar at
# a chunk seam. Dates do not have this problem; timestamps do.
SELECT_INTRADAY_BARS_SQL = """
SELECT bar_ts, open, high, low, close, volume
FROM market_data.intraday_bars
WHERE ticker = $1 AND adjustment = $2 AND timeframe = $3
  AND bar_ts >= $4 AND bar_ts < $5
  AND source = $6
ORDER BY bar_ts
""".strip()


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetchrow(self, sql: str, *args: object) -> Mapping[str, Any] | None: ...

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


class PostgresEvaluationStore:
    """Append-only sink for ``research.evaluations``."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_RESEARCH_SCHEMA_SQL)
            await conn.execute(CREATE_EVALUATIONS_TABLE_SQL)
            await conn.execute(ADD_EVALUATIONS_ANCHOR_REQUIRED_SQL)
            await conn.execute(ADD_EVALUATIONS_ACTIVE_METRICS_SQL)
            await conn.execute(ADD_EVALUATIONS_CONSISTENCY_METRICS_SQL)
            await conn.execute(CREATE_EVALUATIONS_STRATEGY_INDEX_SQL)

    async def insert_evaluation(
        self,
        *,
        evaluation_id: str,
        strategy_id: str,
        spec_hash: str,
        protocol_version: str,
        verdict: str,
        reason: str,
        anchor_required: bool,
        anchor_fresh: bool,
        total_trades: int,
        from_stage: str,
        to_stage: str | None,
        aggregate_metrics: dict[str, Any],
        fold_metrics: list[dict[str, Any]],
        stress_metrics: dict[str, Any],
        active_metrics: dict[str, Any],
        config: dict[str, Any],
        card_path: str | None,
        consistency_metrics: dict[str, Any] | None = None,
        trigger: str,
        created_at: datetime,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_EVALUATION_SQL,
                evaluation_id,
                strategy_id,
                spec_hash,
                protocol_version,
                verdict,
                reason,
                anchor_required,
                anchor_fresh,
                total_trades,
                from_stage,
                to_stage,
                json.dumps(aggregate_metrics, separators=(",", ":")),
                json.dumps(fold_metrics, separators=(",", ":")),
                json.dumps(stress_metrics, separators=(",", ":")),
                json.dumps(active_metrics, separators=(",", ":")),
                json.dumps(consistency_metrics or {}, separators=(",", ":")),
                json.dumps(config, separators=(",", ":")),
                card_path,
                trigger,
                created_at,
            )

    async def latest_evaluation_at(
        self, strategy_id: str, spec_hash: str, protocol_version: str
    ) -> datetime | None:
        """When this exact question was last asked, or ``None`` if never.

        The trigger's re-evaluation floor reads this. Returning ``None`` for a
        changed spec or protocol is intentional, not a miss: it is a different
        question and gets re-asked at once.
        """

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                SELECT_LATEST_EVALUATION_AT_SQL, strategy_id, spec_hash, protocol_version
            )
        if row is None:
            return None
        latest = row["latest"]
        return latest if isinstance(latest, datetime) else None


class PostgresEvaluatorReader:
    """Read-only consumer of market data and foreign anchor/tier tables.

    ``source`` is the feed this reader's bars come from. It is a property of the
    reader rather than an argument to :meth:`read_bars` because it is a property
    of the *panel*: a backtest that drew some sessions from IEX and others from
    SIP would not be a backtest of anything. One feed per reader, chosen once.
    """

    def __init__(self, pool: AsyncPool, *, source: str = DEFAULT_BAR_SOURCE) -> None:
        self._pool = pool
        self._source = source

    async def world_changer_status(self, candidate_id: str) -> str | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_WORLD_CHANGER_STATUS_SQL, candidate_id)
        return None if row is None else str(row["status"])

    async def ticker_tier(self, ticker: str) -> str | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_TICKER_TIER_SQL, ticker)
        return None if row is None else str(row["tier"])

    async def count_draws(self, strategy_ids: Sequence[str]) -> int:
        """How many of these strategies actually sampled the hypothesis.

        The honest multiple-testing denominator. Excludes lineage members whose
        evaluation never reached a backtest or died on a setup defect: those
        took no draw, so charging the survivor for them penalises a data problem
        rather than a search.

        Excludes the strategy being evaluated only if it has no prior edge
        evaluation — a re-run is the same draw, and counting it would let the
        firm raise its own bar by re-measuring.
        """

        if not strategy_ids:
            return 0
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                SELECT_DRAW_COUNT_SQL, list(strategy_ids), sorted(EDGE_REASONS)
            )
        if row is None or row["draws"] is None:
            return 0
        return int(row["draws"])

    async def latest_information_ratio(
        self, strategy_id: str, protocol_version: str
    ) -> float | None:
        """The strategy's most recent measured IR at this protocol, or None.

        None means "never measured comparably" — a strategy with no evaluation,
        or none since the protocol changed. The caller must treat that as "cannot
        compare" rather than as a score of zero.
        """

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(SELECT_LATEST_ACTIVE_IR_SQL, strategy_id, protocol_version)
        if row is None or row["information_ratio"] is None:
            return None
        return float(row["information_ratio"])

    async def read_shares(self, tickers: Sequence[str]) -> dict[str, list[tuple[date, float]]]:
        """Filed share counts per ticker, for the panel's market-cap series.

        Delegates to the module that owns the table rather than re-deriving the
        query. Returns ``{}`` when nothing is filed, which the panel reads as
        "no market cap for this name" — never as zero.
        """

        return await PostgresSharesStore(self._pool).shares_history(tickers)

    async def read_fundamentals(
        self, tickers: Sequence[str]
    ) -> dict[str, dict[str, list[tuple[date, date, float]]]]:
        """Filed accounting figures per ticker and metric, for the panel.

        Delegates to the module that owns the table. Empty when the backfill has
        not run, which the panel reads as "unknown" for every name.
        """

        return await PostgresFundamentalsStore(self._pool).history(tickers)

    async def read_bars(
        self, ticker: str, start: date, end: date, adjustment: str
    ) -> list[BarSample]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                SELECT_DAILY_BARS_SQL, ticker, adjustment, start, end, self._source
            )
        return [
            BarSample(
                session_date=row["session_date"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            for row in rows
        ]


class PostgresIntradayBarReader:
    """Reads ``market_data.intraday_bars`` as a :class:`BarSample` panel.

    Satisfies the same ``BarReader`` shape the Runner and Evaluator already
    depend on, so nothing downstream learns that a panel is intraday. The bars
    carry a ``datetime`` in ``BarSample.session_date`` rather than a ``date``;
    ``datetime`` is a subclass of ``date`` and the panel only ever uses the field
    as a sortable, hashable key, so ``PricePanel`` aligns a 5-minute grid exactly
    as it aligns a daily one.

    **Do not mix grains in one panel.** Comparing a ``date`` with a ``datetime``
    raises ``TypeError``, so a panel built from both would fail on its first
    sort rather than produce a wrong answer — loud, but only at the top of a
    long backtest. One reader per panel.

    **Regular hours are the default and extended hours are opt-in.** The
    backfill stores what Alpaca returns, which spans 04:00-20:00 ET; a consumer
    that forgets is trading four-in-the-morning IEX prints, where a handful of
    shares sets a price no strategy could have traded at size. ``include_extended``
    exists because the data is legitimately there for whoever wants it, but it
    is never the default.
    """

    def __init__(
        self,
        pool: AsyncPool,
        *,
        timeframe: str,
        include_extended: bool = False,
        calendar_name: str = DEFAULT_CALENDAR,
        source: str = DEFAULT_BAR_SOURCE,
    ) -> None:
        self._pool = pool
        self._timeframe = timeframe
        self._source = source
        self._include_extended = include_extended
        self._calendar_name = calendar_name
        self._tz = ZoneInfo(DEFAULT_TIMEZONE)
        # One-entry cache: the Runner reads every ticker over the same window, so
        # the calendar is consulted once per pass rather than once per name.
        self._bounds_window: tuple[date, date] | None = None
        self._bounds: dict[date, tuple[datetime, datetime]] = {}

    def _session_bounds(self, start: date, end: date) -> dict[date, tuple[datetime, datetime]]:
        if self._bounds_window != (start, end):
            self._bounds = regular_session_bounds(start, end, calendar_name=self._calendar_name)
            self._bounds_window = (start, end)
        return self._bounds

    async def read_bars(
        self, ticker: str, start: date, end: date, adjustment: str
    ) -> list[BarSample]:
        # Exchange-local midnight to midnight-after-``end``, so the window is a
        # whole number of sessions however the caller's dates were derived.
        begin_utc = datetime.combine(start, time(0, 0), tzinfo=self._tz)
        finish_utc = datetime.combine(end + timedelta(days=1), time(0, 0), tzinfo=self._tz)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                SELECT_INTRADAY_BARS_SQL,
                ticker,
                adjustment,
                self._timeframe,
                begin_utc,
                finish_utc,
                self._source,
            )
        bounds = {} if self._include_extended else self._session_bounds(start, end)
        samples: list[BarSample] = []
        for row in rows:
            bar_ts = row["bar_ts"]
            if not self._include_extended and not is_regular_hours(bar_ts, bounds):
                continue
            samples.append(
                BarSample(
                    session_date=bar_ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
        return samples


class IntradayEvaluatorReader:
    """A :class:`PostgresEvaluatorReader` whose bars come from an intraday panel.

    The Evaluator's ``ReaderPort`` has five methods and only ``read_bars`` differs
    by bar grain — anchor freshness, tier eligibility, the multiple-testing draw
    count and the parent's information ratio are all questions about a strategy,
    not about a timeframe. So this delegates four and overrides one, rather than
    growing a mode flag inside either reader.

    It exists so the question this whole line of work rests on can actually be
    asked: **does a strategy's per-decision skill survive a shorter horizon?**
    ``IR = IC x sqrt(breadth)`` says 78 decisions a day is worth ~8.8x the
    information ratio of one a day *if* IC holds, and short-horizon prediction is
    usually harder while costs scale with turnover. Running the same strategy
    through the same walk-forward at two grains and comparing the two ratios is
    the measurement that settles it. Nothing else here is new for that reason.
    """

    def __init__(
        self,
        pool: AsyncPool,
        *,
        timeframe: str,
        include_extended: bool = False,
        calendar_name: str = DEFAULT_CALENDAR,
        source: str = DEFAULT_BAR_SOURCE,
    ) -> None:
        # Both halves take the same feed. The delegate answers questions about a
        # strategy rather than about bars, but it also owns the daily
        # `read_bars` this class overrides, and a delegate left on the default
        # feed would be a quiet second opinion about what the market did.
        self._delegate = PostgresEvaluatorReader(pool, source=source)
        self._bars = PostgresIntradayBarReader(
            pool,
            timeframe=timeframe,
            include_extended=include_extended,
            calendar_name=calendar_name,
            source=source,
        )
        self.timeframe = timeframe
        self.source = source

    async def world_changer_status(self, candidate_id: str) -> str | None:
        return await self._delegate.world_changer_status(candidate_id)

    async def ticker_tier(self, ticker: str) -> str | None:
        return await self._delegate.ticker_tier(ticker)

    async def latest_information_ratio(
        self, strategy_id: str, protocol_version: str
    ) -> float | None:
        return await self._delegate.latest_information_ratio(strategy_id, protocol_version)

    async def count_draws(self, strategy_ids: Sequence[str]) -> int:
        return await self._delegate.count_draws(strategy_ids)

    async def read_shares(self, tickers: Sequence[str]) -> dict[str, list[tuple[date, float]]]:
        """Delegated: a share count is a fact about a company, not a bar grain.

        Market cap on an intraday panel is the same ``close x shares`` with a
        finer close, and the counts themselves are filed quarterly either way.
        """

        return await self._delegate.read_shares(tickers)

    async def read_bars(
        self, ticker: str, start: date, end: date, adjustment: str
    ) -> list[BarSample]:
        return await self._bars.read_bars(ticker, start, end, adjustment)


__all__ = [
    "ADD_EVALUATIONS_ACTIVE_METRICS_SQL",
    "ADD_EVALUATIONS_ANCHOR_REQUIRED_SQL",
    "ADD_EVALUATIONS_CONSISTENCY_METRICS_SQL",
    "CREATE_EVALUATIONS_TABLE_SQL",
    "CREATE_RESEARCH_SCHEMA_SQL",
    "INSERT_EVALUATION_SQL",
    "SELECT_DAILY_BARS_SQL",
    "SELECT_DRAW_COUNT_SQL",
    "SELECT_INTRADAY_BARS_SQL",
    "SELECT_LATEST_ACTIVE_IR_SQL",
    "SELECT_LATEST_EVALUATION_AT_SQL",
    "SELECT_TICKER_TIER_SQL",
    "SELECT_WORLD_CHANGER_STATUS_SQL",
    "AsyncPool",
    "IntradayEvaluatorReader",
    "PostgresEvaluationStore",
    "PostgresEvaluatorReader",
    "PostgresIntradayBarReader",
]
