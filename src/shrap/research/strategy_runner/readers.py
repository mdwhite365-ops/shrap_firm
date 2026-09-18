"""Which bar grain a strategy reads, decided by the strategy rather than the firm.

The Runner has always held exactly one bar reader, because every strategy it has
ever run was daily. :mod:`.cadence` ended that assumption for *when* a strategy
decides — ``slot_for`` lets an intraday strategy act every ``interval_minutes``
while a daily one still acts once — and :func:`_lookback_start` ended it for *how
far back* the window reaches. This module ends it for *what the window is made
of*, which is the part whose absence was dangerous.

**The failure this prevents is silent, and it is the KI-030 shape.** A strategy
declaring ``cadence: intraday`` already resolves a 15-minute slot and already
computes a warmup measured in 15-minute bars. Handed the daily reader it would
then read *daily* bars into that window, rank them, and trade — 26 times a
session, on a signal recomputed from a panel that only moves once a day. Nothing
raises. The bars are real, the panel aligns, the signal is a number, the order
fills. Every component is individually correct and the composition trades a
stale ranking at intraday frequency. The Runner asked for bars without saying
which kind, and the reader answered the only question it knew how to answer.

**A resolver, not a mode flag.** ``read_bars`` takes a ticker and a window and
nothing else, so a single reader cannot tell a daily caller from an intraday one
without being told — and telling it means threading a cadence through a seam that
four other callers share. :class:`IntradayEvaluatorReader` already settled this
argument for the Evaluator by delegating rather than growing a flag; this is the
same answer one layer up. The Runner resolves a reader per strategy, once, from
the cadence it has already read for the slot and the window. One fact, read once,
used three times — rather than three components each deciding what "intraday"
means.

**Daily remains the default, by absence.** ``read_cadence`` resolves a missing or
malformed ``cadence`` key to DAILY, so every strategy in the registry today —
both of them, neither declaring a cadence — resolves to exactly the reader the
Runner has always constructed. Merging this changes no behaviour until a spec
says otherwise, which is the property that makes it safe to merge before there is
anything intraday to run.

**Readers are memoised per grain, and that is load-bearing.**
:class:`PostgresIntradayBarReader` caches the exchange calendar's session bounds
for one window, sized on the assumption that the Runner reads every ticker over
the same window. Building a reader per call would discard that cache on every
ticker and re-derive the calendar fifty times a pass. The cache lives in the
reader, so the reader has to outlive the call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from shrap.operations.market_phase import DEFAULT_CALENDAR
from shrap.research.strategy_evaluator.store import (
    PostgresEvaluatorReader,
    PostgresIntradayBarReader,
)
from shrap.research.strategy_evaluator.strategy import BarSample
from shrap.research.strategy_runner.cadence import Cadence, alpaca_timeframe


class BarReader(Protocol):
    """Trailing OHLCV for one ticker over one window, at one grain.

    The grain is the reader's, not the caller's: a window of ``start``..``end``
    means whole sessions to both implementations, and what differs is how many
    bars come back per session.
    """

    async def read_bars(
        self, ticker: str, start: date, end: date, adjustment: str
    ) -> list[BarSample]: ...


class BarReaderResolver(Protocol):
    """Picks the reader whose grain matches a strategy's declared cadence."""

    def for_cadence(self, cadence: Cadence) -> BarReader: ...


@dataclass(frozen=True, slots=True)
class SingleGrainReaders:
    """Every cadence reads the same bars.

    For callers that genuinely have one grain — tests with a fake reader, and
    any future path that is daily by construction. Named rather than achieved by
    passing a bare reader, so that "this ignores cadence" is a statement in the
    code instead of a coincidence of duck typing.
    """

    reader: BarReader

    def for_cadence(self, cadence: Cadence) -> BarReader:
        return self.reader


class CadenceBarReaders:
    """Daily bars for a daily strategy, intraday bars for an intraday one.

    Holds one reader per grain for the life of the service. The daily reader is
    built eagerly because every strategy needs it until one does not; intraday
    readers are built on first use and kept, keyed by the Alpaca timeframe token
    that ``alpaca_timeframe`` derives from the cadence. That token is also what
    ``market_data.intraday_bars.timeframe`` stores, so a strategy asking for a
    grain the backfill never wrote reads zero bars and is skipped by the planner
    for want of a warmup — the loud-by-emptiness failure, not a wrong answer.

    ``include_extended`` is off by default and should stay off. The backfill
    stores 04:00-20:00 as Alpaca returns it, and a pre-market print of a handful
    of shares sets a price no strategy could have traded at size.
    """

    def __init__(
        self,
        pool: object,
        *,
        include_extended: bool = False,
        calendar_name: str = DEFAULT_CALENDAR,
    ) -> None:
        self._pool = pool
        self._include_extended = include_extended
        self._calendar_name = calendar_name
        self._daily: BarReader = PostgresEvaluatorReader(pool)  # type: ignore[arg-type]
        self._intraday: dict[str, BarReader] = {}

    def for_cadence(self, cadence: Cadence) -> BarReader:
        if not cadence.is_intraday:
            return self._daily
        timeframe = alpaca_timeframe(cadence)
        reader = self._intraday.get(timeframe)
        if reader is None:
            reader = PostgresIntradayBarReader(
                self._pool,  # type: ignore[arg-type]
                timeframe=timeframe,
                include_extended=self._include_extended,
                calendar_name=self._calendar_name,
            )
            self._intraday[timeframe] = reader
        return reader


__all__ = [
    "BarReader",
    "BarReaderResolver",
    "CadenceBarReaders",
    "SingleGrainReaders",
]
