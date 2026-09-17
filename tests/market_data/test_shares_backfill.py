"""The shares backfill: fetch orchestration, and what it does when things are odd.

One request per registrant for all of history, so the failure modes worth testing
are per-ticker rather than per-period: a name SEC does not map, a name that
returns a non-200, and the politeness delay that keeps fifty names under SEC's
published 10 req/s.
"""

from __future__ import annotations

from typing import Any

import pytest

from shrap.market_data.shares_backfill import (
    DEFAULT_USER_AGENT,
    run_backfill,
)

COMPANY_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL"},
    "1": {"cik_str": 789019, "ticker": "MSFT"},
}

CONCEPT_PAYLOAD = {
    "units": {
        "shares": [
            {"end": "2023-12-31", "val": 15_500_000_000, "filed": "2024-02-01", "form": "10-K"},
            {"end": "2024-03-31", "val": 15_300_000_000, "filed": "2024-05-09", "form": "10-Q"},
        ]
    }
}


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload


class FakeHTTP:
    """Serves company_tickers.json and per-CIK concept payloads."""

    def __init__(self, *, concept_status: dict[str, int] | None = None) -> None:
        self.requests: list[str] = []
        self.concept_status = concept_status or {}

    async def get(
        self, url: str, *, params: dict[str, str], headers: dict[str, str], timeout: float
    ) -> FakeResponse:
        self.requests.append(url)
        assert headers["User-Agent"], "SEC returns 403 without a User-Agent"
        if url.endswith("company_tickers.json"):
            return FakeResponse(COMPANY_TICKERS)
        for cik, status in self.concept_status.items():
            if f"CIK{cik}" in url:
                return FakeResponse({}, status_code=status)
        return FakeResponse(CONCEPT_PAYLOAD)


class FakeStore:
    def __init__(self) -> None:
        self.rows: list[Any] = []

    async def upsert_rows(self, rows: Any) -> int:
        self.rows.extend(rows)
        return len(rows)


async def test_it_fetches_and_stores_every_mapped_ticker() -> None:
    http = FakeHTTP()
    store = FakeStore()

    result = await run_backfill(
        http, store, tickers=["AAPL", "MSFT"], user_agent=DEFAULT_USER_AGENT, delay_seconds=0
    )

    assert result.tickers == 2
    assert result.rows_fetched == 4
    assert result.rows_upserted == 4
    assert len(store.rows) == 4


async def test_one_request_per_registrant_not_per_period() -> None:
    """companyconcept serves all of history in a single response."""

    http = FakeHTTP()

    await run_backfill(
        http, FakeStore(), tickers=["AAPL", "MSFT"], user_agent=DEFAULT_USER_AGENT, delay_seconds=0
    )

    concept_calls = [u for u in http.requests if "companyconcept" in u]
    assert len(concept_calls) == 2


async def test_an_unmapped_ticker_is_named_rather_than_silently_skipped() -> None:
    """ETFs are not XBRL registrants. "SPY has no market cap" should be visible."""

    http = FakeHTTP()

    result = await run_backfill(
        http, FakeStore(), tickers=["AAPL", "SPY"], user_agent=DEFAULT_USER_AGENT, delay_seconds=0
    )

    assert result.unmapped == ("SPY",)
    assert result.rows_fetched == 2
    assert "SPY" in result.summary()


async def test_one_bad_registrant_does_not_abort_the_run() -> None:
    """A delisted or never-XBRL name must not cost the other forty-nine."""

    http = FakeHTTP(concept_status={"0000789019": 404})

    result = await run_backfill(
        http, FakeStore(), tickers=["AAPL", "MSFT"], user_agent=DEFAULT_USER_AGENT, delay_seconds=0
    )

    assert result.tickers == 2
    assert result.rows_fetched == 2


async def test_dry_run_fetches_but_writes_nothing() -> None:
    http = FakeHTTP()
    store = FakeStore()

    result = await run_backfill(
        http,
        store,
        tickers=["AAPL"],
        user_agent=DEFAULT_USER_AGENT,
        delay_seconds=0,
        dry_run=True,
    )

    assert result.rows_fetched == 2
    assert result.rows_upserted == 0
    assert store.rows == []


