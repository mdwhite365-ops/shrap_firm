"""The shadow forward test: every strategy, run daily on live bars, on paper-on-paper.

**Why this exists.** The firm's two ways of testing a strategy both fail to
produce throughput. A backtest's IR carries a standard error of ~0.47 on this
panel (KI-036), so it can reject the clearly broken and cannot confirm anything.
A broker forward test is real, but there are three accounts, so the firm can
forward-test three strategies at a time — and a daily strategy needs most of a
year before its forward IR means much. At that rate the question "does anything
here work?" takes decades.

This ledger forward-tests **every** enrolled strategy at once, with no broker.
Each session it records what each strategy would hold, and the next session it
settles what that holding earned, net of the Evaluator's own cost model, against
the Evaluator's own benchmark over the strategy's own tickers.

**Out-of-sample by construction, not by care.** A decision row is written on
day *t* from bars through *t*, before the close of *t+1* exists. Settlement on
*t+1* reads the **stored** weights; it never recomputes them. So neither a later
data revision, nor a code change, nor a bug in a strategy's warmup can leak the
future into the record — the weights were fixed while the future was unknown.
A missed day is a gap, not a backfill: a decision nobody recorded in time cannot
be reconstructed without hindsight, so it is not.

**Same accounting as the backtest**, deliberately and by import rather than by
copy: period *p* holds the weights decided at close *p* from close *p* to close
*p+1*; a name must trade at both ends to be held; costs are charged on the
change from the previous period's holdings at the decision bar's ADV. A shadow
return and a backtest return are therefore the same quantity, and the shadow
record is the backtest's out-of-sample continuation.

**Killed strategies are enrolled too.** They were killed on backtest evidence
that cannot separate an IR of 0.45 from 0.50 (KI-036). Shadowing them costs
nothing and is the only way the firm will ever learn whether a kill was right.

What this is not: execution. Fills at the close with a cost model are not fills
at a broker. The three accounts exist to test that, and they should hold the
strategies this ledger ranks highest.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from shrap.research.strategy_evaluator.benchmark import EqualWeightBuyAndHold
from shrap.research.strategy_evaluator.costs import CostModel
from shrap.research.strategy_evaluator.engine import _adv_dollar_series
from shrap.research.strategy_evaluator.strategy import PricePanel, StrategySignal
from shrap.research.strategy_registry import (
    STATUS_HYPOTHESIS,
    STATUS_KILL_REVIEW,
    STATUS_KILL_REVIEW_MIKE,
    STATUS_KILLED,
    STATUS_LIVE_PAPER,
    STATUS_PAPER,
    STATUS_SMALL_SIZE_PAPER,
    StrategyRecord,
)

ENROLLED_STATUSES: tuple[str, ...] = (
    STATUS_HYPOTHESIS,
    STATUS_PAPER,
    STATUS_SMALL_SIZE_PAPER,
    STATUS_LIVE_PAPER,
    STATUS_KILL_REVIEW,
    STATUS_KILL_REVIEW_MIKE,
    STATUS_KILLED,
)
TRADING_DAYS_PER_YEAR = 252
_TRADE_EPS = 1e-12

CREATE_SHADOW_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS research.shadow_ledger (
    strategy_id TEXT NOT NULL,
    session_date DATE NOT NULL,
    spec_hash TEXT NOT NULL,
    weights JSONB NOT NULL,
    benchmark_weights JSONB NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    next_session_date DATE,
    net_return DOUBLE PRECISION,
    gross_return DOUBLE PRECISION,
    cost DOUBLE PRECISION,
    benchmark_return DOUBLE PRECISION,
    settled_at TIMESTAMPTZ,
    PRIMARY KEY (strategy_id, session_date)
)
""".strip()

INSERT_DECISION_SQL = """
INSERT INTO research.shadow_ledger
    (strategy_id, session_date, spec_hash, weights, benchmark_weights)
VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)
ON CONFLICT (strategy_id, session_date) DO NOTHING
""".strip()

