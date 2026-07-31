"""Market-data trigger: the sweep that keeps the price panel advancing without Mike.

``market-data`` shipped as a ``--profile tools`` CLI, so bars only ever moved
when a human typed a command. On 2026-07-31 a systems check found
``market_data.daily_bars`` two sessions stale, and the cost was not the missing
rows — it was that **every evaluation since had read a panel that stopped
advancing, and the Evaluator's most common verdict is ``hold-for-data``** (13 of
22 under protocol 0.2). A verdict that says "not enough data" became
indistinguishable from one caused by nobody running a job. That is KI-024, and
it is the reason this is a service rather than a cron line in a runbook.

The design is the smallest thing that actually works, reusing the pieces that
already exist rather than restating them:

**The tickers come from the launch list, not from configuration.** The backfill
CLI's ``--launch-list`` already resolves the Curator's Tier-3 names and, in its
own words, "cannot drift out of step with the universe." A trigger with its own
ticker list would be a second source of truth that silently diverges.

**The window is computed per ticker, not configured.** :func:`plan_windows`
asks the store what it already has and requests only the gap. In steady state
all names share one last session, so the plan collapses to a single window over
the whole universe — one call shape, not fifty. A ticker with no history at all
gets the bootstrap lookback instead, so onboarding a new name does not need a
separate command and cannot quietly produce a series that starts today.

**It re-requests a few days it already has.** ``adjustment=all`` means splits
and dividends restate history, so the most recent bars are not final when first
written. ``restate_days`` is the width of that correction window; the upsert is
idempotent and refreshing a handful of rows per ticker is cheaper than being
subtly wrong about a split.

**What it deliberately is not.** There is no market-calendar awareness. A sweep
on a Sunday asks Alpaca for a window containing no sessions and writes nothing,
which costs one HTTP call per ticker and no correctness. Adding calendar logic
would couple this to the Market Phase Scheduler to save a request that does not
matter, and the failure mode of a wrong calendar (silently skipping a real
session) is far worse than the failure mode of a wasted call.

**Freshness is checked separately and that is the point.** This service refreshes
``fetched_at`` on every upsert, and ``shrap.operations.staleness`` carries a
target for ``market_data.daily_bars`` naming this producer. A trigger that dies
quietly is the failure this card exists to end, so the card ships the alarm
too — one without the other reproduces KI-024 with extra steps.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import cast

import httpx
import structlog

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.intelligence.market_data import AlpacaMarketDataSettings
from shrap.market_data.backfill import BackfillSummary, backfill_tickers
from shrap.market_data.client import AlpacaDailyBarsClient
from shrap.market_data.config import TriggerSettings
from shrap.market_data.store import PostgresDailyBarStore
from shrap.trading_floor.alpaca import AsyncHttpClient

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WindowPlan:
    """One fetch window and every ticker that shares it."""

    start_day: str
    end_day: str
    tickers: tuple[str, ...]


def plan_windows(
    last_sessions: Mapping[str, date],
    tickers: Sequence[str],
    today: date,
    *,
    restate_days: int,
    bootstrap_days: int,
) -> tuple[WindowPlan, ...]:
    """Group ``tickers`` into the smallest set of windows that closes their gaps.

    A ticker already in the store is fetched from ``last_session - restate_days``
    so late adjustments land; a ticker the store has never seen is fetched from
    ``today - bootstrap_days``. Tickers sharing a start day share a window, so
    the steady state — every name last stored on the same session — produces
    exactly one plan rather than one per ticker.

    Returns windows sorted by start day, each with its tickers in the caller's
    order. An empty ``tickers`` yields no plans; a start day after ``today``
    is clamped to ``today`` so a clock skew cannot invert the window.
    """

    grouped: dict[date, list[str]] = {}
    for ticker in tickers:
        last = last_sessions.get(ticker)
        if last is None:
            start = today - timedelta(days=bootstrap_days)
        else:
            start = last - timedelta(days=restate_days)
        if start > today:
            start = today
        grouped.setdefault(start, []).append(ticker)

    return tuple(
        WindowPlan(
            start_day=start.isoformat(),
            end_day=today.isoformat(),
            tickers=tuple(names),
        )
        for start, names in sorted(grouped.items())
    )


def launch_list_tickers() -> tuple[str, ...]:
    """The Curator's Tier-3 names, the same source ``--launch-list`` resolves.

    Imported lazily for the reason the backfill CLI gives: this is a market-data
    tool and should not fail to start because a Research module moved.
    """

    from shrap.research.universe_curator.launch_list import LAUNCH_LIST

    return tuple(sorted(entry.ticker for entry in LAUNCH_LIST))


async def run_sweep(
    store: PostgresDailyBarStore,
    client: AlpacaDailyBarsClient,
    http: AsyncHttpClient,
    tickers: Sequence[str],
    today: date,
    *,
    restate_days: int,
    bootstrap_days: int,
    dry_run: bool = False,
    request_limit: int = 10000,
    inter_ticker_delay_seconds: float = 0.3,
) -> BackfillSummary:
    """One incremental pass: read what exists, fetch only the gap, upsert.

    Returns the aggregate across every window, so a caller logging one line per
    sweep reports the whole pass rather than its last window.
    """

    last_sessions = await store.last_session_by_ticker()
    plans = plan_windows(
        last_sessions,
        tickers,
        today,
        restate_days=restate_days,
        bootstrap_days=bootstrap_days,
    )

    total_fetched = 0
    total_upserted = 0
    for plan in plans:
        log.info(
            "market_data_trigger.window",
            start_day=plan.start_day,
            end_day=plan.end_day,
            tickers=len(plan.tickers),
            bootstrap=not any(t in last_sessions for t in plan.tickers),
        )
        summary = await backfill_tickers(
            store,
            client,
            http,
            list(plan.tickers),
            plan.start_day,
            plan.end_day,
            dry_run=dry_run,
            request_limit=request_limit,
            inter_ticker_delay_seconds=inter_ticker_delay_seconds,
        )
        total_fetched += summary.rows_fetched
        total_upserted += summary.rows_upserted

    return BackfillSummary(
        tickers=len(tickers),
        rows_fetched=total_fetched,
        rows_upserted=total_upserted,
        dry_run=dry_run,
    )


async def serve(settings: TriggerSettings, credentials: AlpacaMarketDataSettings) -> None:
    """Sweep on an interval until cancelled.

    A failing sweep logs and waits for the next one rather than exiting. The
    daily bars are not urgent to the minute and a restart loop against a broker
    outage would be worse than a gap — but the failure is loud, and the
    freshness target for ``market_data.daily_bars`` alarms independently if the
    gap persists, so a silently dead sweep is not the failure mode it used to be.
    """

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)

    tickers = launch_list_tickers()
    pool = await create_asyncpg_pool(settings.postgres_dsn.get_secret_value())
    store = PostgresDailyBarStore(pool)
    await store.ensure_schema()
    client = AlpacaDailyBarsClient(credentials, feed=settings.feed, adjustment=settings.adjustment)

    log.info(
        "market_data_trigger.started",
        alpaca=credentials.redacted(),
        tickers=len(tickers),
        interval_seconds=settings.sweep_interval_seconds,
        restate_days=settings.restate_days,
        bootstrap_days=settings.bootstrap_days,
    )

    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout) as http:
            while not stopping.is_set():
                try:
                    summary = await run_sweep(
                        store,
                        client,
                        cast(AsyncHttpClient, http),
                        tickers,
                        datetime.now(UTC).date(),
                        restate_days=settings.restate_days,
                        bootstrap_days=settings.bootstrap_days,
                        request_limit=settings.request_limit,
                        inter_ticker_delay_seconds=settings.inter_ticker_delay_seconds,
                    )
                except Exception:
                    log.exception("market_data_trigger.sweep_failed")
                else:
                    log.info(
                        "market_data_trigger.sweep_complete",
                        tickers=summary.tickers,
                        rows_fetched=summary.rows_fetched,
                        rows_upserted=summary.rows_upserted,
                    )
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stopping.wait(), timeout=settings.sweep_interval_seconds)
    finally:
        await pool.close()
        log.info("market_data_trigger.stopped")


def main() -> None:
    settings = TriggerSettings()
    configure_logging(settings.service_name, settings.log_level)
    asyncio.run(serve(settings, AlpacaMarketDataSettings()))


if __name__ == "__main__":
    main()
