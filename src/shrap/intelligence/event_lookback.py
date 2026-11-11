"""The look-back: the firm's biggest one-day moves, and what was on file when they happened.

ADR-0018 card 1. An event plan is only worth writing for a kind of event that
has demonstrably moved a name the firm holds, so before any plan exists this
asks the firm's own history which kinds those are:

1. find every unusually large one-day move in the universe,
2. split the days the whole market moved from the days one name did,
3. attach whatever news and filings the firm had recorded in the window that
   could have caused each move,
4. count the causes.

**No model is involved.** A cause here is the category the News Analyzer or the
Filing Processor *already recorded* for an item, read back. This module adds no
judgment of its own, so its output can be no better than those verdicts, and it
says how many moves it could not explain rather than guessing.

Three things it refuses to blur, each of which this project has been bitten by:

- **A move before the corpus began is not an unexplained move.** The bar
  history goes back years and the news and filing tables began in 2026. A move
  from 2022 with nothing on file is a move the firm was not recording, and
  counting it as "no cause found" would make the corpus look blind (#251: a
  source that is not recording reads exactly like a quiet market).
- **An impossible return is a data fault until shown otherwise.** ETHA's
  1-for-3 split left a 198% one-day "return" in the table (#290). Anything past
  ``SUSPECT_RETURN`` is listed apart and never ranked as an event.
- **Evidence must precede the move.** The window closes at the session's own
  close. An item published that evening explains tomorrow, not today.

Pure functions only; the reads live in ``event_lookback_cli``.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from itertools import pairwise
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")
SESSION_CLOSE = time(16, 0)

# How many prior returns a move is measured against, and the fewest that make
# the comparison mean anything.
DEFAULT_VOL_WINDOW = 60
MIN_VOL_OBSERVATIONS = 20
DEFAULT_Z_MIN = 3.0
# A floor in absolute terms as well, so a bond or dollar fund's 3-sigma day at
# half a percent does not rank beside a stock's.
DEFAULT_ABS_MIN = 0.02
# Past this a one-day return is more likely a corporate action the feed has not
# restated than a price anyone traded at.
SUSPECT_RETURN = 0.40
# A day is the market's, not a name's, when this many distinct names moved or
# any broad index fund did.
DEFAULT_BREADTH_MIN = 8
INDEX_FUNDS: frozenset[str] = frozenset({"SPY", "QQQ", "IWM", "DIA"})

CAUSE_MARKET_WIDE = "market-wide"
CAUSE_UNEXPLAINED = "unexplained"
CAUSE_BEFORE_CORPUS = "before-the-corpus"
KIND_NEWS = "news"
KIND_FILING = "filing"

# The two agents name the same thing differently. A results 8-K and an
# earnings headline are one cause.
_CATEGORY_ALIASES: Mapping[str, str] = {"results": "earnings"}
# Categories that say an item exists and nothing about why a price moved.
# ``unscored`` is an item the agent stored and has not yet judged: it is shown as
# evidence and cannot name a cause.
CATEGORY_UNSCORED = "unscored"
_UNINFORMATIVE: frozenset[str] = frozenset({"other", "other-events", CATEGORY_UNSCORED})


@dataclass(frozen=True)
class Close:
    session_date: date
    close: float


@dataclass(frozen=True)
class Move:
    """One name's unusually large session."""

    ticker: str
    session_date: date
    prior_session: date
    ret: float
    sigma: float

    @property
    def z(self) -> float:
        return abs(self.ret) / self.sigma if self.sigma > 0 else math.inf

    @property
    def window(self) -> tuple[datetime, datetime]:
        """From the prior close to this close: when a cause could have landed."""

        return (
            datetime.combine(self.prior_session, SESSION_CLOSE, tzinfo=MARKET_TZ),
            datetime.combine(self.session_date, SESSION_CLOSE, tzinfo=MARKET_TZ),
        )


@dataclass(frozen=True)
class Evidence:
    """One recorded item that names a ticker, with the verdict already on it."""

    kind: str
    ticker: str
    at: datetime
    category: str
    materiality: int
    text: str

    @property
    def cause(self) -> str:
        return _CATEGORY_ALIASES.get(self.category, self.category)


@dataclass(frozen=True)
class Attribution:
    move: Move
    cause: str
    market_wide: bool
    evidence: tuple[Evidence, ...]


def _utc(at: datetime) -> datetime:
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