SELECT_ROWS_SQL = """
SELECT strategy_id, session_date, spec_hash, weights, benchmark_weights,
       next_session_date, net_return, gross_return, cost, benchmark_return
FROM research.shadow_ledger
WHERE strategy_id = $1
ORDER BY session_date
""".strip()

SETTLE_SQL = """
UPDATE research.shadow_ledger
SET next_session_date = $3, net_return = $4, gross_return = $5, cost = $6,
    benchmark_return = $7, settled_at = now()
WHERE strategy_id = $1 AND session_date = $2 AND net_return IS NULL
""".strip()

SELECT_ALL_SETTLED_SQL = """
SELECT strategy_id, session_date, net_return, benchmark_return
FROM research.shadow_ledger
WHERE net_return IS NOT NULL
ORDER BY strategy_id, session_date
""".strip()


# --- the pure core -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LedgerRow:
    strategy_id: str
    session_date: date
    spec_hash: str
    weights: Mapping[str, float]
    benchmark_weights: Mapping[str, float]
    next_session_date: date | None = None
    net_return: float | None = None
    gross_return: float | None = None
    cost: float | None = None
    benchmark_return: float | None = None

    @property
    def settled(self) -> bool:
        return self.net_return is not None


@dataclass(frozen=True, slots=True)
class PeriodResult:
    net: float
    gross: float
    cost: float


def decide(strategy: StrategySignal, panel: PricePanel) -> dict[str, float] | None:
    """The weights the strategy holds from the latest close. ``None`` before warmup."""

    if panel.n_bars < max(int(strategy.warmup), 1):
        return None
    raw = strategy.target_weights(panel.window(panel.n_bars - 1))
    return {t: float(raw.get(t, 0.0)) for t in panel.tickers}


def settle_period(
    panel: PricePanel,
    index: int,
    weights: Mapping[str, float],
    previous: Mapping[str, float],
    cost_model: CostModel,
    adv: Mapping[str, Sequence[float]],
) -> PeriodResult:
    """What ``weights`` earned from close ``index`` to close ``index + 1``.

    Mirrors one period of :func:`~shrap.research.strategy_evaluator.engine.run_backtest`:
    a name is held only if it traded at both ends, and costs are charged on the
    change from what was actually held in the prior period. ``previous`` is empty
    for the first recorded decision, and after a gap — entering from flat, which
    charges the full entry cost rather than assuming a position nobody recorded.
    """

    def held(w: Mapping[str, float], period: int) -> dict[str, float]:
        return {
            t: (v if panel.is_live(t, period) and panel.is_live(t, period + 1) else 0.0)
            for t, v in w.items()
            if t in panel.closes
        }

    curr = held(weights, index)
    prev = held(previous, index - 1) if previous and index >= 1 else {}
    gross = cost = 0.0
    for ticker in panel.tickers:
        c, p = curr.get(ticker, 0.0), prev.get(ticker, 0.0)
        if abs(c - p) > _TRADE_EPS:
            cost += cost_model.trade_cost_fraction(c - p, adv[ticker][index])
        cost += cost_model.borrow_cost_fraction(c)
        if c == 0.0:
            continue
        start, end = panel.closes[ticker][index], panel.closes[ticker][index + 1]
        if start != 0.0:
            gross += c * (end / start - 1.0)
    return PeriodResult(net=gross - cost, gross=gross, cost=cost)


@dataclass(frozen=True, slots=True)
class ShadowScore:
    """A strategy's shadow record so far, with the error bar it deserves."""

    strategy_id: str
    days: int
    first: date | None
    cumulative: float
    benchmark_cumulative: float
    information_ratio: float | None
    ir_standard_error: float | None

    @property
    def active_cumulative(self) -> float:
        return self.cumulative - self.benchmark_cumulative


