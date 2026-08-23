"""`shrap-live-benchmark` — what the live accounts earned, fairly compared.

Reads three tables the firm already maintains and reports the comparison from
:mod:`shrap.research.live_benchmark`:

    ops.account_snapshots   closing equity per session
    ops.position_snapshots  gross exposure carried into the next session
    market_data.daily_bars  the equal-weight benchmark

Read-only. It computes and prints; it never writes a decision, a state row or an
order, so it is safe to run against production at any time.

**Needs numpy**, via the promote gate's `sharpe` — reused rather than
reimplemented, because a second definition of the firm's central metric would
produce a live IR that cannot be set beside the backtest IR that admitted the
strategy. `strategy-runner` already carries numpy and pandas, so run it there;
a lighter container will fail on import (runbook s1e).

**Both readings are printed on purpose.** The naive one — account return against
a fully invested benchmark — is the number anyone would reach for, and on
2026-08-19 it said the strategy lost by 1.13pp while the exposure-matched one
said it won by 0.37pp. Printing only the fair number would win the argument and
lose the lesson; printing both makes the gap visible every time.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from shrap.common.db import create_asyncpg_pool
from shrap.research.live_benchmark import (
    SessionPoint,
    compare_to_benchmark,
    equal_weight_returns_for_dates,
    trading_dates,
)

SELECT_SESSION_EQUITY_SQL = """
SELECT DISTINCT ON (account_id, at::date)
       account_id, at::date AS session_date, equity
FROM ops.account_snapshots
WHERE account_id IS NOT NULL
  AND equity IS NOT NULL
  AND at::date BETWEEN $1 AND $2
ORDER BY account_id, at::date, at DESC
""".strip()

# The LAST pass of each day, then that pass's positions. Summing every pass
# would count the same book once per reconciliation cycle.
SELECT_SESSION_GROSS_SQL = """
WITH last_pass AS (
    SELECT DISTINCT ON (account_id, at::date)
           account_id, at::date AS session_date, event_id
    FROM ops.position_snapshots
    WHERE at::date BETWEEN $1 AND $2
    ORDER BY account_id, at::date, at DESC
)
SELECT l.account_id,
       l.session_date,
       COALESCE(SUM(ABS(p.market_value)), 0.0) AS gross
FROM last_pass l
LEFT JOIN ops.position_snapshots p
       ON p.event_id = l.event_id
      AND p.account_id = l.account_id
      AND p.ticker <> '__FLAT__'
