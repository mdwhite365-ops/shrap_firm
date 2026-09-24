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
from dataclasses import dataclass, field
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

    def rewind(self, index: int) -> PanelWindow:
        """The same panel as of an earlier bar. Never a later one.

        For rules that rebalance on a schedule: between rebalances they hold what
        they chose on the last rebalance date, which is the window at that date.
        Clamped to the current bar, so rewinding cannot become a way to peek.
        """

        return PanelWindow(self._panel, max(0, min(index, self._index)))

    def closes(self, ticker: str) -> tuple[float, ...]:
        return self._panel.history_cached(ticker, "closes", self._index)

    def aligned_closes(self, ticker: str) -> tuple[float, ...]:
        """The grid-aligned prefix: one entry per panel date, ``nan`` where absent.

        The counterpart to :meth:`closes`, which compresses a name's history to
        the bars it actually has. Compression is right for a per-name signal —
        a rule wanting 126 bars asks for 126 bars and stays ignorant of listing
        dates — and wrong for anything comparing two names across time, because
        two compressed series of equal length need not cover the same dates.

        A cross-sectional correlation computed on compressed series would pair
        one name's Tuesday with another's Thursday and report the result as a
        relationship. This returns the grid, so a caller can require that both
        names actually traded on every date it uses.
        """

        series = self._panel.closes.get(ticker)
        return () if series is None else series[: self._index + 1]

    def volumes(self, ticker: str) -> tuple[float, ...]:
        return self._panel.history_cached(ticker, "volumes", self._index)

    def market_caps(self, ticker: str) -> tuple[float, ...]:
        """``close x shares outstanding`` up to and including the current bar.

        Compressed like :meth:`closes`, so a rule asking for 21 observations
        gets 21 and stays ignorant of listing dates. A name with no share
        history yields an all-``nan`` series; a cross-sectional ranking must
        drop those rather than sort them.
        """

        return self._panel.history_cached(ticker, "market_caps", self._index)


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
    """How long the panel is, and how thinly populated its early stretch was.

    Every gate in the protocol is a claim about a sample, and the sample size was
    the one number the verdict never carried until #136.

    **Restated for the ragged panel.** ``n_bars`` used to be the intersection —
    the panel really was only as long as its shortest ticker — and reporting it
    was how the ETHA truncation became visible in the first place. The panel now
    spans the union, so ``n_bars`` is the union and the question changes: not
    *how much history did one young name cost everyone*, which is now none, but
    *how many names was this actually measured across, and when*.

    ``fully_covered`` is the old intersection, kept because it still answers
    something worth knowing: the number of dates on which the entire universe
    was trading. A panel of 1,303 bars whose universe was only complete for the
    last 525 is a real caveat on a cross-sectional result — the early folds
    ranked a smaller universe than the late ones.

    ``thinnest`` ranks by dates *missed*, so it catches a ticker with interior
    holes as readily as a late listing. Under an intersection both truncated the
    panel; under a union both instead shrink the cross-section on those dates,
    which is milder and still worth naming.
    """

    n_bars: int
    fully_covered: int
    first_date: date | None
    last_date: date | None
    per_ticker: tuple[TickerCoverage, ...]

    @property
    def thinnest(self) -> TickerCoverage | None:
        """The ticker absent from the most dates, or ``None`` if all are complete."""

        ranked = [c for c in self.per_ticker if c.missing > 0]
        if not ranked:
            return None
        return max(ranked, key=lambda c: (c.missing, c.ticker))

    def summary(self) -> str:
        """One field for the verdict line: ``bars=1303 complete=525 thinnest=ETHA``.

        ``complete`` is omitted when the universe was whole throughout, because
        then it equals ``bars`` and repeating it is noise.
        """

        line = f"bars={self.n_bars}"
        thinnest = self.thinnest
        if thinnest is not None:
            line += f" complete={self.fully_covered} thinnest={thinnest.ticker}"
        return line

    @classmethod
    def from_bars(cls, bars_by_ticker: Mapping[str, Sequence[BarSample]]) -> PanelCoverage:
        """Measure the same inputs :meth:`PricePanel.from_bars` aligns."""

        dates_by_ticker = {
            ticker: {bar.session_date for bar in bars} for ticker, bars in bars_by_ticker.items()
        }
        # The union IS the panel now, so this is its length rather than a
        # hypothetical ceiling the intersection fell short of.
        spanned: set[date] = set()
        for seen in dates_by_ticker.values():
            spanned |= seen
        complete: set[date] | None = None
        for seen in dates_by_ticker.values():
            complete = seen if complete is None else (complete & seen)
        complete = complete or set()
        per_ticker = tuple(
            TickerCoverage(
                ticker=ticker,
                n_bars=len(seen),
                missing=len(spanned - seen),
                first_date=min(seen) if seen else None,
                last_date=max(seen) if seen else None,
            )
            for ticker, seen in dates_by_ticker.items()
        )
        return cls(
            n_bars=len(spanned),
            fully_covered=len(complete),
            first_date=min(spanned) if spanned else None,
            last_date=max(spanned) if spanned else None,
            per_ticker=per_ticker,
        )


