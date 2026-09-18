"""Intraday trigger: the sweep that keeps the intraday panel advancing without Mike.

The daily sibling of this service exists because ``market_data.daily_bars`` went
two sessions stale on 2026-07-31 and nothing noticed (KI-024). This one exists
because ``market_data.intraday_bars`` did the same thing and nothing noticed
again: the table was written once by a hand-run backfill, and on 2026-09-18 its
newest bar was **2026-09-16 16:45 ET**, two sessions old, while the
``market-data-trigger`` beside it swept ``1Day`` on schedule.

**The failure this prevents is worse at this grain than at a daily one.** A
stale daily panel produces a stale daily decision — one wrong answer a day. A
stale intraday panel produces a strategy that wakes every 15 minutes, reads a
panel that has not moved, recomputes an identical ranking, and re-emits
identical targets. The Runner's own guard does not catch it: the slot changed,
so the decision is legitimately new. Nothing raises, nothing repeats in the
logs, and the strategy is not trading on old data in a way anyone could see —
it is trading on old data at full speed.

**Which grains are swept is read, not configured.** The daily trigger already
argues this about tickers: *"A trigger with its own ticker list would be a
second source of truth that silently diverges."* A configured timeframe is the
same hazard one field over. A strategy declaring ``interval_minutes: 5`` while
this service sweeps ``15Min`` gets zero rows — an empty panel, a skipped
strategy, and no error anywhere. So the grains come from what live strategies
actually declare (:func:`declared_timeframes`), unioned with a baseline grain
kept warm so the Evaluator can always backtest against recent data.

**The sweep interval is derived from the finest grain, and that is the point.**
A 15-minute strategy against a table refreshed every 30 minutes re-decides on an
unchanged panel every other slot. :func:`resolve_interval` treats the configured
interval as a *ceiling* and lowers it to half the finest declared grain, floored
at one minute because that is Alpaca's finest bar. An operator cannot configure
this service into the failure it exists to prevent.

**No market-calendar awareness**, for the reason the daily trigger gives: a
sweep outside session hours asks Alpaca for a window with no new bars and writes
nothing, costing one request per ticker and no correctness, whereas a wrong
calendar silently skips a real session. Fifty names every five minutes is ~14k
requests a day against a 200/minute allowance.

**Freshness is alarmed separately**, because shipping a sweep without one
reproduces KI-024 with extra steps. ``shrap.operations.staleness`` carries a
target for ``market_data.intraday_bars`` naming this producer.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import signal
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import cast

import httpx
import structlog

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.intelligence.market_data import AlpacaMarketDataSettings
from shrap.market_data.backfill import BackfillSummary
from shrap.market_data.client import AlpacaIntradayBarsClient
from shrap.market_data.config import IntradayTriggerSettings
from shrap.market_data.intraday_backfill import backfill_intraday
from shrap.market_data.store import PostgresIntradayBarStore
from shrap.market_data.trigger_service import launch_list_tickers, plan_windows
from shrap.trading_floor.alpaca import AsyncHttpClient

log = structlog.get_logger(__name__)

_TIMEFRAME_RE = re.compile(r"^(\d+)Min$")

# Statuses whose strategies are live enough to be reading bars. Mirrors the
# Runner's ACTIVE_PAPER_STAGES: a killed strategy's grain is not worth fetching,
# and a strategy that has not reached paper is not reading this table live.
LIVE_STATUSES: tuple[str, ...] = ("paper", "active")


def timeframe_minutes(timeframe: str) -> int | None:
    """Minutes in an Alpaca intraday timeframe token, or None if it is not one.

    ``"1Day"`` returns None rather than 1440: a daily grain is the other
    trigger's business, and returning a number here would let it set this
    service's sweep interval.
    """

    match = _TIMEFRAME_RE.match(timeframe.strip())
    return int(match.group(1)) if match else None


async def declared_timeframes(pool: object, baseline: str) -> tuple[str, ...]:
    """The intraday grains live strategies declare, plus the baseline.

    Reads the registry rather than configuration so the swept grain cannot
    diverge from the grain a strategy is actually reading. A daily strategy
    contributes nothing — ``alpaca_timeframe`` maps it to ``"1Day"``, which
    :func:`timeframe_minutes` rejects.

    **Never raises.** A registry that cannot be read yields the baseline alone:
    a market-data service must not fail to start because a Research table moved,
    and sweeping one grain is strictly better than sweeping none. The fallback is
    logged at warning so it cannot be mistaken for "no strategy declared one".
    """

    from shrap.research.strategy_runner.cadence import alpaca_timeframe, read_cadence

    found: set[str] = set()
    try:
        async with pool.acquire() as conn:  # type: ignore[attr-defined]
            rows = await conn.fetch(
                "SELECT spec FROM research.strategies WHERE status = ANY($1::text[])",
                list(LIVE_STATUSES),
            )
        for row in rows:
            spec = row["spec"]
            if isinstance(spec, str):
                import json

                spec = json.loads(spec)
            token = alpaca_timeframe(read_cadence(spec))
            if timeframe_minutes(token) is not None:
                found.add(token)
    except Exception:
        log.warning("market_data_intraday_trigger.registry_unreadable", exc_info=True)
        return (baseline,)

    if timeframe_minutes(baseline) is not None:
        found.add(baseline)
    # Finest first, so logs read in the order that matters for the interval.
    return tuple(sorted(found, key=lambda t: timeframe_minutes(t) or 0))


def resolve_interval(
    timeframes: Sequence[str], *, ceiling_seconds: float, floor_seconds: float
) -> float:
    """How often to sweep, given the grains being swept.

    Half the finest grain, so every completed bar is fetched within half a bar
    of completing — the configured value is a ceiling and never a way to sweep
    more slowly than the data changes. Floored at ``floor_seconds`` because
    below one minute there is no new bar to fetch.
    """

    finest = min(
        (m for m in (timeframe_minutes(t) for t in timeframes) if m is not None),
        default=None,
    )
    target = ceiling_seconds if finest is None else min(ceiling_seconds, finest * 60 / 2)
    return max(target, floor_seconds)


async def run_sweep(
    store: PostgresIntradayBarStore,
    client: AlpacaIntradayBarsClient,
    http: AsyncHttpClient,
    tickers: Sequence[str],
    today: date,
    timeframes: Sequence[str],
    *,
    restate_days: int,
    bootstrap_days: int,
    dry_run: bool = False,
    request_limit: int = 10000,
    inter_request_delay_seconds: float = 0.3,
) -> BackfillSummary:
    """One incremental pass per grain: read what exists, fetch the gap, upsert.

    Grains are swept in sequence rather than concurrently. Fifty names at two
    grains is a hundred requests; running them together would buy a few seconds
    and make the rate-limit behaviour depend on how many strategies happen to be
    live.
    """

    total_fetched = 0
    total_upserted = 0
    for timeframe in timeframes:
        last_bars = await store.last_bar_by_ticker(timeframe)
        # plan_windows reasons in sessions; a bar timestamp is a moment inside
        # one. Truncating to its date re-requests the whole of the last stored
        # session, which is what `restate_days` already assumes and what makes
        # the in-progress session's provisional final bar get corrected.
        last_sessions = {ticker: at.date() for ticker, at in last_bars.items()}
        plans = plan_windows(
            last_sessions,
            tickers,
            today,
            restate_days=restate_days,
            bootstrap_days=bootstrap_days,
        )
        for plan in plans:
            log.info(
                "market_data_intraday_trigger.window",
                timeframe=timeframe,
                start_day=plan.start_day,
                end_day=plan.end_day,
                tickers=len(plan.tickers),
                bootstrap=not any(t in last_sessions for t in plan.tickers),
            )
            summary = await backfill_intraday(
                store,
                client,
                http,
                list(plan.tickers),
                plan.start_day,
                plan.end_day,
                dry_run=dry_run,
                timeframe=timeframe,
                request_limit=request_limit,
                inter_request_delay_seconds=inter_request_delay_seconds,
            )
            total_fetched += summary.rows_fetched
            total_upserted += summary.rows_upserted

    return BackfillSummary(
        tickers=len(tickers),
        rows_fetched=total_fetched,
        rows_upserted=total_upserted,
        dry_run=dry_run,
    )


async def serve(settings: IntradayTriggerSettings, credentials: AlpacaMarketDataSettings) -> None:
    """Sweep on a derived interval until cancelled.

    A failing sweep logs and waits for the next rather than exiting, matching
    the daily trigger: a restart loop against a broker outage is worse than a
    gap, and the freshness target alarms independently if the gap persists.

    The grain set and the interval are both re-resolved every pass, so promoting
    an intraday strategy takes effect on the next sweep rather than on the next
    deploy. That matters: the alternative is a strategy going live against a
    grain nobody is fetching, which is the failure this service exists to
    prevent, arriving through the back door.
    """

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)

    tickers = launch_list_tickers()
    pool = await create_asyncpg_pool(settings.postgres_dsn.get_secret_value())
    store = PostgresIntradayBarStore(pool)
    await store.ensure_schema()
    client = AlpacaIntradayBarsClient(
        credentials, feed=settings.feed, adjustment=settings.adjustment
    )

    log.info(
        "market_data_intraday_trigger.started",
        alpaca=credentials.redacted(),
        tickers=len(tickers),
        interval_ceiling_seconds=settings.sweep_interval_seconds,
        baseline_timeframe=settings.baseline_timeframe,
        restate_days=settings.restate_days,
        bootstrap_days=settings.bootstrap_days,
    )

    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout) as http:
            while not stopping.is_set():
                timeframes = await declared_timeframes(pool, settings.baseline_timeframe)
                interval = resolve_interval(
                    timeframes,
                    ceiling_seconds=settings.sweep_interval_seconds,
                    floor_seconds=settings.min_sweep_interval_seconds,
                )
                try:
                    summary = await run_sweep(
                        store,
                        client,
                        cast(AsyncHttpClient, http),
                        tickers,
                        datetime.now(UTC).date(),
                        timeframes,
                        restate_days=settings.restate_days,
                        bootstrap_days=settings.bootstrap_days,
                        request_limit=settings.request_limit,
                        inter_request_delay_seconds=settings.inter_ticker_delay_seconds,
                    )
                except Exception:
                    log.exception("market_data_intraday_trigger.sweep_failed")
                else:
                    log.info(
                        "market_data_intraday_trigger.sweep_complete",
                        timeframes=list(timeframes),
                        interval_seconds=interval,
                        tickers=summary.tickers,
                        rows_fetched=summary.rows_fetched,
                        rows_upserted=summary.rows_upserted,
                    )
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stopping.wait(), timeout=interval)
    finally:
        await pool.close()
        log.info("market_data_intraday_trigger.stopped")


def main() -> None:
    settings = IntradayTriggerSettings()
    configure_logging(settings.service_name, settings.log_level)
    asyncio.run(serve(settings, AlpacaMarketDataSettings()))


__all__ = [
    "LIVE_STATUSES",
    "declared_timeframes",
    "main",
    "resolve_interval",
    "run_sweep",
    "serve",
    "timeframe_minutes",
]
