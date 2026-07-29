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

import math
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

    **Price accessors return a ticker's real bars, not the panel grid.** The
    panel spans every date any member traded, so a name that listed midway has
    no bar for the dates before it existed. ``closes`` skips those rather than
    padding them, which is what lets a strategy stay ignorant of listing dates:
    a rule that needs 126 bars asks for 126 bars, and a name that has not traded
    that many times simply does not supply them. It answers "can I compute my
    signal for this name" without ever asking how old the name is.

    Padding instead — with zeros, or by forward-filling — would make a
    six-month-old name look like it had years of flat history and rank it as the
    calmest momentum name in the universe.
    """

    __slots__ = ("_index", "_panel")

    def __init__(self, panel: PricePanel, index: int) -> None:
        self._panel = panel
        self._index = index

    @property
    def tickers(self) -> tuple[str, ...]:
        """Every ticker in the panel, listed or not.

        Deliberately not just the live ones: the engine recovers trades by
        diffing weights per ticker, so a strategy must be able to name every
        ticker it is flat in. An omitted ticker reads as "unchanged", which is a
        silent way to never sell.
        """

        return self._panel.tickers

    @property
    def live_tickers(self) -> tuple[str, ...]:
        """Tickers with a bar on the current date — the investable universe now."""

        return tuple(t for t in self._panel.tickers if self._panel.is_live(t, self._index))

    @property
    def current_index(self) -> int:
        return self._index

    @property
    def current_date(self) -> date:
        return self._panel.dates[self._index]

    def dates(self) -> tuple[date, ...]:
        return self._panel.dates[: self._index + 1]

    def closes(self, ticker: str) -> tuple[float, ...]:
        return self._panel.history(ticker, self._panel.closes, self._index)

    def volumes(self, ticker: str) -> tuple[float, ...]:
        return self._panel.history(ticker, self._panel.volumes, self._index)


@dataclass(frozen=True, slots=True)
class PricePanel:
    """Point-in-time daily OHLCV across one or more tickers, ragged by design.

    The panel spans the **union** of its members' session dates, and ``live``
    records which tickers actually traded on each of them. A name that listed
    partway through simply has no bars before it existed.

    This replaced an intersection — a date survived only if *every* ticker had a
    bar — which was correct point-in-time behaviour and quietly disastrous in
    practice: it made every name inherit the youngest one's age. On the 50-name
    launch list, ETHA (listed 2024-07) truncated a five-year panel to about two
    years, discarding history that SPY had sitting in the same table. The data
    was not missing. ETHA cannot have bars before it listed, and under an
    intersection that fact propagated to all fifty names.

    A cross-sectional universe grows over time, and a backtest that cannot
    express that can only be run over names that already existed at the start —
    which is a selection rule masquerading as an alignment rule.

    **Still no forward-fill and no fabricated bars.** A missing bar is marked
    absent and carries ``nan``, so any arithmetic that reaches one produces
    ``nan`` rather than a plausible number. Loud beats silent: a zero would book
    a -100% return, and a forward-fill would invent a flat price series that
    ranks as the calmest name in the universe.
    """

    tickers: tuple[str, ...]
    dates: tuple[date, ...]
    opens: dict[str, tuple[float, ...]]
    highs: dict[str, tuple[float, ...]]
    lows: dict[str, tuple[float, ...]]
    closes: dict[str, tuple[float, ...]]
    volumes: dict[str, tuple[float, ...]]
    live: dict[str, tuple[bool, ...]]

    @property
    def n_bars(self) -> int:
        return len(self.dates)

    def is_live(self, ticker: str, index: int) -> bool:
        """Did ``ticker`` trade on bar ``index``?"""

        flags = self.live.get(ticker)
        if flags is None or not 0 <= index < len(flags):
            return False
        return flags[index]

    def history(
        self, ticker: str, series: Mapping[str, tuple[float, ...]], index: int
    ) -> tuple[float, ...]:
        """``ticker``'s real values from ``series`` through bar ``index``.

        Absent bars are dropped rather than padded, so the length of what comes
        back is how many times the name has actually traded. Every "do I have
        enough history" check in every strategy is a length check, so they all
        get the right answer for a newly-listed name without knowing it is one.
        """

        values = series.get(ticker)
        flags = self.live.get(ticker)
        if values is None or flags is None:
            return ()
        stop = min(index + 1, len(values))
        return tuple(values[i] for i in range(stop) if flags[i])

    def window(self, index: int) -> PanelWindow:
        if not 0 <= index < self.n_bars:
            raise IndexError(f"bar index {index} out of range [0, {self.n_bars})")
        return PanelWindow(self, index)

    @classmethod
    def from_bars(cls, bars_by_ticker: Mapping[str, Sequence[BarSample]]) -> PricePanel:
        """Build a panel over every date any ticker traded."""

        if not bars_by_ticker:
            raise ValueError("PricePanel.from_bars requires at least one ticker")
        tickers = tuple(bars_by_ticker.keys())
        by_ticker: dict[str, dict[date, BarSample]] = {}
        every: set[date] = set()
        for ticker, bars in bars_by_ticker.items():
            indexed = {bar.session_date: bar for bar in bars}
            by_ticker[ticker] = indexed
            every |= set(indexed)
        dates = tuple(sorted(every))
        opens: dict[str, tuple[float, ...]] = {}
        highs: dict[str, tuple[float, ...]] = {}
        lows: dict[str, tuple[float, ...]] = {}
        closes: dict[str, tuple[float, ...]] = {}
        volumes: dict[str, tuple[float, ...]] = {}
        live: dict[str, tuple[bool, ...]] = {}
        for ticker in tickers:
            indexed = by_ticker[ticker]
            rows = [indexed.get(d) for d in dates]
            opens[ticker] = tuple(r.open if r else math.nan for r in rows)
            highs[ticker] = tuple(r.high if r else math.nan for r in rows)
            lows[ticker] = tuple(r.low if r else math.nan for r in rows)
            closes[ticker] = tuple(r.close if r else math.nan for r in rows)
            volumes[ticker] = tuple(r.volume if r else math.nan for r in rows)
            live[ticker] = tuple(r is not None for r in rows)
        return cls(
            tickers=tickers,
            dates=dates,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            live=live,
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
    "PanelWindow",
    "PricePanel",
    "StrategySignal",
]