async def test_it_delays_between_registrants_but_not_after_the_last() -> None:
    """SEC publishes 10 req/s and enforces it; a trailing sleep is pure waste."""

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    import shrap.market_data.shares_backfill as mod

    original = mod.asyncio.sleep
    mod.asyncio.sleep = fake_sleep  # type: ignore[assignment]
    try:
        await run_backfill(
            FakeHTTP(),
            FakeStore(),
            tickers=["AAPL", "MSFT"],
            user_agent=DEFAULT_USER_AGENT,
            delay_seconds=0.2,
        )
    finally:
        mod.asyncio.sleep = original  # type: ignore[assignment]

    assert slept == [0.2]


async def test_a_failed_ticker_map_raises_rather_than_backfilling_nothing() -> None:
    """Zero rows from a broken map looks identical to zero rows from empty data."""

    class BrokenHTTP(FakeHTTP):
        async def get(self, url: str, **kwargs: Any) -> FakeResponse:
            if url.endswith("company_tickers.json"):
                return FakeResponse({}, status_code=503)
            return FakeResponse(CONCEPT_PAYLOAD)

    with pytest.raises(RuntimeError, match="company_tickers"):
        await run_backfill(
            BrokenHTTP(),
            FakeStore(),
            tickers=["AAPL"],
            user_agent=DEFAULT_USER_AGENT,
            delay_seconds=0,
        )


# --- the concept chain, added after the first live run ------------------------

CONCEPT_PAYLOAD_USGAAP = {
    "units": {
        "shares": [
            {"end": "2026-06-30", "val": 12_230_000_000, "filed": "2026-07-23", "form": "10-Q"},
        ]
    }
}


class MultiClassHTTP(FakeHTTP):
    """A registrant that tags shares per class, like GOOGL and META.

    Their `dei` cover-page count does not exist as an undimensioned fact —
    GOOGL's entire `dei` fact set is `EntityPublicFloat` — so the endpoint 404s
    and the aggregate lives under `us-gaap` instead.
    """

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append(url)
        if url.endswith("company_tickers.json"):
            return FakeResponse(COMPANY_TICKERS)
        if "/dei/" in url:
            return FakeResponse({}, status_code=404)
        return FakeResponse(CONCEPT_PAYLOAD_USGAAP)


async def test_a_multiclass_issuer_falls_back_to_us_gaap() -> None:
    """The defect the first live run exposed: 16 of 50 names silently empty."""

    result = await run_backfill(
        MultiClassHTTP(),
        FakeStore(),
        tickers=["AAPL"],
        user_agent=DEFAULT_USER_AGENT,
        delay_seconds=0,
    )

    assert result.rows_fetched == 1
    assert result.empty == ()


async def test_the_dei_concept_is_still_preferred_when_present() -> None:
    """Cover-page count is nearer the filing date; us-gaap is quarter-end."""

    http = FakeHTTP()

    result = await run_backfill(
        http, FakeStore(), tickers=["AAPL"], user_agent=DEFAULT_USER_AGENT, delay_seconds=0
    )

    assert result.rows_fetched == 2
    assert not any("us-gaap" in u for u in http.requests)


async def test_a_ticker_with_no_data_anywhere_is_reported_separately() -> None:
    """`unmapped=8` implied 42 worked. 26 did. That gap is now named.

    A size-ranked factor silently missing GOOGL and META, while the tool reports
    success, is the failure this distinction prevents.
    """

    class EmptyEverywhere(FakeHTTP):
        async def get(self, url: str, **kwargs: Any) -> FakeResponse:
            self.requests.append(url)
            if url.endswith("company_tickers.json"):
                return FakeResponse(COMPANY_TICKERS)
            return FakeResponse({}, status_code=404)

    result = await run_backfill(
        EmptyEverywhere(),
        FakeStore(),
        tickers=["AAPL", "MSFT", "SPY"],
        user_agent=DEFAULT_USER_AGENT,
        delay_seconds=0,
    )

    assert result.unmapped == ("SPY",)
    assert result.empty == ("AAPL", "MSFT")
    summary = result.summary()
    assert "covered=0" in summary
    assert "no_data=AAPL,MSFT" in summary


async def test_the_summary_reports_coverage_not_just_failures() -> None:
    result = await run_backfill(
        FakeHTTP(),
        FakeStore(),
        tickers=["AAPL", "MSFT", "SPY"],
        user_agent=DEFAULT_USER_AGENT,
        delay_seconds=0,
    )

    assert "covered=2" in result.summary()
