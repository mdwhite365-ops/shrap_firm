"""What a live account earned, against what its exposure entitled it to.

The firm could already answer two questions and not this one. A backtest is
scored against :mod:`shrap.research.strategy_evaluator.benchmark` — equal-weight
buy-and-hold — and a live account is scored on growth over drawdown by
:mod:`shrap.research.forward_score`. Neither compares a *live* account to the
benchmark, so "is the forward test confirming the evaluation?" had no arithmetic
behind it.

It needed some, because the naive comparison is wrong in a way that reverses its
own conclusion. Measured 2026-08-19 over ten sessions:

    equal-weight buy-and-hold        +1.825%
    PA3KQN57WVXY                     +0.70%   -> "lost to the benchmark"
    ...but it averaged 17.9% invested
    17.9% of +1.825%                 +0.33%   -> "beat the benchmark"

Both readings come from the same data and they disagree about the sign. The
second is the fair one: a paper-stage strategy is scaled by
``stage_fraction x regime_multiplier`` (0.25 x 0.75 = 0.1875 today), so it
*cannot* track a fully invested benchmark and should not be asked to. Comparing
a fifth-invested book to a fully invested one measures the Risk Officer's
caution, not the strategy's skill.

**This is also the reason a forward test and the gate that admitted it are not
directly comparable.** The promote gate scores strategies fully invested; the
live book runs them at 0.1875. Nothing anywhere reconciled the two, so every
"the forward test says otherwise" claim has carried that factor silently.

Three decisions worth stating:

**Exposure is taken from the PRIOR session.** Today's return is earned on
yesterday's position — the book that was actually held through the move, not the
one the close leaves behind. Using same-day exposure credits a position for a
move it was not in.

**Excess is summed, not compounded.** Per-period active return is the standard
construction and it keeps each session's contribution inspectable; compounding an
excess series conflates the strategy's return with the benchmark's.

**No beta adjustment, and that is a real limitation.** Scaling a 50-name
benchmark by a scalar exposure assumes the held names move like the universe.
These are concentrated ten-name books, so they do not. This measures "did the
account beat a passive book of the same size", which is worth knowing and is not
the same as alpha. Do not report it as alpha.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

from shrap.research.forward_score import (
    DEFAULT_MIN_SESSIONS_FOR_RATE,
    TRADING_DAYS_PER_YEAR,
)
from shrap.research.strategy_evaluator.engine import sharpe

# Below this the number describes the sample, not the strategy. Deliberately
# lower than forward_score's 20-session floor for an annualised *rate*: an
# excess return over a handful of sessions is at least a fact about those
# sessions, where an extrapolated CAGR is not.
DEFAULT_MIN_SESSIONS = 5

REASON_TOO_FEW_POINTS = "fewer than two equity samples — no return to measure"
REASON_LENGTH_MISMATCH = (
    "benchmark series does not cover the same sessions as the equity series, so "
    "the comparison would be between different periods"
)


@dataclass(frozen=True, slots=True)
class SessionPoint:
    """One session's closing equity and the gross exposure carried into it."""

    session_date: date
    equity: float
    gross_exposure: float
    """Absolute sum of position market values. Dollars, not a fraction."""

    @property
    def exposure_fraction(self) -> float:
        if self.equity <= 0.0:
            return 0.0
        return self.gross_exposure / self.equity


@dataclass(frozen=True, slots=True)
class LiveComparison:
    """A live account measured against an exposure-matched passive book."""

    sessions: int
    account_return: float
    benchmark_return: float
    entitled_return: float
    """What the benchmark would have paid a book held at this account's exposure."""
    excess: float
    average_exposure: float
    underpowered: bool
    excess_series: tuple[float, ...] = ()
    """Per-session excess. Kept because its *dispersion* is the whole question:
    a cumulative +0.069% over nine sessions is meaningless without it."""
    reason: str = ""

    @property
    def information_ratio(self) -> float | None:
        """Annualised IR of the exposure-adjusted excess, or ``None``.

        **The same statistic the promote gate uses**, computed with the same
        function — :func:`~shrap.research.strategy_evaluator.engine.sharpe` over
        an active-return series. Reimplementing it here would create a second
        definition of the firm's central metric, and a live IR that could not be
        set beside the backtest IR that admitted the strategy is the exact thing
        this module exists to fix.

        ``None`` rather than 0.0 when the series cannot support one: `sharpe`
        returns 0.0 both for a flat series and for one too short to measure, and
        those mean different things. A zero IR is a finding; an undefined one is
        an absence of data.
        """

        if len(self.excess_series) < 2:
            return None
        if all(value == self.excess_series[0] for value in self.excess_series):
            return None  # zero dispersion: sharpe would return 0.0, meaning "undefined"
        return sharpe(self.excess_series, TRADING_DAYS_PER_YEAR)

    @property
    def ratio_is_meaningful(self) -> bool:
        """Whether the window supports an annualised figure at all.

        Mirrors :data:`~shrap.research.forward_score.DEFAULT_MIN_SESSIONS_FOR_RATE`
        deliberately. Annualising multiplies by sqrt(252), so nine sessions
        produce a confident-looking number from nothing — "noise wearing a
        CAGR's clothes", as forward_score puts it about the same trap.
        """

        return self.sessions >= DEFAULT_MIN_SESSIONS_FOR_RATE

    @property
    def is_scored(self) -> bool:
        return not self.reason

    @property
    def beat_benchmark_naively(self) -> bool:
        """The unadjusted comparison. Kept because it is the one people reach
        for, and showing it beside the fair one is how the difference gets
        noticed rather than argued about."""

        return self.account_return > self.benchmark_return


