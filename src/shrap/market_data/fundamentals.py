"""Company fundamentals from SEC XBRL, point in time.

**Why this exists.** The strategy engine could see price, volume and market cap.
The best-replicated stock anomalies in the literature — value, profitability,
investment, accruals — are ratios of accounting figures, and none were
computable. SEC serves every registrant's tagged financials free, in one
``companyfacts`` request per company; ``shares.py`` already reads that endpoint
for share counts. This reads the rest of it.

**Point in time, on ``filed_at``.** The same rule as shares, for the same reason:
a fiscal year ending 2024-12-31 is not public until the 10-K is filed in
February, and a backtest that used it in January would be trading on the future.
Every lookup here selects on the filing date and never on the period end. A
figure later restated arrives as another row with a later ``filed_at``, and
both are kept.

**Two kinds of metric, and the difference is load-bearing:**

- *flow* (revenue, earnings, cash flow) — only **annual** figures, meaning a
  reported duration of roughly a year. A 10-K repeats prior years and often
  quarters too, so "it came from a 10-K" does not identify an annual figure; the
  duration does. Quarterly figures are left out rather than summed into a
  trailing twelve months, because XBRL reports the fourth quarter only inside
  the annual figure and a TTM built from the pieces would be a reconstruction
  that could disagree with the filed number.
- *stock* (assets, equity, debt) — instants, from any filing, so the latest
  balance sheet is used as soon as it is public.

**Each metric is a chain of concepts**, tried together. Revenue is the plain
example: filers moved from ``Revenues`` to
``RevenueFromContractWithCustomerExcludingAssessedTax`` with ASC 606 in 2018, and
a single concept would silently end every company's revenue history there.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from shrap.market_data.shares import SOURCE_EDGAR_XBRL

FLOW = "flow"
STOCK = "stock"

# A fiscal year is 52 or 53 weeks: 364 or 371 days, plus slack for how filers
# date period starts. Anything outside this is a quarter, a half or a stub.
ANNUAL_MIN_DAYS = 340
ANNUAL_MAX_DAYS = 390


@dataclass(frozen=True, slots=True)
class Metric:
    name: str
    kind: str
    concepts: tuple[str, ...]


METRICS: dict[str, Metric] = {
    m.name: m
    for m in (
        Metric(
            "revenue",
            FLOW,
            (
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues",
                "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
            ),
        ),
        Metric("gross_profit", FLOW, ("GrossProfit",)),
        Metric("operating_income", FLOW, ("OperatingIncomeLoss",)),
        Metric("net_income", FLOW, ("NetIncomeLoss", "ProfitLoss")),
        Metric(
            "operating_cash_flow",
            FLOW,
            (
                "NetCashProvidedByUsedInOperatingActivities",
                "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            ),
        ),
        Metric("rd_expense", FLOW, ("ResearchAndDevelopmentExpense",)),
        Metric("total_assets", STOCK, ("Assets",)),
        Metric(
            "stockholders_equity",
            STOCK,
            (
                "StockholdersEquity",
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            ),
        ),
        Metric("long_term_debt", STOCK, ("LongTermDebtNoncurrent", "LongTermDebt")),
    )
}


@dataclass(frozen=True, slots=True)
class FundamentalRow:
    """One reported figure, bound for ``market_data.fundamentals``."""

    ticker: str
    cik: str
    metric: str
    concept: str
    period_start: date | None
    period_end: date
    filed_at: date
    """The no-peek boundary: the date the figure became public."""

    value: float
    form: str | None
    accession: str
    source: str = SOURCE_EDGAR_XBRL


def _as_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def parse_fundamentals(
    payload: Mapping[str, object], *, ticker: str, cik: str
) -> list[FundamentalRow]:
    """Every usable figure for every metric in a ``companyfacts`` response."""

    facts = payload.get("facts")
    gaap = facts.get("us-gaap") if isinstance(facts, Mapping) else None
    if not isinstance(gaap, Mapping):
        return []
    rows: list[FundamentalRow] = []
    seen: set[tuple[str, date, str]] = set()
    for metric in METRICS.values():
        for concept in metric.concepts:
            node = gaap.get(concept)
            units = node.get("units") if isinstance(node, Mapping) else None
            entries = units.get("USD") if isinstance(units, Mapping) else None
            if not isinstance(entries, Iterable):
                continue
            for entry in entries:
                row = _row(entry, metric, concept, ticker=ticker, cik=cik)
                if row is None:
                    continue
                # The same fact is often tagged under two concepts of one chain;
                # keep the first concept's, which the chain ranks higher.
                key = (metric.name, row.period_end, row.accession)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
    rows.sort(key=lambda r: (r.metric, r.filed_at, r.period_end))
    return rows


def _row(
    entry: object, metric: Metric, concept: str, *, ticker: str, cik: str
) -> FundamentalRow | None:
    if not isinstance(entry, Mapping):
        return None
    end = _as_date(entry.get("end"))
    filed = _as_date(entry.get("filed"))
    start = _as_date(entry.get("start"))
    value = entry.get("val")
    accession = entry.get("accn")
    if end is None or filed is None or not isinstance(accession, str):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if metric.kind == FLOW:
        if start is None or not ANNUAL_MIN_DAYS <= (end - start).days <= ANNUAL_MAX_DAYS:
            return None
    elif start is not None:
        return None  # a stock metric is an instant; a duration here is mis-tagged
    form = entry.get("form")
    return FundamentalRow(
        ticker=ticker.strip().upper(),
        cik=cik,
        metric=metric.name,
        concept=concept,
        period_start=start,
        period_end=end,
        filed_at=filed,
        value=float(value),
        form=str(form) if isinstance(form, str) else None,
        accession=accession,
    )


Observation = tuple[date, date, float]
"""``(filed_at, period_end, value)`` — the shape the strategy panel consumes."""


def value_as_of(observations: Sequence[Observation], on: date) -> float | None:
    """The latest period the firm could have known about on ``on``.

    Among figures filed by then, the newest period wins; ties on the period go to
    the later filing, which is the restatement.
    """

    best: tuple[date, date, float] | None = None
    for filed, end, value in observations:
        if filed > on:
            continue
        if best is None or (end, filed) > (best[1], best[0]):
            best = (filed, end, value)
    return None if best is None else best[2]


def prior_year_value_as_of(observations: Sequence[Observation], on: date) -> float | None:
    """The figure for the period one year before the latest visible one.

    For growth rates. Chosen by period end within 30 days of a year earlier,
    among figures visible on ``on`` — so a growth rate never mixes a restated
    current year with a prior year only known later.
    """

    visible = [(f, e, v) for f, e, v in observations if f <= on]
    if not visible:
        return None
    latest_end = max(e for _, e, _ in visible)
    candidates = [(e, f, v) for f, e, v in visible if 335 <= (latest_end - e).days <= 395]
    if not candidates:
        return None
    return max(candidates)[2]


__all__ = [
    "ANNUAL_MAX_DAYS",
    "ANNUAL_MIN_DAYS",
    "FLOW",
    "METRICS",
    "STOCK",
    "FundamentalRow",
    "Metric",
    "Observation",
    "parse_fundamentals",
    "prior_year_value_as_of",
    "value_as_of",
]
