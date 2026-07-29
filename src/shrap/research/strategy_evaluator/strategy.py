"""The strategy interface seam (``StrategySignal``) and the price-panel model.

DECISION-CARRYING (pending Mike). ``StrategySignal`` is the interface every
future strategy implements — it is the architectural boundary between the
Evaluator's deterministic engine and the (deferred) strategy-authoring system.
Merging this card accepts the seam. See ``docs/research/eval-protocol.md``.

The seam is deliberately narrow and no-peek by construction. A strategy is a
parameterized object; it is handed a :class:`PanelWindow` that exposes bars
**only up to and including the current bar**, and it returns a target portfolio
weight per ticker (signed: negative = short, magnitude = fraction of book).
Discrete trades are recovered by the engine as changes in target weight — the
same seam expresses both "target positions" and "discrete trades" without a
second interface. Because the window cannot expose future bars, a strategy is
structurally unable to peek; look-ahead is prevented by the data model, not by
convention.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BarSample:
    """One daily OHLCV bar, as read from ``market_data.daily_bars``."""

    session_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


class PanelWindow:
    """A no-peek view of a :class:`PricePanel` from bar 0 through ``index``.

    All accessors return the prefix ``[0 .. index]`` inclusive; there is no way
    to reach a later bar. Strategies compute their signal from this view, so a
    look-ahead bug in a strategy is not expressible through the seam.
    """

    __slots__ = ("_index", "_panel")

    def __init__(self, panel: PricePanel, index: int) -> None:
        self._panel = panel
        self._index = index

    @property
    def tickers(self) -> tuple[str, ...]:
        return self._panel.tickers

    @property
    def current_index(self) -> int:
        return self._index

    @property
    def current_date(self) -> date:
        return self._panel.dates[self._index]

    def dates(self) -> tuple[date, ...]:
        return self._panel.dates[: self._index + 1]

    def closes(self, ticker: str) -> tuple[float, ...]:
        return self._panel.closes[ticker][: self._index + 1]

    def volumes(self, ticker: str) -> tuple[float, ...]:
        return self._panel.volumes[ticker][: self._index + 1]


@dataclass(frozen=True, slots=True)
class TickerCoverage:
    """How many of the panel's candidate dates one ticker actually supplied."""

    ticker: str
    n_bars: int
    missing: int
    first_date: date | None
    last_date: date | None


@dataclass(frozen=True, slots=True)
class PanelCoverage:
    """What the intersection cost, and which ticker cost the most of it.

    ``PricePanel`` intersects session dates, so the panel every strategy is
    measured on is only as long as its *shortest* ticker's history. That is the
    correct point-in-time rule — the alternative is forward-filling bars that
    did not exist — but it is silent, and silence is the problem this type
    exists to fix.

    The first cross-sectional verdict the firm produced ran on the 50-name
    launch list, which contains ETHA (listed 2024-07). Every other name had
    years more history and none of it was usable, because a date survives only
    if all fifty tickers have it. Nothing in the verdict, the summary line or
    the evaluation card said so: the panel length was computed, used to size
    every fold, and never reported. An operator who backfilled to 2018 — which
    the momentum runbook explicitly instructs — had no way to discover that the
    test ran on two years.

    ``worst`` is ranked by dates *missed*, not by history length, so it catches
    a ticker with interior holes as readily as a late listing. Both truncate the
    panel; only one is obvious from an inception date.
    """

    n_bars: int
    candidate_bars: int
    first_date: date | None
    last_date: date | None
    per_ticker: tuple[TickerCoverage, ...]

    @property
    def worst(self) -> TickerCoverage | None:
        """The ticker costing the panel the most dates, or ``None`` if lossless."""

        ranked = [c for c in self.per_ticker if c.missing > 0]
        if not ranked:
            return None
        return max(ranked, key=lambda c: (c.missing, c.ticker))

    def summary(self) -> str:
        """One field for the verdict line: ``bars=506/1258 binds=ETHA``."""

        line = f"bars={self.n_bars}/{self.candidate_bars}"
        worst = self.worst
        if worst is not None:
            line += f" binds={worst.ticker}"
        return line

    @classmethod
    def from_bars(cls, bars_by_ticker: Mapping[str, Sequence[BarSample]]) -> PanelCoverage:
        """Measure the same inputs :meth:`PricePanel.from_bars` intersects."""

        dates_by_ticker = {
            ticker: {bar.session_date for bar in bars} for ticker, bars in bars_by_ticker.items()
        }
        # The candidate calendar is the UNION: every date any ticker traded, which
        # is the panel the operator thinks they backfilled. Measuring loss against
        # the longest single ticker would understate it whenever two tickers are
        # short in different places.
        candidates: set[date] = set()
        for seen in dates_by_ticker.values():
            candidates |= seen
        kept: set[date] | None = None
        for seen in dates_by_ticker.values():
            kept = seen if kept is None else (kept & seen)
        kept = kept or set()
        per_ticker = tuple(
            TickerCoverage(
                ticker=ticker,
                n_bars=len(seen),
                missing=len(candidates - seen),
                first_date=min(seen) if seen else None,
                last_date=max(seen) if seen else None,
            )
            for ticker, seen in dates_by_ticker.items()
        )
        return cls(
            n_bars=len(kept),
            candidate_bars=len(candidates),
            first_date=min(kept) if kept else None,
            last_date=max(kept) if kept else None,
            per_ticker=per_ticker,
        )


