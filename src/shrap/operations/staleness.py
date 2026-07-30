"""Output staleness: does the firm's work actually appear in its tables?

Every monitoring signal the firm had before this module asked a service how it
was doing. Three failures in one session answered "fine" while producing
nothing:

- The News Analyzer had never fetched a single item. Every poll returned HTTP
  400; the loop caught it, logged it, slept, and reported a healthy pass.
  ``intelligence.news_items`` was empty for the agent's entire deployed life.
- The Hypothesis Generator was never triggered. The container was up, idle, and
  correct about being idle.
- The literature filter kept nothing, for a prompt reason, while reporting a
  successful pass over every item.

A per-service counter cannot catch any of those, because in all three the
service's own view of itself was accurate. The question that catches all three
is asked from outside: **did output appear?**

So this module never talks to an agent. It reads the tables the agents are
supposed to be filling, and it treats two conditions as alarms:

``no-rows``
    The table exists and is empty. This is the loudest signal in the module and
    the reason for its name — a producer that has been deployed for days with
    an empty output table has never worked. Zero is not "quiet"; zero is an
    alarm. Classified ``down``.

``stale``
    The table has rows, but the newest is older than the target's
    ``max_age``. The producer worked once and stopped. Classified ``degraded``.

Thresholds are **first cuts, not rulings** (see the table in
:data:`DEFAULT_TARGETS`). Each one is set longer than the longest legitimate
quiet period the producer has, so that firing means broken rather than
weekend. Every target carries the reasoning as data, and the reasoning travels
with the alarm — an alert that cannot say why its threshold is what it is
teaches the operator to ignore it.

What this module cannot do:

- It cannot tell "the producer is broken" from "there was genuinely nothing to
  produce". For ``intelligence.filings`` and ``research.evaluations`` a quiet
  period is legitimate; the threshold is where quiet stops being plausible, and
  that judgement is a guess until Mike rules on it.
- It cannot see output that is written somewhere other than these tables.
  Coverage is exactly :data:`DEFAULT_TARGETS` and nothing else.
- It says nothing about whether the rows are *correct*. A producer writing
  garbage on schedule reads as ``ok`` here. Freshness is a floor, not a proof.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)

# Table and column names are interpolated into SQL — asyncpg cannot parameterize
# identifiers. Every identifier in this module comes from the code-owned
# registry below, never from input, and this pattern is enforced at construction
# so that stays true if someone adds a target later.
_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

Status = str  # "ok" | "degraded" | "down" — matches the Health Monitor's convention.


@dataclass(frozen=True, slots=True)
class FreshnessTarget:
    """One table whose growth is evidence that a producer is working.

    ``max_age`` is the oldest the newest row may be before the target is
    considered stale. ``rationale`` explains the number and is published with
    the anomaly, so the alert can justify itself to whoever it wakes.
    """

    name: str
    schema: str
    table: str
    timestamp_column: str
    producer: str
    max_age: timedelta
    rationale: str

    def __post_init__(self) -> None:
        for part in (self.schema, self.table, self.timestamp_column):
            if not _IDENTIFIER_RE.match(part):
                raise ValueError(
                    f"unsafe SQL identifier in freshness target {self.name!r}: {part!r}"
                )
        if self.max_age <= timedelta(0):
            raise ValueError(f"max_age must be positive for freshness target {self.name!r}")

    @property
    def qualified_table(self) -> str:
        return f"{self.schema}.{self.table}"


# The five tables that must grow for the firm to be doing anything, with the
# cadence each threshold was derived from. Every number here is an unruled first
# cut: set to clear the producer's longest legitimate quiet period, so a firing
# check means broken rather than weekend.
#
# Deliberately absent: ``market_data.daily_bars``. It is the Evaluator's
# backtest corpus, filled by an on-demand backfill behind the compose "tools"
# profile — it is *supposed* to sit unchanged for weeks, so an age threshold
# over it would only ever produce noise.
DEFAULT_TARGETS: tuple[FreshnessTarget, ...] = (
    FreshnessTarget(
        name="research.raw_source_items",
        schema="research",
        table="raw_source_items",
        timestamp_column="fetched_at",
        producer="tech-watcher",
        max_age=timedelta(hours=6),
        rationale=(
            "Tech Watcher ingests hourly (TECH_WATCHER_INTERVAL_SECONDS=3600) across five "
            "sources. Six hours is six consecutive passes in which EDGAR, arXiv, "
            "USASpending, DOE and the Federal Register all returned nothing."
        ),
    ),
    FreshnessTarget(
        name="intelligence.news_items",
        schema="intelligence",
        table="news_items",
        timestamp_column="fetched_at",
        producer="news-analyzer",
        max_age=timedelta(hours=24),
        rationale=(
            "News Analyzer polls hourly when idle, every ten minutes when active, over nine "
            "of the most-covered symbols in the market. Twenty-four hours clears a thin "
            "weekend; the empty-table rule, not this threshold, is what catches the HTTP 400 "
            "failure that motivated the check."
        ),
    ),
    FreshnessTarget(
        name="intelligence.filings",
        schema="intelligence",
        table="filings",
        timestamp_column="discovered_at",
        producer="filing-processor",
        max_age=timedelta(days=5),
        rationale=(
            "8-K discovery is scoped to a four-name Tier 3 roster and bounded by business "
            "days: a small roster can legitimately file nothing for days. Five days clears "
            "the longest US market closure (holiday Friday plus a weekend) with a day spare."
        ),
    ),
    FreshnessTarget(
        name="market_data.ohlcv_1d",
        schema="market_data",
        table="ohlcv_1d",
        timestamp_column="updated_at",
        producer="regime-classifier",
        max_age=timedelta(hours=1),
        rationale=(
            "The Regime Classifier re-pulls and re-upserts the last stored day every 300s, "
            "refreshing updated_at on every pass. This measures pass liveness, not bar "
            "availability, so it is unaffected by weekends: one hour is twelve missed passes."
        ),
    ),
    FreshnessTarget(
        name="research.evaluations",
        schema="research",
        table="evaluations",
        timestamp_column="created_at",
        producer="strategy-evaluator-trigger",
        max_age=timedelta(days=7),
        rationale=(
            "The trigger re-evaluates every 24h but only when something is eligible, so an "
            "empty research pipeline is legitimately quiet. Seven days says the firm has "
            "judged nothing for a week — a research-throughput alarm, not an infra one."
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class FreshnessReading:
    """What the database said about one target. No judgement applied yet."""

    table_exists: bool
    has_rows: bool
    last_row_at: datetime | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class FreshnessVerdict:
    """A reading classified against its target's threshold."""

    target: FreshnessTarget
    reading: FreshnessReading
    status: Status
    reason: str
    age_seconds: float | None

    def evidence(self) -> dict[str, Any]:
        """Flat, log- and envelope-safe detail. Carries the rationale with it."""

        return {
            "table": self.target.qualified_table,
            "column": self.target.timestamp_column,
            "producer": self.target.producer,
            "reason": self.reason,
            "table_exists": self.reading.table_exists,
            "has_rows": self.reading.has_rows,
            "last_row_at": (
                self.reading.last_row_at.isoformat() if self.reading.last_row_at else None
            ),
            "age_seconds": self.age_seconds,
            "max_age_seconds": self.target.max_age.total_seconds(),
            "rationale": self.target.rationale,
            **({"error": self.reading.error} if self.reading.error else {}),
        }


