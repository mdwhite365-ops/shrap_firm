"""Measured links between assets: does one moving go with another moving, and how reliably.

ADR-0018 card 8, the measuring half. §6 of that ADR says any plan that depends
on one asset moving another must cite the measured relationship and the window
it was measured over, because "oil up, stocks down" holds in a supply scare and
reverses when oil rises on demand. This is the measurement.

For a driver (XLE, TLT, UUP, GLD, ...) and each name it reports:

- the correlation of daily returns over the whole span,
- the same **with the market taken out of both**, which is the number that
  matters: two stocks both rising with SPY says nothing about each other,
- how many non-overlapping windows agree in sign with the whole-span figure.

**The third number is the point.** A correlation of -0.3 over five years that
was -0.6 in half the windows and +0.1 in the rest is not a relationship a plan
can lean on, and the whole-span figure hides that entirely. A link is called
``stable`` only when it is both strong enough to notice and the same sign
nearly every window.

Nothing here is a forecast, and no threshold is a ruling. Pure functions; the
reads live in ``relationships_cli``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

DEFAULT_WINDOW = 63
"""Sessions per window: about a quarter."""
MIN_WINDOW_OBSERVATIONS = 40
MIN_WINDOWS = 4
STRONG = 0.30
STABLE_AGREEMENT = 0.80
UNSTABLE_AGREEMENT = 0.60

VERDICT_STABLE = "stable"
VERDICT_WEAK = "weak"
VERDICT_UNSTABLE = "unstable"
VERDICT_TOO_SHORT = "too-short"

Returns = Mapping[date, float]


def returns_from_closes(closes: Sequence[tuple[date, float]]) -> dict[date, float]:
    """Close-to-close returns keyed by the later session."""

    ordered = sorted(closes)
    out: dict[date, float] = {}
    for (_, prev), (day, cur) in pairwise(ordered):
        if prev > 0 and cur > 0:
            out[day] = cur / prev - 1.0
    return out


def _corr(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / math.sqrt(sxx * syy)


def _beta(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Slope of ``ys`` on ``xs``."""

    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx


def residual(series: Returns, market: Returns) -> dict[date, float]:
    """``series`` with its market beta removed, on the sessions both have.

    One beta over the whole span. That is crude, and it is the same crudeness
    for every name, which is what a comparison needs.
    """

    days = sorted(set(series) & set(market))
    xs = [market[d] for d in days]
    ys = [series[d] for d in days]
    beta = _beta(xs, ys)
    if beta is None:
        return {}
    my, mx = sum(ys) / len(ys), sum(xs) / len(xs)
    return {d: (series[d] - my) - beta * (market[d] - mx) for d in days}


@dataclass(frozen=True)
class Link:
    driver: str
    name: str
    sessions: int
    first: date | None
    last: date | None
    corr: float | None
    corr_ex_market: float | None
    beta_ex_market: float | None
    windows: int
    agreement: float | None
    latest_window: float | None
    verdict: str


def _windowed(days: Sequence[date], window: int) -> list[Sequence[date]]:
    """Non-overlapping windows, newest last. A short leading stub is dropped,
    so the newest window is always whole."""

    out: list[Sequence[date]] = []
    end = len(days)
    while end - window >= 0:
        out.append(days[end - window : end])
        end -= window
    return list(reversed(out))


def measure(
    driver: str,
    name: str,
    driver_returns: Returns,
    name_returns: Returns,
    market: Returns | None,
    *,
    window: int = DEFAULT_WINDOW,
) -> Link:
    """One link, with the market removed from both sides when it is supplied.

    ``market`` is ``None`` when the driver *is* the market: removing SPY from
    SPY leaves nothing to correlate.
    """

    days = sorted(set(driver_returns) & set(name_returns))
    raw = _corr([driver_returns[d] for d in days], [name_returns[d] for d in days])
    if market is None:
        dx, nx = dict(driver_returns), dict(name_returns)
    else:
        dx, nx = residual(driver_returns, market), residual(name_returns, market)
    common = sorted(set(dx) & set(nx))
    xs, ys = [dx[d] for d in common], [nx[d] for d in common]
    overall = _corr(xs, ys)
    per_window: list[float] = []
    for chunk in _windowed(common, window):
        if len(chunk) < MIN_WINDOW_OBSERVATIONS:
            continue
        value = _corr([dx[d] for d in chunk], [nx[d] for d in chunk])
        if value is not None:
            per_window.append(value)
    agreement: float | None = None
    if overall is not None and per_window:
        same = sum(1 for v in per_window if v * overall > 0)
        agreement = same / len(per_window)
    if overall is None or agreement is None or len(per_window) < MIN_WINDOWS:
        verdict = VERDICT_TOO_SHORT
    elif agreement < UNSTABLE_AGREEMENT:
        verdict = VERDICT_UNSTABLE
    elif abs(overall) >= STRONG and agreement >= STABLE_AGREEMENT:
        verdict = VERDICT_STABLE
    else:
        verdict = VERDICT_WEAK
    return Link(
        driver=driver,
        name=name,
        sessions=len(common),
        first=common[0] if common else None,
        last=common[-1] if common else None,
        corr=raw,
        corr_ex_market=overall,
        beta_ex_market=_beta(xs, ys),
        windows=len(per_window),
        agreement=agreement,
        latest_window=per_window[-1] if per_window else None,
        verdict=verdict,
    )


def _fmt(value: float | None, spec: str = "+.2f") -> str:
    return "n/a" if value is None else format(value, spec)


def render(links: Sequence[Link], *, market: str, window: int) -> str:
    lines = [
        "# Measured relationships (ADR-0018, card 8)",
        "",
        f"Daily returns, with {market} removed from both sides. Windows are "
        f"{window} sessions, non-overlapping. **Agree** is the share of windows "
        "with the same sign as the whole span.",
        "",
        f"A link is `{VERDICT_STABLE}` at |corr| of {STRONG:.2f} or more and "
        f"{STABLE_AGREEMENT:.0%} agreement, `{VERDICT_UNSTABLE}` under "
        f"{UNSTABLE_AGREEMENT:.0%} agreement, and `{VERDICT_WEAK}` otherwise. "
        "These are first cuts. A plan should cite a `stable` link or say why not.",
    ]
    drivers = sorted({link.driver for link in links})
    for driver in drivers:
        rows = [link for link in links if link.driver == driver]
        rows.sort(key=lambda r: -abs(r.corr_ex_market or 0.0))
        span = next(((r.first, r.last) for r in rows if r.first), (None, None))
        lines += [
            "",
            f"## {driver}  ({span[0]} to {span[1]})",
            "",
            "| Name | Corr ex-market | Raw corr | Beta | Windows | Agree | Latest | Verdict |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            lines.append(
                f"| {r.name} | {_fmt(r.corr_ex_market)} | {_fmt(r.corr)} "
                f"| {_fmt(r.beta_ex_market)} | {r.windows} | {_fmt(r.agreement, '.0%')} "
                f"| {_fmt(r.latest_window)} | {r.verdict} |"
            )
    return "\n".join(lines)


__all__ = [
    "DEFAULT_WINDOW",
    "VERDICT_STABLE",
    "VERDICT_TOO_SHORT",
    "VERDICT_UNSTABLE",
    "VERDICT_WEAK",
    "Link",
    "measure",
    "render",
    "residual",
    "returns_from_closes",
]
