"""Shares outstanding, and the look-ahead bias that makes it non-trivial.

Closes the cheapest of KI-035's seven capability gaps: the Hypothesis Generator
refused a paper because the firm had no market capitalisation, and no
`market_cap`, `shares` or `outstanding` column existed anywhere.

Market cap is `shares x price` and the firm already has the price. Everything
difficult here is about *which* share count may be used on a given date.

A count describing 2024-03-31 is typically not filed until early May. Using it
for an April market cap uses information nobody had. That inflates a backtest and
produces a strategy that does not work — the worst failure available, because it
passes every test and loses money. So the rule is **select on `filed_at`, never
on `as_of`**, and these tests exist mostly to hold that line.
"""

from __future__ import annotations

from datetime import date

from shrap.market_data.shares import (
    SharesRow,
    company_concept_url,
    market_cap,
    parse_company_concept,
    shares_as_of,
)


def _row(as_of: str, filed_at: str, shares: float, ticker: str = "AAPL") -> SharesRow:
    return SharesRow(
        ticker=ticker,
        cik="320193",
        as_of=date.fromisoformat(as_of),
        filed_at=date.fromisoformat(filed_at),
        shares=shares,
        unit="shares",
        form="10-Q",
    )


# --- the point-in-time rule ---------------------------------------------------


def test_a_count_is_not_usable_before_it_was_filed() -> None:
    """The defect this module exists to prevent.

    Q1 ends 2024-03-31; the 10-Q reporting it is filed 2024-05-09. On 2024-04-15
    that count did not exist, and a market cap built from it is look-ahead.
    """

    rows = [
        _row("2023-12-31", "2024-02-01", 15_500_000_000.0),
        _row("2024-03-31", "2024-05-09", 15_300_000_000.0),
    ]

    visible = shares_as_of(rows, date(2024, 4, 15))

    assert visible is not None
    assert visible.as_of == date(2023, 12, 31)
    assert visible.shares == 15_500_000_000.0


def test_the_newer_count_becomes_usable_on_its_filing_date() -> None:
    rows = [
        _row("2023-12-31", "2024-02-01", 15_500_000_000.0),
        _row("2024-03-31", "2024-05-09", 15_300_000_000.0),
    ]

    assert shares_as_of(rows, date(2024, 5, 8)).as_of == date(2023, 12, 31)
    assert shares_as_of(rows, date(2024, 5, 9)).as_of == date(2024, 3, 31)


def test_nothing_filed_yet_returns_none_rather_than_the_earliest() -> None:
    """None is not zero and not "use the oldest you have".

    A name that had not reported by the date in question has no market cap that
    day. Substituting any number invents one.
    """

    rows = [_row("2024-03-31", "2024-05-09", 15_300_000_000.0)]

    assert shares_as_of(rows, date(2024, 1, 1)) is None


def test_an_amendment_wins_over_the_original_for_the_same_period() -> None:
    """A restatement filed later supersedes, for dates after it was filed."""

    original = _row("2024-03-31", "2024-05-09", 15_300_000_000.0)
    amended = _row("2024-03-31", "2024-06-01", 15_290_000_000.0)

    assert shares_as_of([original, amended], date(2024, 5, 20)).shares == 15_300_000_000.0
    assert shares_as_of([original, amended], date(2024, 6, 2)).shares == 15_290_000_000.0


def test_a_late_filing_for_an_older_period_does_not_displace_a_newer_one() -> None:
    """Ordering is by period first, then filing — not by filing alone.

    A catch-up filing for an old quarter arriving after a newer quarter's filing
    must not roll the firm's view backwards.
    """

    newer_period = _row("2024-03-31", "2024-05-09", 15_300_000_000.0)
    stale_catchup = _row("2023-09-30", "2024-05-20", 15_800_000_000.0)

    chosen = shares_as_of([newer_period, stale_catchup], date(2024, 6, 1))

    assert chosen.as_of == date(2024, 3, 31)


# --- parsing ------------------------------------------------------------------


def test_it_parses_a_companyconcept_payload() -> None:
    payload = {
        "units": {
            "shares": [
                {"end": "2023-12-31", "val": 15_500_000_000, "filed": "2024-02-01", "form": "10-K"},
                {"end": "2024-03-31", "val": 15_300_000_000, "filed": "2024-05-09", "form": "10-Q"},
            ]
        }
    }

    rows = parse_company_concept(payload, ticker="aapl", cik="320193")

    assert [r.as_of for r in rows] == [date(2023, 12, 31), date(2024, 3, 31)]
    assert rows[0].ticker == "AAPL"
    assert rows[0].form == "10-K"


def test_an_entry_that_cannot_be_placed_in_time_is_dropped() -> None:
    """Worse than absent: a row with no filing date would still be selected.

    `shares_as_of` filters on `filed_at`. An entry defaulted to some filing date
    would pass that filter and silently become a look-ahead value.
    """

    payload = {
        "units": {
            "shares": [
                {"end": "2024-03-31", "val": 15_300_000_000},
                {"val": 15_300_000_000, "filed": "2024-05-09"},
                {"end": "2024-06-30", "val": 15_200_000_000, "filed": "2024-08-01"},
            ]
        }
    }

    rows = parse_company_concept(payload, ticker="AAPL", cik="320193")

    assert len(rows) == 1
    assert rows[0].as_of == date(2024, 6, 30)


def test_nonsense_values_are_dropped() -> None:
    payload = {
        "units": {
            "shares": [
                {"end": "2024-03-31", "val": 0, "filed": "2024-05-09"},
                {"end": "2024-03-31", "val": -5, "filed": "2024-05-09"},
                {"end": "2024-03-31", "val": True, "filed": "2024-05-09"},
                {"end": "2024-03-31", "val": "lots", "filed": "2024-05-09"},
            ]
        }
    }

    assert parse_company_concept(payload, ticker="AAPL", cik="320193") == []


def test_a_malformed_payload_returns_nothing_rather_than_raising() -> None:
    assert parse_company_concept({}, ticker="AAPL", cik="320193") == []
    assert parse_company_concept({"units": None}, ticker="AAPL", cik="320193") == []


def test_rows_come_back_oldest_filing_first() -> None:
    payload = {
        "units": {
            "shares": [
                {"end": "2024-06-30", "val": 2, "filed": "2024-08-01"},
                {"end": "2023-12-31", "val": 1, "filed": "2024-02-01"},
            ]
        }
    }

    rows = parse_company_concept(payload, ticker="AAPL", cik="320193")

    assert [r.filed_at for r in rows] == [date(2024, 2, 1), date(2024, 8, 1)]


# --- url + arithmetic ---------------------------------------------------------


def test_the_url_zero_pads_the_cik() -> None:
    """The API requires ten digits; `normalize_cik` deliberately strips them."""

    assert company_concept_url("320193").endswith(
        "/CIK0000320193/dei/EntityCommonStockSharesOutstanding.json"
    )
    assert company_concept_url("0000320193") == company_concept_url("320193")


def test_market_cap_is_shares_times_close() -> None:
    assert market_cap(_row("2024-03-31", "2024-05-09", 1_000_000.0), 150.0) == 150_000_000.0


def test_a_missing_input_gives_none_not_zero() -> None:
    """Zero is rankable and would sort a name to the bottom of a size screen."""

    assert market_cap(None, 150.0) is None
    assert market_cap(_row("2024-03-31", "2024-05-09", 1_000_000.0), None) is None
    assert market_cap(_row("2024-03-31", "2024-05-09", 1_000_000.0), 0.0) is None
