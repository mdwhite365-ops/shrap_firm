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

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

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
    reason: str = ""

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
        excess += account_move - attributed
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


__all__ = [
    "DEFAULT_MIN_SESSIONS",
    "LiveComparison",
    "SessionPoint",
    "compare_to_benchmark",
    "equal_weight_session_returns",
]