class StalenessStore(Protocol):
    async def read(self, target: FreshnessTarget) -> FreshnessReading: ...


def classify(target: FreshnessTarget, reading: FreshnessReading, now: datetime) -> FreshnessVerdict:
    """Judge one reading. The empty-table case is the sharp one.

    ``down`` is reserved for "this producer has never written anything", which
    is strictly worse than "it wrote something and stopped" (``degraded``) — a
    producer that has never worked was never going to start on its own.
    """

    def verdict(status: Status, reason: str, age: float | None = None) -> FreshnessVerdict:
        return FreshnessVerdict(
            target=target, reading=reading, status=status, reason=reason, age_seconds=age
        )

    if reading.error is not None:
        return verdict("degraded", "query-failed")
    if not reading.table_exists:
        # The producer never ran here at all: every one of these tables is
        # created by its own producer's ensure_schema() on first start.
        return verdict("degraded", "table-missing")
    if not reading.has_rows:
        return verdict("down", "no-rows")
    if reading.last_row_at is None:
        # Rows exist but the timestamp column is entirely NULL. Pathological
        # rather than expected; report it rather than reading it as fresh.
        return verdict("degraded", "no-timestamp")

    age = (now - reading.last_row_at).total_seconds()
    if age > target.max_age.total_seconds():
        return verdict("degraded", "stale", age)
    return verdict("ok", "fresh", age)