@dataclass(frozen=True, slots=True)
class PricePanel:
    """Point-in-time aligned daily OHLCV across one or more tickers.

    Alignment is the **intersection** of each ticker's session dates: a date
    survives only if every ticker has a bar for it. No forward-fill, no
    fabricated bars — a missing bar is a missing date, which keeps the panel
    honest about what data actually existed (the spec's point-in-time, no
    survivor-bias rule at the daily grain).
    """

    tickers: tuple[str, ...]
    dates: tuple[date, ...]
    opens: dict[str, tuple[float, ...]]
    highs: dict[str, tuple[float, ...]]
    lows: dict[str, tuple[float, ...]]
    closes: dict[str, tuple[float, ...]]
    volumes: dict[str, tuple[float, ...]]

    @property
    def n_bars(self) -> int:
        return len(self.dates)

    def window(self, index: int) -> PanelWindow:
        if not 0 <= index < self.n_bars:
            raise IndexError(f"bar index {index} out of range [0, {self.n_bars})")
        return PanelWindow(self, index)

    @classmethod
    def from_bars(cls, bars_by_ticker: Mapping[str, Sequence[BarSample]]) -> PricePanel:
        """Build a panel from per-ticker bar lists, aligned on common dates."""

        if not bars_by_ticker:
            raise ValueError("PricePanel.from_bars requires at least one ticker")
        tickers = tuple(bars_by_ticker.keys())
        by_ticker: dict[str, dict[date, BarSample]] = {}
        common: set[date] | None = None
        for ticker, bars in bars_by_ticker.items():
            indexed = {bar.session_date: bar for bar in bars}
            by_ticker[ticker] = indexed
            keys = set(indexed)
            common = keys if common is None else (common & keys)
        dates = tuple(sorted(common or set()))
        opens: dict[str, tuple[float, ...]] = {}
        highs: dict[str, tuple[float, ...]] = {}
        lows: dict[str, tuple[float, ...]] = {}
        closes: dict[str, tuple[float, ...]] = {}
        volumes: dict[str, tuple[float, ...]] = {}
        for ticker in tickers:
            rows = [by_ticker[ticker][d] for d in dates]
            opens[ticker] = tuple(r.open for r in rows)
            highs[ticker] = tuple(r.high for r in rows)
            lows[ticker] = tuple(r.low for r in rows)
            closes[ticker] = tuple(r.close for r in rows)
            volumes[ticker] = tuple(r.volume for r in rows)
        return cls(
            tickers=tickers,
            dates=dates,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
        )


class StrategySignal(Protocol):
    """The seam every strategy implements.

    A strategy is a parameterized object. ``warmup`` is the number of leading
    bars it needs before its first meaningful signal (used to place the initial
    walk-forward train block). ``target_weights`` maps the current no-peek
    window to a signed target weight per ticker; the engine turns changes in
    those weights into trades, costs, and PnL.
    """

    @property
    def name(self) -> str: ...

    @property
    def warmup(self) -> int: ...

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]: ...


__all__ = [
    "BarSample",
    "PanelCoverage",
    "PanelWindow",
    "PricePanel",
    "StrategySignal",
    "TickerCoverage",
]