def _market_cap_series(
    dates: Sequence[date],
    closes: Sequence[float],
    shares: Sequence[tuple[date, float]],
) -> tuple[float, ...]:
    """``close x shares`` per panel date, using only counts already filed.

    **The selection is on ``filed_at``, never on the date the count describes**,
    and that is the whole point-in-time rule. A count describing 2024-03-31 may
    not be filed until 2024-05-09; using it on 2024-04-15 is look-ahead the
    backtest would reward and the live book could not reproduce. See
    :mod:`shrap.market_data.shares` — this is the same discipline one layer up,
    applied against the panel's own grid.

    ``nan`` before the first filing and wherever the close is absent. A name the
    firm has no share history for is ``nan`` throughout rather than zero: zero is
    a number a ranking would happily sort, and "smallest company in the
    universe" is the wrong answer to "we do not know".
    """

    if not shares:
        return tuple(math.nan for _ in dates)
    ordered = sorted(shares, key=lambda row: row[0])
    out: list[float] = []
    cursor = 0
    latest = math.nan
    for i, day in enumerate(dates):
        # `filed_at <= day`, so a count filed *on* the session date counts: it
        # was public that morning.
        while cursor < len(ordered) and ordered[cursor][0] <= day:
            latest = ordered[cursor][1]
            cursor += 1
        close = closes[i]
        out.append(math.nan if math.isnan(latest) or math.isnan(close) else close * latest)
    return tuple(out)


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
    market_caps: dict[str, tuple[float, ...]] = field(default_factory=dict)
    """``close x shares``, point-in-time on ``filed_at``. See :func:`_market_cap_series`.

    Defaulted so every existing construction of a panel keeps working and
    simply has no market caps — a panel built without share history is a
    valid panel, not a broken one.
    """

    # Derived in __post_init__, never by a caller. `compare=False` keeps two
    # panels with identical inputs equal, and `repr=False` keeps a panel's repr
    # readable — these hold one compressed copy of every series.
    _compressed: dict[str, dict[str, tuple[float, ...]]] = field(
        default_factory=dict, repr=False, compare=False
    )
    _live_counts: dict[str, tuple[int, ...]] = field(
        default_factory=dict, repr=False, compare=False
    )

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

        **The reference implementation.** :meth:`history_cached` returns the
        identical tuple for every input and is what the hot path uses; this
        stays as the definition that one is tested against, and as the fallback
        for a series with no precomputed form.
        """

        values = series.get(ticker)
        flags = self.live.get(ticker)
        if values is None or flags is None:
            return ()
        stop = min(index + 1, len(values))
        return tuple(values[i] for i in range(stop) if flags[i])

    def history_cached(self, ticker: str, series_name: str, index: int) -> tuple[float, ...]:
        """:meth:`history`, answered from precomputed state. Bit-identical.

        **The arithmetic that makes this worth doing.** ``history`` rebuilds the
        whole prefix on every call, and a strategy calls it once per ticker per
        bar, so a backtest costs ``tickers x bars^2 / 2`` element visits. On the
        firm's 15-minute panel — 50 names, 37,116 bars — that is **34.6 billion**
        generator iterations. Measured on the Dell at 9.3M/s in situ, one
        evaluation takes about an hour per pass and the walk-forward runs two
        expensive passes. A single intraday backtest cost ~2.5 hours, and the
        toll grows QUADRATICALLY: doubling the history quadruples the runtime.

        **Why it is provably the same answer.** ``history`` yields ``values[i]``
        for ascending ``i`` where ``flags[i]``, stopping after ``index``. That is
        by definition the first ``K`` entries of the fully compressed series,
        where ``K`` is how many live bars fall at or before ``index``. Both are
        precomputed here, so this is the same elements in the same order —
        not an approximation, and not a faster algorithm that might round
        differently. It is a tuple slice of a tuple the generator would have
        rebuilt element by element.

        Still O(K), because the caller wants the prefix. The win is the
        constant: a slice is one C-level copy rather than K Python iterations
        with a conditional. The deeper fix — letting a strategy ask for two
        elements instead of a whole prefix — changes the strategy seam and is
        deliberately not in this card.
        """

        compressed = self._compressed.get(series_name)
        counts = self._live_counts.get(ticker)
        if compressed is None or counts is None:
            return ()
        values = compressed.get(ticker)
        if values is None or index < 0 or not counts:
            return ()
        return values[: counts[min(index, len(counts) - 1)]]

    def __post_init__(self) -> None:
        """Precompute the compressed series and the live-bar prefix counts.

        Built here rather than in :meth:`from_bars` because panels are also
        constructed directly (tests do), and a cache that only exists on one
        construction path is a cache that is silently absent on the other.

        Mutating these dicts does not violate ``frozen``: the fields are bound
        once by the dataclass and only their contents are filled, so the panel
        remains immutable to anything outside this method.
        """

        # `market_caps` compresses on the same `live` flags as the price series,
        # which is what keeps `market_caps(t)[i]` the cap of `closes(t)[i]`. It is
        # compressed by "did the name trade", never by "is the value nan" — a name
        # that traded before its first filing has a real bar and an unknown cap,
        # and dropping that position would shift every later value by one.
        for name, series in (
            ("closes", self.closes),
            ("volumes", self.volumes),
            ("market_caps", self.market_caps),
        ):
            self._compressed[name] = {
                ticker: tuple(
                    value
                    for position, value in enumerate(series.get(ticker, ()))
                    if position < len(self.live.get(ticker, ()))
                    and self.live.get(ticker, ())[position]
                )
                for ticker in self.tickers
            }
        for ticker in self.tickers:
            running = 0
            counts: list[int] = []
            for flag in self.live.get(ticker, ()):
                if flag:
                    running += 1
                counts.append(running)
            self._live_counts[ticker] = tuple(counts)

    def window(self, index: int) -> PanelWindow:
        if not 0 <= index < self.n_bars:
            raise IndexError(f"bar index {index} out of range [0, {self.n_bars})")
        return PanelWindow(self, index)

    @classmethod
    def from_bars(
        cls,
        bars_by_ticker: Mapping[str, Sequence[BarSample]],
        shares_by_ticker: Mapping[str, Sequence[tuple[date, float]]] | None = None,
    ) -> PricePanel:
        """Build a panel over every date any ticker traded.

        ``shares_by_ticker`` is each name's reported share counts as
        ``(filed_at, shares)``, newest last. Market capitalisation is derived
        here as ``close x shares`` against **this panel's own closes**, and that
        is the whole reason it is computed here rather than read from SQL.

        `market_data.shares_store` has a `SELECT_MARKET_CAP_SQL` that joins
        shares onto `daily_bars` itself. Using it would mean the panel's price
        and the panel's market cap came from two independent reads, each with
        its own `adjustment` and `source` arguments — and since #247 the
        evaluator can run on either feed. Two reads that disagree about which
        feed they used would produce a market cap that is not close x shares for
        the close the strategy sees. That is this project's recurring defect
        exactly: a component recomputing a fact the system already holds, and
        the two copies disagreeing.

        Names with no share history get ``nan`` throughout, the same as a name
        with no bar. A strategy ranking on market cap must handle that; it is
        the honest representation of four multi-class issuers whose counts SEC
        does not publish (see #253).
        """

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
        market_caps: dict[str, tuple[float, ...]] = {}
        shares_by_ticker = shares_by_ticker or {}
        for ticker in tickers:
            indexed = by_ticker[ticker]
            rows = [indexed.get(d) for d in dates]
            opens[ticker] = tuple(r.open if r else math.nan for r in rows)
            highs[ticker] = tuple(r.high if r else math.nan for r in rows)
            lows[ticker] = tuple(r.low if r else math.nan for r in rows)
            closes[ticker] = tuple(r.close if r else math.nan for r in rows)
            volumes[ticker] = tuple(r.volume if r else math.nan for r in rows)
            live[ticker] = tuple(r is not None for r in rows)
            market_caps[ticker] = _market_cap_series(
                dates, closes[ticker], shares_by_ticker.get(ticker, ())
            )
        return cls(
            tickers=tickers,
            dates=dates,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            live=live,
            market_caps=market_caps,
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