GROUP BY l.account_id, l.session_date
""".strip()

SELECT_UNIVERSE_CLOSES_SQL = """
SELECT ticker, session_date, close
FROM market_data.daily_bars
WHERE adjustment = $3 AND session_date BETWEEN $1 AND $2
ORDER BY ticker, session_date
""".strip()


@dataclass(frozen=True, slots=True)
class AccountSeries:
    account_id: str
    points: tuple[SessionPoint, ...]


async def load_series(
    pool: object, start: date, end: date
) -> tuple[list[AccountSeries], dict[str, dict[date, float]]]:
    """Read equity, exposure and bars for the window."""

    async with pool.acquire() as conn:  # type: ignore[attr-defined]
        equity_rows = await conn.fetch(SELECT_SESSION_EQUITY_SQL, start, end)
        gross_rows = await conn.fetch(SELECT_SESSION_GROSS_SQL, start, end)
        bar_rows = await conn.fetch(SELECT_UNIVERSE_CLOSES_SQL, start, end, "all")

    gross: dict[tuple[str, date], float] = {
        (r["account_id"], r["session_date"]): float(r["gross"]) for r in gross_rows
    }
    by_account: dict[str, list[SessionPoint]] = {}
    for row in equity_rows:
        account = str(row["account_id"])
        session = row["session_date"]
        # A session with equity but no position pass is skipped, not assumed
        # flat: "no pass ran" and "the book was empty" are different facts, and
        # only one of them means zero exposure.
        key = (account, session)
        if key not in gross:
            continue
        by_account.setdefault(account, []).append(
            SessionPoint(
                session_date=session,
                equity=float(row["equity"]),
                gross_exposure=gross[key],
            )
        )

    closes: dict[str, dict[date, float]] = {}
    for row in bar_rows:
        closes.setdefault(str(row["ticker"]), {})[row["session_date"]] = float(row["close"])

    series = [
        AccountSeries(
            account_id=account, points=tuple(sorted(points, key=lambda p: p.session_date))
        )
        for account, points in sorted(by_account.items())
    ]
    return series, closes


def render(series: Sequence[AccountSeries], closes: dict[str, dict[date, float]]) -> str:
    # Only days the market priced. The account tables run seven days a week, so
    # an unfiltered window puts Saturdays in the series: no bar, benchmark 0.0,
    # and the entire Friday-to-Monday move landing in `excess` unoffset.
    sessions = trading_dates(closes)
    lines: list[str] = []
    for entry in series:
        points = tuple(p for p in entry.points if p.session_date in sessions)
        dropped = len(entry.points) - len(points)
        dates = [p.session_date for p in points]
        benchmark = equal_weight_returns_for_dates(closes, dates)
        result = compare_to_benchmark(points, benchmark)
        suffix = f", {dropped} non-trading day(s) dropped" if dropped else ""
        lines.append(f"\n{entry.account_id}  ({max(len(dates) - 1, 0)} sessions{suffix})")
        if not result.is_scored:
            lines.append(f"  not scored: {result.reason}")
            continue
        lines.append(f"  account return          {result.account_return * 100:+8.3f}%")
        lines.append(f"  benchmark (fully inv.)  {result.benchmark_return * 100:+8.3f}%")
        lines.append(f"  average exposure        {result.average_exposure * 100:8.1f}%")
        lines.append(f"  entitled at that size   {result.entitled_return * 100:+8.3f}%")
        lines.append(f"  EXCESS                  {result.excess * 100:+8.3f}%")
        ratio = result.information_ratio
        if ratio is None:
            lines.append("  information ratio            n/a (series too short or flat)")
        elif result.ratio_is_meaningful:
            lines.append(f"  information ratio       {ratio:+8.2f}   (promote floor 0.50)")
        else:
            # Annualising multiplies by sqrt(252). Below the session floor the
            # figure is arithmetic, not evidence, and printing it beside the
            # promote floor without saying so invites exactly the comparison it
            # cannot support.
            lines.append(
                f"  information ratio       {ratio:+8.2f}   "
                f"NOT MEANINGFUL at {result.sessions} sessions"
            )
        if result.average_exposure <= 0.0:
            # A book that was never invested did not compete. Calling that a
            # loss reads as a verdict on a strategy that never placed a trade.
            lines.append("  never invested — did not play, so neither beat nor lost")
        else:
            naive = "lost to" if not result.beat_benchmark_naively else "beat"
            fair = "matched" if result.excess == 0 else ("beat" if result.excess > 0 else "lost to")
            lines.append(f"  naive reading: {naive} the benchmark · exposure-matched: {fair} it")
            if fair != "matched" and naive != fair:
                lines.append(
                    "  ^ the two readings DISAGREE; the exposure-matched one is the fair one"
                )
        if result.underpowered:
            lines.append("  (underpowered: too few sessions to mean much)")
    return "\n".join(lines) if lines else "no accounts with both equity and position data"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", required=True, help="first session date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="last session date, YYYY-MM-DD")
    parser.add_argument(
        "--dsn",
        default=os.environ.get("STRATEGY_RUNNER_POSTGRES_DSN"),
        help="Postgres DSN (default: STRATEGY_RUNNER_POSTGRES_DSN env)",
    )
    return parser


async def _run(dsn: str, start: date, end: date) -> str:
    pool = await create_asyncpg_pool(dsn)
    try:
        series, closes = await load_series(pool, start, end)
        return render(series, closes)
    finally:
        await pool.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.dsn:
        print("no DSN: pass --dsn or set STRATEGY_RUNNER_POSTGRES_DSN")
        return 2
    print(asyncio.run(_run(args.dsn, date.fromisoformat(args.start), date.fromisoformat(args.end))))
    return 0


__all__ = ["build_parser", "load_series", "main", "render"]
