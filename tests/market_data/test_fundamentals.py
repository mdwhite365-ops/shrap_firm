"""SEC XBRL fundamentals: annual flows, instant stocks, and the filing-date boundary."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from shrap.market_data.fundamentals import (
    parse_fundamentals,
    prior_year_value_as_of,
    value_as_of,
)
from shrap.market_data.fundamentals_backfill import run_backfill
from shrap.research.strategy_evaluator.strategy import BarSample, PricePanel


def _fact(val: float, end: str, filed: str, *, start: str | None = None, accn: str = "a1") -> dict:
    fact: dict[str, Any] = {"val": val, "end": end, "filed": filed, "accn": accn, "form": "10-K"}
    if start is not None:
        fact["start"] = start
    return fact


PAYLOAD = {
    "facts": {
        "us-gaap": {
            # Annual, quarterly, and the pre-2018 concept for an earlier year.
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {
                    "USD": [
                        _fact(400.0, "2025-09-27", "2025-10-31", start="2024-09-29", accn="k25"),
                        _fact(100.0, "2025-06-28", "2025-08-01", start="2025-03-30", accn="q3"),
                    ]
                }
            },
            "Revenues": {
                "units": {
                    "USD": [
                        _fact(380.0, "2024-09-28", "2024-11-01", start="2023-10-01", accn="k24"),
                        # The same fiscal year, tagged under the older concept in the
                        # same filing: kept once, from the higher-ranked concept.
                        _fact(400.0, "2025-09-27", "2025-10-31", start="2024-09-29", accn="k25"),
                    ]
                }
            },
            "Assets": {
                "units": {
                    "USD": [
                        _fact(900.0, "2025-06-28", "2025-08-01", accn="q3"),
                        _fact(950.0, "2025-09-27", "2025-10-31", accn="k25"),
                        # A stock metric carrying a duration is mis-tagged: dropped.
                        _fact(1.0, "2025-09-27", "2025-10-31", start="2024-09-29", accn="bad"),
                    ]
                }
            },
            "NetIncomeLoss": {"units": {"USD": [{"val": 5.0, "end": "2025-09-27"}]}},
        }
    }
}


def test_flows_are_annual_only_and_deduped_across_a_concept_chain() -> None:
    rows = parse_fundamentals(PAYLOAD, ticker="aapl", cik="320193")
    revenue = sorted((r.period_end, r.value, r.concept) for r in rows if r.metric == "revenue")

    assert revenue == [
        (date(2024, 9, 28), 380.0, "Revenues"),
        (date(2025, 9, 27), 400.0, "RevenueFromContractWithCustomerExcludingAssessedTax"),
    ]
    assert all(r.ticker == "AAPL" for r in rows)


def test_stocks_are_instants_and_a_fact_without_a_filing_date_is_dropped() -> None:
    rows = parse_fundamentals(PAYLOAD, ticker="AAPL", cik="1")
    assets = sorted(r.value for r in rows if r.metric == "total_assets")
    assert assets == [900.0, 950.0]
    assert not [r for r in rows if r.metric == "net_income"]


def test_a_figure_is_invisible_until_it_is_filed() -> None:
    obs = [
        (date(2024, 11, 1), date(2024, 9, 28), 380.0),
        (date(2025, 10, 31), date(2025, 9, 27), 400.0),
    ]
    # The fiscal year has ended but the 10-K is not public yet.
    assert value_as_of(obs, date(2025, 10, 15)) == 380.0
    assert value_as_of(obs, date(2025, 10, 31)) == 400.0
    assert value_as_of(obs, date(2024, 10, 1)) is None


def test_a_restatement_wins_only_from_its_own_filing_date() -> None:
    obs = [
        (date(2025, 10, 31), date(2025, 9, 27), 400.0),
        (date(2026, 2, 1), date(2025, 9, 27), 390.0),
    ]
    assert value_as_of(obs, date(2025, 12, 1)) == 400.0
    assert value_as_of(obs, date(2026, 2, 1)) == 390.0


def test_prior_year_is_the_period_a_year_before_the_latest_visible() -> None:
    obs = [
        (date(2024, 11, 1), date(2024, 9, 28), 380.0),
        (date(2025, 10, 31), date(2025, 9, 27), 400.0),
    ]
    assert prior_year_value_as_of(obs, date(2025, 11, 1)) == 380.0
    assert prior_year_value_as_of(obs, date(2025, 1, 1)) is None


def test_the_panel_answers_point_in_time() -> None:
    start = date(2025, 10, 28)
    bars = [BarSample(start + timedelta(days=i), 10.0, 10.0, 10.0, 10.0, 1.0) for i in range(6)]
    panel = PricePanel.from_bars(
        {"AAPL": bars},
        fundamentals_by_ticker={
            "AAPL": {"revenue": [(date(2025, 10, 31), date(2025, 9, 27), 400.0)]},
            "NOT_IN_PANEL": {"revenue": [(date(2025, 1, 1), date(2024, 9, 28), 1.0)]},
        },
    )
    assert panel.window(2).fundamental("AAPL", "revenue") is None  # the 30th
    assert panel.window(3).fundamental("AAPL", "revenue") == 400.0  # the 31st
    assert panel.window(3).fundamental("AAPL", "total_assets") is None
    assert "NOT_IN_PANEL" not in panel.fundamentals


class _Resp:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        return self._body


class _Http:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def get(self, url: str, **_: Any) -> _Resp:
        self.urls.append(url)
        if url.endswith("company_tickers.json"):
            return _Resp(
                200,
                {
                    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"},
                    "1": {"cik_str": 1, "ticker": "EMPTY", "title": "Nothing tagged"},
                },
            )
        if "CIK0000320193" in url:
            return _Resp(200, PAYLOAD)
        return _Resp(200, {"facts": {}})


async def test_the_backfill_names_etfs_and_empty_registrants_rather_than_hiding_them() -> None:
    result = await run_backfill(
        _Http(),
        None,
        tickers=["AAPL", "SPY", "EMPTY", "GLD"],
        user_agent="test contact@example.com",
        delay_seconds=0.0,
        dry_run=True,
        funds=frozenset({"GLD"}),
    )
    assert result.unmapped == ("SPY",)
    assert result.empty == ("EMPTY",)
    assert result.funds == ("GLD",)
    assert result.rows_fetched == 4
    assert "1/4 names covered" in result.summary()


def test_funds_include_every_liquid_etf_and_the_crypto_trusts_but_not_miners() -> None:
    from shrap.market_data.fundamentals_backfill import fund_tickers

    funds = fund_tickers()
    assert {"SPY", "GLD", "UUP", "IBIT", "ETHA"} <= funds
    assert not {"MARA", "RIOT", "AAPL"} & funds
