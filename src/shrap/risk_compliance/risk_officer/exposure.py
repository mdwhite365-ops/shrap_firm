"""What the account currently holds, and what it would hold if an order filled.

The limits in ``limits.py`` are statements about the book *after* a trade, not
before it. An order that takes a 19% position to 24% must be judged on the 24%,
so every check here runs against a projected book rather than the current one.

Positions are signed. A short is a negative quantity and a negative market
value, which makes net exposure a plain sum and gross a sum of absolute values.
The Strategy Runner cannot currently open a short, so in practice every position
is long today — the sign is carried anyway because the alternative is arithmetic
that silently breaks the day that changes.

**Market value comes from the broker, not from quantity x a price we looked up.**
`ops.position_snapshots` carries the venue's own valuation. Recomputing it here
would introduce a second answer to "how big is this position", and the risk gate
should not be the component that disagrees with the broker about that.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

# Positions this stale are not the account's current book. The Reconciliation
# Agent writes a snapshot every pass (~300s), matching the Runner's equity
# tolerance in `research/strategy_runner/sizing.py`.
DEFAULT_MAX_POSITION_AGE = timedelta(minutes=30)


class ExposureUnavailable(Exception):
    """The current book cannot be established, so no order may be approved.

    Fail closed. Approving against an unknown book is the failure mode the
    spec calls the firm's worst: "a bug in the Risk Officer that fails *open*".
    """


@dataclass(frozen=True, slots=True)
class Position:
    """One holding, as the broker reports it."""

    ticker: str
    quantity: float
    market_value: float
    """Signed. Negative for a short."""

    @property
    def notional(self) -> float:
        return abs(self.market_value)


@dataclass(frozen=True, slots=True)
class BookExposure:
    """An account's positions expressed as fractions of its NAV."""

    nav: float
    positions: tuple[Position, ...]

    def __post_init__(self) -> None:
        if self.nav <= 0.0:
            raise ExposureUnavailable(f"account NAV is {self.nav}, which cannot carry exposure")

    @property
    def by_ticker(self) -> Mapping[str, float]:
        """Signed market value per ticker, summed across duplicate rows."""

        totals: dict[str, float] = {}
        for position in self.positions:
            key = position.ticker.strip().upper()
            totals[key] = totals.get(key, 0.0) + position.market_value
        return totals

    def weight(self, ticker: str) -> float:
        """Signed weight of one name. 0.0 when not held."""

        return self.by_ticker.get(ticker.strip().upper(), 0.0) / self.nav

    @property
    def gross(self) -> float:
        return sum(abs(value) for value in self.by_ticker.values()) / self.nav

    @property
    def net(self) -> float:
        return sum(self.by_ticker.values()) / self.nav

    def projected(self, ticker: str, delta_market_value: float) -> BookExposure:
        """The book as it would stand if an order for ``delta_market_value`` filled.

        ``delta_market_value`` is signed the way the trade is: a buy is
        positive, a sell or a short is negative. Selling more than is held
        crosses to short, which the arithmetic handles and the Runner does not
        currently generate.
        """

        key = ticker.strip().upper()
        remaining = [p for p in self.positions if p.ticker.strip().upper() != key]
        existing = self.by_ticker.get(key, 0.0)
        combined = existing + delta_market_value
        if combined != 0.0:
            remaining.append(Position(ticker=key, quantity=0.0, market_value=combined))
        return BookExposure(nav=self.nav, positions=tuple(remaining))


def assert_positions_usable(
    observed_at: datetime | None,
    now: datetime,
    *,
    max_age: timedelta = DEFAULT_MAX_POSITION_AGE,
) -> None:
    """Refuse a book too old to be the account's current one.

    An empty position list is a legitimate book — a flat account — so emptiness
    is never the failure signal. Absence of a *snapshot* is: it means the
    Reconciliation Agent has never run or cannot reach the broker, and the
    difference between "flat" and "unknown" is the whole point of this check.
    """

    if observed_at is None:
        raise ExposureUnavailable(
            "no position snapshot in ops.position_snapshots — the Reconciliation "
            "Agent writes one every pass, so this means it has never run or "
            "cannot reach the broker. Refusing to approve against an unknown book."
        )
    age = now - observed_at
    if age > max_age:
        raise ExposureUnavailable(
            f"position snapshot is {age} old (limit {max_age}) — a book this stale "
            "is not the account's current one. Refusing rather than guessing."
        )


def build_book(
    nav: float,
    positions: Iterable[Position],
    observed_at: datetime | None,
    now: datetime,
    *,
    max_age: timedelta = DEFAULT_MAX_POSITION_AGE,
) -> BookExposure:
    """Validate freshness and build the book, or raise :class:`ExposureUnavailable`."""

    assert_positions_usable(observed_at, now, max_age=max_age)
    return BookExposure(nav=nav, positions=tuple(positions))


__all__ = [
    "DEFAULT_MAX_POSITION_AGE",
    "BookExposure",
    "ExposureUnavailable",
    "Position",
    "assert_positions_usable",
    "build_book",
]