def find_moves(
    closes: Mapping[str, Sequence[Close]],
    *,
    vol_window: int = DEFAULT_VOL_WINDOW,
    z_min: float = DEFAULT_Z_MIN,
    abs_min: float = DEFAULT_ABS_MIN,
) -> tuple[list[Move], list[Move]]:
    """Every large move, and separately every return too large to believe.

    A move is large against the name's *own* recent volatility, measured over
    the ``vol_window`` returns before it and never including it: a shock that
    is allowed to inflate its own yardstick hides itself. Suspect returns are
    also left out of the yardstick, or one unrestated split would mute a
    quarter of real moves after it.
    """

    moves: list[Move] = []
    suspect: list[Move] = []
    for ticker, series in closes.items():
        ordered = sorted(series, key=lambda c: c.session_date)
        history: list[float] = []
        for prev, cur in pairwise(ordered):
            if prev.close <= 0 or cur.close <= 0:
                continue
            ret = cur.close / prev.close - 1.0
            recent = history[-vol_window:]
            sigma = _stdev(recent) if len(recent) >= MIN_VOL_OBSERVATIONS else 0.0
            candidate = Move(ticker, cur.session_date, prev.session_date, ret, sigma)
            if abs(ret) >= SUSPECT_RETURN:
                suspect.append(candidate)
                continue
            if sigma > 0 and abs(ret) >= abs_min and abs(ret) >= z_min * sigma:
                moves.append(candidate)
            history.append(ret)
    key = lambda m: (m.session_date, m.ticker)  # noqa: E731
    return sorted(moves, key=key), sorted(suspect, key=key)


def _stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def market_wide_days(
    moves: Sequence[Move],
    *,
    breadth_min: int = DEFAULT_BREADTH_MIN,
    index_funds: frozenset[str] = INDEX_FUNDS,
) -> frozenset[date]:
    """Sessions on which the move belonged to the market rather than to a name."""

    by_day: dict[date, set[str]] = defaultdict(set)
    for move in moves:
        by_day[move.session_date].add(move.ticker)
    return frozenset(
        day
        for day, tickers in by_day.items()
        if len(tickers) >= breadth_min or tickers & index_funds
    )


def evidence_for(move: Move, evidence: Sequence[Evidence]) -> tuple[Evidence, ...]:
    """Items naming this ticker that landed after the prior close and by this one."""

    start, end = (_utc(t) for t in move.window)
    hits = [e for e in evidence if e.ticker == move.ticker and start < _utc(e.at) <= end]
    return tuple(sorted(hits, key=lambda e: (-e.materiality, _utc(e.at))))


def attribute(
    moves: Sequence[Move],
    evidence: Sequence[Evidence],
    *,
    corpus_start: datetime | None,
    breadth_min: int = DEFAULT_BREADTH_MIN,
) -> list[Attribution]:
    """Label each move with the best cause on file, or say there is none.

    Order of precedence, and why:

    1. **Before the corpus.** Nothing could be on file. Not a finding.
    2. **Market-wide.** When fifteen names fall together the cause is not the
       one of them that happened to file an 8-K that morning.
    3. **The most material informative item** naming the ticker in the window.
    4. **Unexplained.** Recorded as such. This is the number that says how much
       of what moves the firm its corpus does not see.

    ``corpus_start`` is the earliest timestamp of any evidence the firm holds,
    passed in rather than derived from ``evidence`` because the caller may have
    filtered that list to a window.
    """

    wide = market_wide_days(moves, breadth_min=breadth_min)
    by_ticker: dict[str, list[Evidence]] = defaultdict(list)
    for item in evidence:
        by_ticker[item.ticker].append(item)
    out: list[Attribution] = []
    for move in moves:
        is_wide = move.session_date in wide
        if corpus_start is None or _utc(move.window[1]) <= _utc(corpus_start):
            out.append(Attribution(move, CAUSE_BEFORE_CORPUS, is_wide, ()))
            continue
        hits = evidence_for(move, by_ticker.get(move.ticker, ()))
        if is_wide:
            out.append(Attribution(move, CAUSE_MARKET_WIDE, True, hits))
            continue
        informative = [e for e in hits if e.cause not in _UNINFORMATIVE]
        cause = informative[0].cause if informative else CAUSE_UNEXPLAINED
        out.append(Attribution(move, cause, False, hits))
    return out


@dataclass(frozen=True)
class CauseSummary:
    cause: str
    moves: int
    sessions: int
    tickers: int
    mean_abs_ret: float
    max_abs_ret: float


def summarise(attributions: Sequence[Attribution]) -> list[CauseSummary]:
    """One row per cause, most frequent first.

    ``sessions`` is the count that matters for a plan: forty names falling on
    one day is one event, not forty.
    """

    groups: dict[str, list[Attribution]] = defaultdict(list)
    for item in attributions:
        groups[item.cause].append(item)
    rows = [
        CauseSummary(
            cause=cause,
            moves=len(items),
            sessions=len({a.move.session_date for a in items}),
            tickers=len({a.move.ticker for a in items}),
            mean_abs_ret=sum(abs(a.move.ret) for a in items) / len(items),
            max_abs_ret=max(abs(a.move.ret) for a in items),
        )
        for cause, items in groups.items()
    ]
    return sorted(rows, key=lambda r: (-r.sessions, -r.moves, r.cause))