def score(strategy_id: str, rows: Sequence[tuple[date, float, float]]) -> ShadowScore:
    """Annualised IR of active return, and its standard error.

    The standard error is the point. With 60 days of shadow history the SE of an
    annualised IR is about 2.0, so nothing the ledger shows in its first months
    separates skill from luck — it separates *broken* from *not obviously broken*,
    and ranks. ``SE ~ sqrt((1 + IR_d^2 / 2) / n) x sqrt(252)`` (Lo, 2002).
    """

    n = len(rows)
    cum = bench = 1.0
    active: list[float] = []
    for _, net, b in rows:
        cum *= 1.0 + net
        bench *= 1.0 + b
        active.append(net - b)
    ir = se = None
    if n >= 2:
        mean = sum(active) / n
        sd = math.sqrt(sum((a - mean) ** 2 for a in active) / (n - 1))
        if sd > 0.0:
            ir_daily = mean / sd
            ir = ir_daily * math.sqrt(TRADING_DAYS_PER_YEAR)
            se = math.sqrt((1.0 + ir_daily**2 / 2.0) / n) * math.sqrt(TRADING_DAYS_PER_YEAR)
    return ShadowScore(
        strategy_id=strategy_id,
        days=n,
        first=rows[0][0] if rows else None,
        cumulative=cum - 1.0,
        benchmark_cumulative=bench - 1.0,
        information_ratio=ir,
        ir_standard_error=se,
    )


# --- one pass ----------------------------------------------------------------------


class LedgerStore(Protocol):
    async def rows(self, strategy_id: str) -> list[LedgerRow]: ...

    async def record_decision(self, row: LedgerRow) -> bool: ...

    async def settle(
        self,
        strategy_id: str,
        session_date: date,
        next_date: date,
        result: PeriodResult,
        benchmark_return: float,
    ) -> None: ...


PanelLoader = Callable[[Sequence[str]], Any]
"""``await loader(tickers)`` -> a :class:`PricePanel` of daily bars through the latest session."""

StrategyFactory = Callable[[StrategyRecord, list[str]], StrategySignal]


@dataclass(frozen=True, slots=True)
class StrategyPass:
    strategy_id: str
    decided: date | None = None
    settled: int = 0
    skipped: str | None = None


async def run_strategy(
    record: StrategyRecord,
    tickers: list[str],
    panel: PricePanel,
    store: LedgerStore,
    factory: StrategyFactory,
    cost_model: CostModel,
) -> StrategyPass:
    """Settle whatever tomorrow has made settleable, then decide today. Idempotent."""

    try:
        strategy = factory(record, tickers)
    except Exception as exc:
        return StrategyPass(record.strategy_id, skipped=f"cannot build: {exc}")
    if panel.n_bars < 2:
        return StrategyPass(record.strategy_id, skipped="panel has fewer than two sessions")

    index_of = {d: i for i, d in enumerate(panel.dates)}
    adv = _adv_dollar_series(panel, cost_model.adv_window)
    rows = await store.rows(record.strategy_id)
    by_date = {r.session_date: r for r in rows}

    settled = 0
    for row in rows:
        if row.settled:
            continue
        i = index_of.get(row.session_date)
        if i is None or i + 1 >= panel.n_bars:
            continue  # tomorrow's close does not exist yet
        prev_row = by_date.get(panel.dates[i - 1]) if i >= 1 else None
        result = settle_period(
            panel, i, row.weights, prev_row.weights if prev_row else {}, cost_model, adv
        )
        bench = settle_period(
            panel,
            i,
            row.benchmark_weights,
            prev_row.benchmark_weights if prev_row else {},
            cost_model,
            adv,
        )
        await store.settle(
            record.strategy_id, row.session_date, panel.dates[i + 1], result, bench.net
        )
        settled += 1

    latest = panel.dates[-1]
    if latest in by_date:
        return StrategyPass(record.strategy_id, settled=settled)
    weights = decide(strategy, panel)
    if weights is None:
        return StrategyPass(record.strategy_id, settled=settled, skipped="inside warmup")
    benchmark = decide(EqualWeightBuyAndHold(), panel) or {}
    await store.record_decision(
        LedgerRow(
            strategy_id=record.strategy_id,
            session_date=latest,
            spec_hash=record.spec_hash,
            weights=weights,
            benchmark_weights=benchmark,
        )
    )
    return StrategyPass(record.strategy_id, decided=latest, settled=settled)


# --- Postgres --------------------------------------------------------------------------


def _weights(raw: object) -> dict[str, float]:
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, Mapping):
        return {}
    return {str(k): float(v) for k, v in data.items()}