def _refused(reason: str) -> LiveComparison:
    return LiveComparison(
        sessions=0,
        account_return=0.0,
        benchmark_return=0.0,
        entitled_return=0.0,
        excess=0.0,
        average_exposure=0.0,
        underpowered=True,
        excess_series=(),
        reason=reason,
    )


def compare_to_benchmark(
    points: Sequence[SessionPoint],
    benchmark_returns: Sequence[float],
    *,
    min_sessions: int = DEFAULT_MIN_SESSIONS,
) -> LiveComparison:
    """Score ``points`` against a benchmark scaled to the account's exposure.

    ``benchmark_returns`` holds one per-session return per *transition*, so it
    is one shorter than ``points``: with N closes there are N-1 moves.
    """

    if len(points) < 2:
        return _refused(REASON_TOO_FEW_POINTS)
    if len(benchmark_returns) != len(points) - 1:
        return _refused(
            f"{REASON_LENGTH_MISMATCH} ({len(benchmark_returns)} benchmark "
            f"returns for {len(points)} equity samples; expected {len(points) - 1})"
        )
    if any(p.equity <= 0.0 for p in points):
        return _refused("an equity sample is zero or negative, which cannot carry a return")

    excess = 0.0
    entitled = 0.0
    per_session: list[float] = []
    exposures: list[float] = []
    benchmark_compounded = 1.0

    for index, benchmark_move in enumerate(benchmark_returns):
        previous, current = points[index], points[index + 1]
        account_move = current.equity / previous.equity - 1.0
        # Prior-session exposure: the book that was actually held through this
        # move. Same-day exposure would credit a position for a move it missed.
        carried = previous.exposure_fraction
        attributed = carried * benchmark_move
        entitled += attributed
        session_excess = account_move - attributed
        per_session.append(session_excess)
        excess += session_excess
        exposures.append(carried)
        benchmark_compounded *= 1.0 + benchmark_move

    return LiveComparison(
        sessions=len(benchmark_returns),
        account_return=points[-1].equity / points[0].equity - 1.0,
        benchmark_return=benchmark_compounded - 1.0,
        entitled_return=entitled,
        excess=excess,
        average_exposure=sum(exposures) / len(exposures),
        underpowered=len(benchmark_returns) < min_sessions,
        excess_series=tuple(per_session),
    )


def equal_weight_session_returns(
    closes_by_ticker: dict[str, Sequence[float]],
) -> list[float]:
    """Per-session equal-weight return across the universe.

    The mean of each name's return, which is a daily-rebalanced equal-weight
    book. Names with fewer than two closes contribute nothing rather than being
    padded: a short-history ticker silently truncating the panel is a trap this
    firm has already been caught by once.
    """

    usable = {t: c for t, c in closes_by_ticker.items() if len(c) >= 2}
    if not usable:
        return []
    length = min(len(c) for c in usable.values())
    returns: list[float] = []
    for index in range(1, length):
        moves = [c[index] / c[index - 1] - 1.0 for c in usable.values() if c[index - 1] > 0.0]
        returns.append(sum(moves) / len(moves) if moves else 0.0)
    return returns


def equal_weight_returns_for_dates(
    closes: Mapping[str, Mapping[date, float]],
    dates: Sequence[date],
) -> list[float]:
    """Per-transition returns of an equal-weight **buy-and-hold** book.

    Buy 1/N at ``dates[0]`` and hold. The portfolio's value at *t* is the mean
    of each name's ``close(t) / close(dates[0])``, and the returns are the
    changes in that value.

    **This averaged per-period returns until 2026-08-23, which is daily
    rebalancing, not buy-and-hold.** The two are different books and they gave
    different answers on the first live run — +2.403% rebalanced against
    +1.825% held, over the same fortnight. That gap was larger than every
    excess figure being reported, and the module called itself buy-and-hold
    throughout. The name was right and the arithmetic was wrong.

    It matters beyond accuracy: the promote gate scores backtests against
    :class:`~shrap.research.strategy_evaluator.benchmark.EqualWeightBuyAndHold`,
    so a live comparison using a different book cannot be set beside the
    evaluation that admitted the strategy.

    Ragged data: a name contributes to *t* when it is priced at both the base
    date and *t*. Dropping it from the whole window instead would shrink the
    universe to whatever listed earliest.
    """

    if len(dates) < 2:
        return []
    base = dates[0]

    returns: list[float] = []
    for previous, current in pairwise(dates):
        # Membership is held CONSTANT across each transition: only names priced
        # at the base, at `previous` AND at `current`. Recomputing the universe
        # per date instead lets a name rejoining mid-window drag its whole
        # since-inception return into one transition, recording a composition
        # change as a price move.
        held = [
            series
            for series in closes.values()
            if base in series and previous in series and current in series and series[base] > 0.0
        ]
        if not held:
            returns.append(0.0)
            continue
        before = sum(s[previous] / s[base] for s in held) / len(held)
        after = sum(s[current] / s[base] for s in held) / len(held)
        returns.append(after / before - 1.0 if before > 0.0 else 0.0)
    return returns


def trading_dates(closes: Mapping[str, Mapping[date, float]]) -> set[date]:
    """Dates the market actually priced, per the bar table.

    The account tables do not know about weekends — reconciliation writes a
    snapshot every ~300s all week — so a naive window includes Saturdays. Those
    carry no bar, so the benchmark reads 0.0 across them while the account's
    equity still moves, and the whole Friday-to-Monday move lands in `excess`
    with nothing to offset it. Measured on the first live run: 14 "sessions"
    over a fortnight that held 10 trading days.
    """

    return {moment for series in closes.values() for moment in series}


__all__ = [
    "DEFAULT_MIN_SESSIONS",
    "LiveComparison",
    "SessionPoint",
    "compare_to_benchmark",
    "equal_weight_returns_for_dates",
    "equal_weight_session_returns",
    "trading_dates",
]
