"""Shares outstanding from EDGAR XBRL, and the market capitalisation it unlocks.

**Why this exists.** KI-035: the Hypothesis Generator recorded seven
``capability-gap`` rows — testable effects it could not compute because the firm
lacks the data. The cheapest of the seven asks for market capitalisation, to rank
names and weight a portfolio. No ``market_cap``, ``shares`` or ``outstanding``
column existed anywhere in the database, so the gap was real and this closes it.

Market cap is ``shares outstanding x price``. The firm already stores the price.

**The whole difficulty is point-in-time correctness, and it is the reason this
module is more than a division.**

A share count is a fact about a moment, and it moves — buybacks, issuance,
secondary offerings. Two different dates matter and conflating them injects
look-ahead bias that nothing downstream would notice:

- ``as_of`` — the date the count *describes* (XBRL's ``end``).
- ``filed_at`` — the date it *became public* (XBRL's ``filed``).

A count describing 2024-03-31 may not be filed until 2024-05-09. Computing a
2024-04-15 market cap from it uses information that did not exist on that date.
The backtest would look better and the strategy would not work, which is the
worst failure mode available.

So :func:`shares_as_of` selects on ``filed_at <= on``, never on ``as_of``. The
firm already enforces no-peek in the strategy layer through ``PanelWindow``;
this is the same discipline one layer down, where the panel cannot see it.

**Amendments are kept, not overwritten.** A restated count arrives as a new row
with a later ``filed_at``, and the old row stays. A backtest replaying 2024 must
see what was believed in 2024, including what was later corrected — overwriting
would quietly rewrite history in the firm's favour.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

# The XBRL concept carrying the cover-page share count. `dei` is the Document and
# Entity Information taxonomy, which every registrant files; the alternative
# (`us-gaap:CommonStockSharesOutstanding`) is balance-sheet data and is both
# sparser and later.
SHARES_CONCEPT = "EntityCommonStockSharesOutstanding"
SHARES_TAXONOMY = "dei"

COMPANY_CONCEPT_BASE = "https://data.sec.gov/api/xbrl/companyconcept"

SOURCE_EDGAR_XBRL = "edgar-xbrl"


def company_concept_url(
    cik: str, *, taxonomy: str = SHARES_TAXONOMY, concept: str = SHARES_CONCEPT
) -> str:
    """The companyconcept endpoint for one registrant and one XBRL concept.

    CIK is zero-padded to ten digits, which the API requires and which
    ``normalize_cik`` deliberately strips — so the padding happens here rather
    than changing a function three other callers depend on.
    """

    digits = "".join(ch for ch in str(cik) if ch.isdigit()).lstrip("0")
    return f"{COMPANY_CONCEPT_BASE}/CIK{digits.zfill(10)}/{taxonomy}/{concept}.json"


@dataclass(frozen=True, slots=True)
class SharesRow:
    """One reported share count, bound for ``market_data.shares_outstanding``."""

    ticker: str
    cik: str
    as_of: date
    """The date the count describes. NOT the date it may be used from."""

    filed_at: date
    """The date it became public. This is the no-peek boundary."""

    shares: float
    unit: str
    form: str | None
    source: str = SOURCE_EDGAR_XBRL


def parse_company_concept(
    payload: Mapping[str, object], *, ticker: str, cik: str
) -> list[SharesRow]:
    """Every reported share count in a ``companyconcept`` response.

    The payload shape is ``{"units": {"shares": [{end, val, filed, form, ...}]}}``.
    Entries missing any of end/val/filed are dropped rather than defaulted: a
    share count with no filing date cannot be placed in time, and a row that
    cannot be placed in time is worse than an absent one because it will still
    be selected by a point-in-time query.
    """

    units = payload.get("units")
    if not isinstance(units, Mapping):
        return []
    rows: list[SharesRow] = []
    for unit_name, entries in units.items():
        if not isinstance(entries, Iterable):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            row = _row_from_entry(entry, ticker=ticker, cik=cik, unit=str(unit_name))
            if row is not None:
                rows.append(row)
    # Stable order: oldest filing first, so an upsert replays history forwards.
    rows.sort(key=lambda r: (r.filed_at, r.as_of))
    return rows


def _row_from_entry(
    entry: Mapping[str, object], *, ticker: str, cik: str, unit: str
) -> SharesRow | None:
    end = _as_date(entry.get("end"))
    filed = _as_date(entry.get("filed"))
    value = entry.get("val")
    if end is None or filed is None or not isinstance(value, (int, float)):
        return None
    if isinstance(value, bool) or value <= 0:
        return None
    form = entry.get("form")
    return SharesRow(
        ticker=ticker.strip().upper(),
        cik=cik,
        as_of=end,
        filed_at=filed,
        shares=float(value),
        unit=unit,
        form=str(form) if isinstance(form, str) else None,
    )


def _as_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def shares_as_of(rows: Sequence[SharesRow], on: date) -> SharesRow | None:
    """The share count the firm could have known on ``on``.

    **Selects on ``filed_at``, never on ``as_of``** — see the module docstring.
    Among rows filed by that date, the one describing the most recent period
    wins; ties break on the later filing, which is the amendment.

    Returns ``None`` when nothing had been filed yet, and the caller must treat
    that as "no market cap for this name on this date" rather than substituting
    a later value.
    """

    visible = [row for row in rows if row.filed_at <= on]
    if not visible:
        return None
    return max(visible, key=lambda r: (r.as_of, r.filed_at))


def market_cap(shares: SharesRow | None, close: float | None) -> float | None:
    """``shares x close``, or ``None`` when either input is missing.

    None rather than 0.0: a market cap of zero is a rankable number and would
    sort a name to the bottom of a size screen instead of excluding it.
    """

    if shares is None or close is None or close <= 0.0:
        return None
    return shares.shares * close


__all__ = [
    "COMPANY_CONCEPT_BASE",
    "SHARES_CONCEPT",
    "SHARES_TAXONOMY",
    "SOURCE_EDGAR_XBRL",
    "SharesRow",
    "company_concept_url",
    "market_cap",
    "parse_company_concept",
    "shares_as_of",
]