def _nonzero_json(weights: Mapping[str, float]) -> str:
    # Flat names are omitted: every consumer treats a missing ticker as 0.0, and
    # fifty explicit zeros per row per strategy per day is most of the table.
    return json.dumps({t: w for t, w in sorted(weights.items()) if w != 0.0})


class PostgresShadowLedger:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS research")
            await conn.execute(CREATE_SHADOW_LEDGER_SQL)

    async def rows(self, strategy_id: str) -> list[LedgerRow]:
        async with self._pool.acquire() as conn:
            records = await conn.fetch(SELECT_ROWS_SQL, strategy_id)
        return [
            LedgerRow(
                strategy_id=str(r["strategy_id"]),
                session_date=r["session_date"],
                spec_hash=str(r["spec_hash"]),
                weights=_weights(r["weights"]),
                benchmark_weights=_weights(r["benchmark_weights"]),
                next_session_date=r["next_session_date"],
                net_return=r["net_return"],
                gross_return=r["gross_return"],
                cost=r["cost"],
                benchmark_return=r["benchmark_return"],
            )
            for r in records
        ]

    async def record_decision(self, row: LedgerRow) -> bool:
        async with self._pool.acquire() as conn:
            status = await conn.execute(
                INSERT_DECISION_SQL,
                row.strategy_id,
                row.session_date,
                row.spec_hash,
                _nonzero_json(row.weights),
                _nonzero_json(row.benchmark_weights),
            )
        return str(status).endswith("1")

    async def settle(
        self,
        strategy_id: str,
        session_date: date,
        next_date: date,
        result: PeriodResult,
        benchmark_return: float,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                SETTLE_SQL,
                strategy_id,
                session_date,
                next_date,
                result.net,
                result.gross,
                result.cost,
                benchmark_return,
            )

    async def settled_series(self) -> dict[str, list[tuple[date, float, float]]]:
        async with self._pool.acquire() as conn:
            records = await conn.fetch(SELECT_ALL_SETTLED_SQL)
        out: dict[str, list[tuple[date, float, float]]] = {}
        for r in records:
            out.setdefault(str(r["strategy_id"]), []).append(
                (r["session_date"], float(r["net_return"]), float(r["benchmark_return"]))
            )
        return out


def render_report(
    scores: Sequence[ShadowScore], names: Mapping[str, str], *, now: datetime | None = None
) -> str:
    """Leaderboard, best IR first, with its error bar beside it."""

    ranked = sorted(
        scores,
        key=lambda s: (s.information_ratio is None, -(s.information_ratio or 0.0)),
    )
    lines = [
        "Shadow forward test — net of costs, against equal-weight buy-and-hold of each "
        "strategy's own tickers.",
        "IR +/- SE is annualised; until SE is well under the gap between two strategies, "
        "their order is not evidence.",
        "",
        f"{'strategy':<52} {'days':>5} {'net':>8} {'bench':>8} {'active':>8} "
        f"{'IR':>7} {'+/-SE':>7}",
    ]
    for s in ranked:
        ir = "n/a" if s.information_ratio is None else f"{s.information_ratio:+.2f}"
        se = "n/a" if s.ir_standard_error is None else f"{s.ir_standard_error:.2f}"
        name = f"{names.get(s.strategy_id, '?')[:38]} ({s.strategy_id[-6:]})"
        lines.append(
            f"{name:<52} {s.days:>5} {s.cumulative:>+8.2%} {s.benchmark_cumulative:>+8.2%} "
            f"{s.active_cumulative:>+8.2%} {ir:>7} {se:>7}"
        )
    if now is not None:
        lines.append(f"\nas of {now.isoformat(timespec='minutes')}")
    return "\n".join(lines)


__all__ = [
    "CREATE_SHADOW_LEDGER_SQL",
    "ENROLLED_STATUSES",
    "LedgerRow",
    "LedgerStore",
    "PeriodResult",
    "PostgresShadowLedger",
    "ShadowScore",
    "StrategyPass",
    "decide",
    "render_report",
    "run_strategy",
    "score",
    "settle_period",
]
