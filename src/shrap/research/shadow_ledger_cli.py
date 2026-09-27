"""``shrap-shadow-ledger`` — run and read the shadow forward test.

    shrap-shadow-ledger run      # one pass: settle what can be settled, decide today
    shrap-shadow-ledger loop     # the always-on service: a pass every interval
    shrap-shadow-ledger report   # the leaderboard, with error bars

A pass is idempotent, so the loop's interval is responsiveness rather than
correctness: it acts once per new session however often it wakes.

Intraday strategies are skipped and say so. Their decision grain is not a
session, and settling them on daily closes would record a different strategy
from the one declared.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.research.shadow_ledger import (
    ENROLLED_STATUSES,
    LedgerRow,
    LedgerStore,
    PostgresShadowLedger,
    StrategyPass,
    render_report,
    run_strategy,
    score,
)
from shrap.research.strategy_evaluator.costs import CostModel
from shrap.research.strategy_evaluator.pipeline import _default_strategy_factory, _extract_tickers
from shrap.research.strategy_evaluator.store import PostgresEvaluatorReader
from shrap.research.strategy_evaluator.strategy import BarSample, PricePanel
from shrap.research.strategy_registry import PostgresStrategyRegistry, StrategyRecord
from shrap.research.strategy_runner.cadence import read_cadence

log = structlog.get_logger(service="shadow-ledger")

# Enough history for the longest warmup the signal language allows (756 bars,
# three years of sessions) plus weekends and holidays.
LOOKBACK_CALENDAR_DAYS = 1_200
ADJUSTMENT = "all"
DEFAULT_INTERVAL_SECONDS = 3_600
MARKET_TZ = ZoneInfo("America/New_York")


def last_complete_session_bound(now: datetime) -> date:
    """The latest session date the ledger may read: yesterday, in New York.

    ``market_data.daily_bars`` holds a *partial* bar for the session in progress
    (the market-data trigger sweeps every six hours, including mid-session), and
    the first dry run on the Dell decided on 2026-09-24 at 12:54 PT from exactly
    such a bar. Deciding on a price that is not yet a close is not look-ahead —
    it is less information — but it is not the backtest's convention either, and
    a decision is recorded once and never revised. So today is invisible until
    it is yesterday. The ledger lags a day; in exchange every close it decides on
    or settles against is final.
    """

    return now.astimezone(MARKET_TZ).date() - timedelta(days=1)


def _dsn() -> str:
    return (
        os.environ.get("SHADOW_LEDGER_POSTGRES_DSN")
        or os.environ.get("STRATEGY_EVALUATOR_POSTGRES_DSN")
        or "postgresql://shrap:shrap@postgres:5432/shrap"
    )


async def _panel(
    reader: PostgresEvaluatorReader, tickers: Sequence[str], today: date
) -> PricePanel:
    start = today - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    bars: dict[str, list[BarSample]] = {}
    for ticker in tickers:
        rows = await reader.read_bars(ticker, start, today, ADJUSTMENT)
        if rows:
            bars[ticker] = rows
    shares = await reader.read_shares(list(bars))
    return PricePanel.from_bars(bars, shares)


class _DryRunLedger:
    """Computes everything, writes nothing — for checking a pass against live data."""

    def __init__(self) -> None:
        self.decisions: list[LedgerRow] = []

    async def rows(self, strategy_id: str) -> list[LedgerRow]:
        return []

    async def record_decision(self, row: LedgerRow) -> bool:
        self.decisions.append(row)
        return True

    async def settle(self, *args: object, **kwargs: object) -> None:
        return None


async def run_pass(pool: object, *, dry_run: bool = False) -> list[StrategyPass]:
    registry = PostgresStrategyRegistry(pool)  # type: ignore[arg-type]
    reader = PostgresEvaluatorReader(pool)  # type: ignore[arg-type]
    store: LedgerStore
    if dry_run:
        store = _DryRunLedger()
    else:
        store = PostgresShadowLedger(pool)
        await store.ensure_schema()

    records: list[StrategyRecord] = []
    for status in ENROLLED_STATUSES:
        records.extend(await registry.list_by_status(status))

    today = last_complete_session_bound(datetime.now(UTC))
    panels: dict[tuple[str, ...], PricePanel] = {}
    results: list[StrategyPass] = []
    for record in sorted(records, key=lambda r: r.strategy_id):
        if read_cadence(record.spec).is_intraday:
            results.append(StrategyPass(record.strategy_id, skipped="intraday cadence"))
            continue
        tickers = _extract_tickers(record.tickers)
        if not tickers:
            results.append(StrategyPass(record.strategy_id, skipped="no tickers"))
            continue
        key = tuple(sorted(tickers))
        if key not in panels:
            panels[key] = await _panel(reader, key, today)
        results.append(
            await run_strategy(
                record, tickers, panels[key], store, _default_strategy_factory, CostModel()
            )
        )
    for result in results:
        log.info(
            "shadow_ledger.strategy_pass",
            strategy_id=result.strategy_id,
            decided=result.decided.isoformat() if result.decided else None,
            settled=result.settled,
            skipped=result.skipped,
        )
    if isinstance(store, _DryRunLedger):
        for row in store.decisions:
            held = {t: round(w, 4) for t, w in row.weights.items() if w}
            log.info(
                "shadow_ledger.dry_run_decision",
                strategy_id=row.strategy_id,
                session_date=row.session_date.isoformat(),
                names=len(held),
                gross=round(sum(abs(w) for w in held.values()), 4),
                weights=held,
            )
    return results


async def report(pool: object) -> str:
    registry = PostgresStrategyRegistry(pool)  # type: ignore[arg-type]
    store = PostgresShadowLedger(pool)
    await store.ensure_schema()
    series = await store.settled_series()
    names = {r.strategy_id: r.name for r in await registry.list_all()}
    scores = [score(sid, rows) for sid, rows in series.items()]
    if not scores:
        return "Shadow forward test: nothing settled yet (a decision settles on the next session)."
    return render_report(scores, names, now=datetime.now(UTC))


async def _main(action: str, interval: float, dry_run: bool = False) -> str:
    pool = await create_asyncpg_pool(_dsn())
    try:
        if action == "report":
            return await report(pool)
        if action == "run":
            results = await run_pass(pool, dry_run=dry_run)
            decided = sum(1 for r in results if r.decided)
            settled = sum(r.settled for r in results)
            return f"shadow pass: {len(results)} strategies, {decided} decided, {settled} settled"
        while True:  # loop
            try:
                await run_pass(pool)
            except Exception:
                # Logged and retried next interval. A dead loop is caught by the
                # research.shadow_ledger freshness target, not by this handler.
                log.error("shadow_ledger.pass_failed", exc_info=True)
            await asyncio.sleep(interval)
    finally:
        await pool.close()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Shadow forward test for every strategy")
    parser.add_argument("action", choices=["run", "loop", "report"])
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("SHADOW_LEDGER_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="run: compute every decision, write nothing"
    )
    args = parser.parse_args(argv)
    configure_logging("shadow-ledger", os.environ.get("SHADOW_LEDGER_LOG_LEVEL", "INFO"))
    print(asyncio.run(_main(args.action, args.interval, args.dry_run)))


if __name__ == "__main__":
    main()