async def sweep(
    store: StalenessStore,
    targets: Sequence[FreshnessTarget],
    now: datetime,
) -> list[FreshnessVerdict]:
    """Read and classify every target. One failing target never aborts the rest."""

    verdicts: list[FreshnessVerdict] = []
    for target in targets:
        try:
            reading = await store.read(target)
        except Exception as e:
            log.exception("staleness.read_failed", target=target.name)
            reading = FreshnessReading(
                table_exists=False, has_rows=False, last_row_at=None, error=str(e)[:500]
            )
        verdicts.append(classify(target, reading, now))
    return verdicts


# ---------------------------------------------------------------------------
# Postgres store
# ---------------------------------------------------------------------------

# to_regclass returns NULL for a table that does not exist, which is the only
# way to ask without a failed parse taking the connection's transaction with it.
_TABLE_EXISTS_SQL = "SELECT to_regclass($1) IS NOT NULL AS table_exists"


def _reading_sql(target: FreshnessTarget) -> str:
    """Newest timestamp and row presence in one round trip.

    ``max()`` returns NULL both for an empty table and for a table whose column
    is entirely NULL; the EXISTS tells those apart, and it stops at the first
    row rather than counting them.
    """

    table = target.qualified_table
    return (
        f"SELECT (SELECT max({target.timestamp_column}) FROM {table}) AS last_row_at, "
        f"EXISTS (SELECT 1 FROM {table}) AS has_rows"
    )


class AsyncConnection(Protocol):
    async def fetchrow(self, sql: str, *args: object) -> Any: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


class PostgresStalenessStore:
    """Read-only freshness probe. Creates nothing, writes nothing, locks nothing."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def read(self, target: FreshnessTarget) -> FreshnessReading:
        async with self._pool.acquire() as conn:
            exists_row = await conn.fetchrow(_TABLE_EXISTS_SQL, target.qualified_table)
            if not (exists_row and exists_row["table_exists"]):
                return FreshnessReading(table_exists=False, has_rows=False, last_row_at=None)
            row = await conn.fetchrow(_reading_sql(target))

        if row is None:
            return FreshnessReading(table_exists=True, has_rows=False, last_row_at=None)
        last_row_at = row["last_row_at"]
        if last_row_at is not None and last_row_at.tzinfo is None:
            # Every column in the registry is TIMESTAMPTZ, so this should not
            # happen — but a naive datetime compared against an aware `now`
            # raises, and taking down the monitor over it would be the worst
            # possible trade.
            last_row_at = last_row_at.replace(tzinfo=UTC)
        return FreshnessReading(
            table_exists=True, has_rows=bool(row["has_rows"]), last_row_at=last_row_at
        )


__all__ = [
    "DEFAULT_TARGETS",
    "FreshnessReading",
    "FreshnessTarget",
    "FreshnessVerdict",
    "PostgresStalenessStore",
    "StalenessStore",
    "classify",
    "sweep",
]