def _pct(value: float) -> str:
    return f"{value * 100:+.1f}%"


def render_report(
    attributions: Sequence[Attribution],
    suspect: Sequence[Move],
    *,
    first_session: date | None,
    last_session: date | None,
    corpus_start: datetime | None,
    tickers: int,
    top: int = 40,
    source_starts: Mapping[str, datetime | None] | None = None,
) -> str:
    """The report as Markdown. Counts first, then the moves a person can check.

    ``source_starts`` names when each evidence source began recording. The
    report shows them separately because ``corpus_start`` is one date standing
    for several producers, and a single date over several producers is how a
    dead source hid behind a live one in #251.
    """

    lines: list[str] = ["# Event look-back (ADR-0018, card 1)", ""]
    span = f"{first_session} to {last_session}" if first_session and last_session else "no bars"
    corpus = corpus_start.date().isoformat() if corpus_start else "nothing on file"
    lines += [
        f"- Bars: {tickers} names, {span}",
        f"- Moves are attributed from: {corpus}",
    ]
    for name, started in sorted((source_starts or {}).items()):
        when = started.date().isoformat() if started else "nothing on file"
        lines.append(f"  - {name} on file from: {when}")
    lines += [f"- Large moves found: {len(attributions)}", ""]
    if not attributions:
        lines.append("No large moves in the window. Nothing to attribute.")
        return "\n".join(lines)

    in_corpus = [a for a in attributions if a.cause != CAUSE_BEFORE_CORPUS]
    unexplained = [a for a in in_corpus if a.cause == CAUSE_UNEXPLAINED]
    lines += ["## What the corpus could and could not see", ""]
    lines.append(
        f"- {len(attributions) - len(in_corpus)} moves fall before the corpus began. "
        "They are not evidence about anything and are excluded below."
    )
    if in_corpus:
        share = len(unexplained) / len(in_corpus)
        lines.append(
            f"- Of the {len(in_corpus)} moves since, **{len(unexplained)} "
            f"({share:.0%}) have no informative item on file.** That share is how "
            "much of what moves the firm its corpus does not explain."
        )
    else:
        lines.append(
            "- **No move falls inside the corpus's lifetime, so nothing below is a "
            "finding.** Re-run when the corpus has lived through more sessions."
        )
    lines.append("")

    lines += [
        "## Causes, ranked by how many sessions they hit",
        "",
        "| Cause | Sessions | Moves | Names | Mean move | Largest |",
        "|---|---|---|---|---|---|",
    ]
    for row in summarise(in_corpus):
        lines.append(
            f"| {row.cause} | {row.sessions} | {row.moves} | {row.tickers} "
            f"| {row.mean_abs_ret * 100:.1f}% | {row.max_abs_ret * 100:.1f}% |"
        )
    lines += [
        "",
        "A cause is the category the News Analyzer or Filing Processor recorded "
        "for the most material item in the window. It is a candidate, not a "
        "verdict: read the items before writing a plan from a row.",
        "",
        f"## The {min(top, len(in_corpus))} largest moves since the corpus began",
        "",
        "| Session | Name | Move | Sigmas | Cause | Most material item on file |",
        "|---|---|---|---|---|---|",
    ]
    ranked = sorted(in_corpus, key=lambda a: -abs(a.move.ret))[:top]
    for item in ranked:
        best = item.evidence[0] if item.evidence else None
        text = "" if best is None else f"[{best.kind}] {best.text[:110]}".replace("|", "/")
        lines.append(
            f"| {item.move.session_date} | {item.move.ticker} | {_pct(item.move.ret)} "
            f"| {item.move.z:.1f} | {item.cause} | {text} |"
        )
    if suspect:
        lines += [
            "",
            "## Returns too large to believe",
            "",
            f"One-day returns of {SUSPECT_RETURN:.0%} or more. Treated as data "
            "faults (#290) and left out of everything above until checked.",
            "",
            "| Session | Name | Return |",
            "|---|---|---|",
        ]
        lines += [f"| {m.session_date} | {m.ticker} | {_pct(m.ret)} |" for m in suspect]
    return "\n".join(lines)


__all__ = [
    "CATEGORY_UNSCORED",
    "CAUSE_BEFORE_CORPUS",
    "CAUSE_MARKET_WIDE",
    "CAUSE_UNEXPLAINED",
    "DEFAULT_ABS_MIN",
    "DEFAULT_BREADTH_MIN",
    "DEFAULT_VOL_WINDOW",
    "DEFAULT_Z_MIN",
    "INDEX_FUNDS",
    "KIND_FILING",
    "KIND_NEWS",
    "SUSPECT_RETURN",
    "Attribution",
    "CauseSummary",
    "Close",
    "Evidence",
    "Move",
    "attribute",
    "evidence_for",
    "find_moves",
    "market_wide_days",
    "render_report",
    "summarise",
]
