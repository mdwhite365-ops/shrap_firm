"""Tests for EDGAR submissions-API discovery (the historical 8-K door).

The payload fixtures below are trimmed copies of real responses, captured
2026-07-30 from ``data.sec.gov/submissions/CIK0000320193.json`` and
``sec.gov/files/company_tickers.json``. The shape — column-oriented parallel
arrays, older filings sharded behind ``filings.files``, a ticker file keyed by
row index — is the whole risk in this module, so it is pinned against what the
API actually returned rather than what it was assumed to return.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

import pytest

from shrap.intelligence.filing_processor.client import parse_roster
from shrap.intelligence.filing_processor.discovery import (
    DISCOVERY_FEED,
    FORM_8K,
    FORM_8K_AMENDED,
    DiscoveryConfig,
    ShardRef,
    SubmissionFiling,
    default_symbols,
    discover_pass,
    filing_index_url,
    normalize_cik,
    parse_company_tickers,
    parse_date_range,
    parse_filing_arrays,
    parse_submissions,
    resolve_ciks,
    roster_ciks,
    select_filings,
    submissions_url,
    to_pending,
)
from shrap.intelligence.filing_processor.store import PendingFiling

ROSTER = parse_roster("AAPL:320193,NVDA:1045810,TSLA:1318605,LMT:936468")
NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)

# Trimmed from the live AAPL submissions payload (2026-07-30). Column order is
# preserved: index 0 is the 2026-07-30 8-K, index 2 an agent-filed 8-K whose
# accession prefix is NOT the registrant's CIK, index 3 a non-8-K.
AAPL_SUBMISSIONS: dict[str, Any] = {
    "cik": "0000320193",
    "name": "Apple Inc.",
    "tickers": ["AAPL"],
    "filings": {
        "recent": {
            "accessionNumber": [
                "0000320193-26-000018",
                "0000320193-26-000011",
                "0001140361-26-015711",
                "0000320193-26-000007",
            ],
            "form": ["8-K", "8-K", "8-K", "10-Q"],
            "filingDate": ["2026-07-30", "2026-04-30", "2026-04-20", "2026-01-30"],
            "items": ["2.02,9.01", "2.02,9.01", "5.02", ""],
        },
        "files": [
            {
                "name": "CIK0000320193-submissions-001.json",
                "filingCount": 1236,
                "filingFrom": "1994-01-26",
                "filingTo": "2015-05-27",
            }
        ],
    },
}

# Trimmed older shard: bare parallel arrays at top level, no wrapper. The 1996
# row declares a pre-2004 single-digit item code.
AAPL_SHARD: dict[str, Any] = {
    "accessionNumber": ["0001193125-15-186064", "0000320193-96-000014"],
    "form": ["8-K", "8-K"],
    "filingDate": ["2015-05-13", "1996-06-17"],
    "items": ["8.01,9.01", "1"],
}

COMPANY_TICKERS: dict[str, Any] = {
    "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "2": {"cik_str": 1819994, "ticker": "RKLB", "title": "Rocket Lab Corp"},
}


# --- fakes -----------------------------------------------------------------


class FakeSource:
    """Submissions API stand-in that records every call it is asked to make."""

    def __init__(
        self,
        submissions: Mapping[str, Any] | None = None,
        shards: Mapping[str, Any] | None = None,
        tickers: Mapping[str, Any] | None = None,
        fail_ciks: frozenset[str] = frozenset(),
    ) -> None:
        self._submissions = submissions if submissions is not None else AAPL_SUBMISSIONS
        self._shards = (
            shards if shards is not None else {"CIK0000320193-submissions-001.json": AAPL_SHARD}
        )
        self._tickers = tickers if tickers is not None else COMPANY_TICKERS
        self._fail_ciks = fail_ciks
        self.ticker_calls = 0
        self.submission_ciks: list[str] = []
        self.shard_names: list[str] = []

    async def company_tickers(self, http: Any, timeout: float = 30.0) -> dict[str, str]:
        self.ticker_calls += 1
        return parse_company_tickers(self._tickers)

    async def submissions(self, http: Any, cik: str, timeout: float = 30.0) -> Mapping[str, Any]:
        self.submission_ciks.append(cik)
        if cik in self._fail_ciks:
            raise RuntimeError(f"HTTP 500 for {cik}")
        return self._submissions

    async def shard(self, http: Any, name: str, timeout: float = 30.0) -> Mapping[str, Any]:
        self.shard_names.append(name)
        return self._shards[name]


class FakeStore:
    def __init__(self, known: Sequence[str] = ()) -> None:
        self._known = set(known)
        self.recorded: list[tuple[str, list[PendingFiling]]] = []

    async def select_filing_states(self, accessions: Sequence[str]) -> Mapping[str, Any]:
        return {a: object() for a in accessions if a in self._known}

    async def record_and_advance(
        self,
        feed: str,
        pendings: Sequence[PendingFiling],
        last_fetched_at: datetime | None,
        seen: int,
        now: datetime,
    ) -> int:
        assert last_fetched_at is None, "discovery must not claim a poll position"
        self.recorded.append((feed, list(pendings)))
        return sum(1 for p in pendings if p.accession not in self._known)


def _config(**kwargs: Any) -> DiscoveryConfig:
    base: dict[str, Any] = {"throttle_seconds": 0.0}
    base.update(kwargs)
    return DiscoveryConfig(**base)


# --- payload parsing -------------------------------------------------------


def test_parallel_arrays_zip_into_filings() -> None:
    filings = parse_filing_arrays(AAPL_SUBMISSIONS["filings"]["recent"])
    assert [f.accession for f in filings] == [
        "0000320193-26-000018",
        "0000320193-26-000011",
        "0001140361-26-015711",
        "0000320193-26-000007",
    ]
    assert filings[0].form == "8-K"
    assert filings[0].filing_date == date(2026, 7, 30)
    assert filings[0].declared_items == ("2.02", "9.01")
    assert filings[3].declared_items == ()


def test_ragged_arrays_truncate_rather_than_misalign() -> None:
    """A misaligned zip attaches one filing's date to another's accession."""

    filings = parse_filing_arrays(
        {
            "accessionNumber": ["a-1", "a-2", "a-3"],
            "form": ["8-K", "8-K"],
            "filingDate": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "items": ["1.01", "1.01", "1.01"],
        }
    )
    assert [f.accession for f in filings] == ["a-1", "a-2"]


def test_rows_without_an_accession_or_date_are_skipped() -> None:
    filings = parse_filing_arrays(
        {
            "accessionNumber": ["", "a-2", "a-3"],
            "form": ["8-K", "8-K", "8-K"],
            "filingDate": ["2026-01-01", "not-a-date", "2026-01-03"],
            "items": ["", "", ""],
        }
    )
    assert [f.accession for f in filings] == ["a-3"]


def test_missing_columns_yield_nothing_rather_than_raising() -> None:
    assert parse_filing_arrays({"form": ["8-K"]}) == []
    assert parse_filing_arrays({}) == []


def test_submissions_split_into_name_recent_and_shards() -> None:
    name, recent, shards = parse_submissions(AAPL_SUBMISSIONS)
    assert name == "Apple Inc."
    assert len(recent) == 4
    assert [s.name for s in shards] == ["CIK0000320193-submissions-001.json"]
    assert shards[0].filing_from == date(1994, 1, 26)
    assert shards[0].filing_to == date(2015, 5, 27)


def test_submissions_without_a_filings_block_is_survivable() -> None:
    name, recent, shards = parse_submissions({"name": "Nobody Inc."})
    assert (name, recent, shards) == ("Nobody Inc.", [], [])


def test_shard_shape_is_bare_parallel_arrays() -> None:
    """The older-submission shards have no ``filings.recent`` wrapper."""

    filings = parse_filing_arrays(AAPL_SHARD)
    assert [f.accession for f in filings] == [
        "0001193125-15-186064",
        "0000320193-96-000014",
    ]


def test_company_tickers_is_keyed_by_row_index_not_ticker() -> None:
    by_ticker = parse_company_tickers(COMPANY_TICKERS)
    assert by_ticker == {"NVDA": "1045810", "AAPL": "320193", "RKLB": "1819994"}


def test_company_tickers_first_occurrence_wins() -> None:
    by_ticker = parse_company_tickers(
        {
            "0": {"cik_str": 111, "ticker": "DUP", "title": "Bigger"},
            "1": {"cik_str": 222, "ticker": "DUP", "title": "Smaller"},
            "2": {"ticker": "NOCIK"},
        }
    )
    assert by_ticker == {"DUP": "111"}


# --- URLs and identifiers --------------------------------------------------


def test_submissions_url_zero_pads_to_ten_digits() -> None:
    assert submissions_url("320193").endswith("/CIK0000320193.json")
    assert submissions_url(320193).endswith("/CIK0000320193.json")
    assert submissions_url("0000320193").endswith("/CIK0000320193.json")


def test_normalize_cik_strips_padding() -> None:
    assert normalize_cik("0000320193") == "320193"
    assert normalize_cik(936468) == "936468"


def test_index_url_uses_the_registrant_cik_for_an_agent_filed_accession() -> None:
    """Verified live: EDGAR Archives resolves an agent's accession under the
    registrant's CIK path, which is what the fetch stage will ask for."""

    url = filing_index_url("320193", "0001140361-26-015711")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000114036126015711/0001140361-26-015711-index.htm"
    )


# --- selection -------------------------------------------------------------


def _filing(accession: str, form: str, day: str, items: tuple[str, ...] = ()) -> SubmissionFiling:
    return SubmissionFiling(
        accession=accession, form=form, filing_date=date.fromisoformat(day), declared_items=items
    )


def test_selection_filters_form_and_half_open_window() -> None:
    filings = [
        _filing("a", "8-K", "2026-01-01"),
        _filing("b", "10-Q", "2026-01-02"),
        _filing("c", "8-K", "2026-01-31"),
        _filing("d", "8-K", "2025-12-31"),
    ]
    picked = select_filings(filings, (FORM_8K,), date(2026, 1, 1), date(2026, 1, 31))
    assert [f.accession for f in picked] == ["a"]


def test_selection_sorts_oldest_first() -> None:
    filings = [_filing("b", "8-K", "2026-03-01"), _filing("a", "8-K", "2026-01-01")]
    picked = select_filings(filings, (FORM_8K,), date(2026, 1, 1), date(2026, 4, 1))
    assert [f.accession for f in picked] == ["a", "b"]


def test_amendments_are_opt_in() -> None:
    filings = [_filing("a", "8-K", "2026-01-01"), _filing("b", "8-K/A", "2026-01-02")]
    window = (date(2026, 1, 1), date(2026, 2, 1))
    assert [f.accession for f in select_filings(filings, (FORM_8K,), *window)] == ["a"]
    assert [f.accession for f in select_filings(filings, (FORM_8K, FORM_8K_AMENDED), *window)] == [
        "a",
        "b",
    ]


def test_legacy_item_codes_are_flagged_but_empty_items_are_not() -> None:
    assert _filing("a", "8-K", "1996-06-17", ("1",)).declares_legacy_item_codes()
    assert not _filing("b", "8-K", "2026-01-01", ("2.02", "9.01")).declares_legacy_item_codes()
    assert not _filing("c", "8-K", "2026-01-01", ()).declares_legacy_item_codes()


# --- shard windows ---------------------------------------------------------


def test_shard_is_skipped_when_it_predates_the_window() -> None:
    shard = ShardRef("s", date(1994, 1, 26), date(2015, 5, 27))
    assert not shard.intersects(date(2024, 1, 1), date(2026, 1, 1))
    assert shard.intersects(date(2014, 1, 1), date(2026, 1, 1))
    assert not shard.intersects(date(1990, 1, 1), date(1993, 1, 1))


def test_shard_with_unparseable_bounds_is_fetched() -> None:
    """Erring toward one extra request beats silently skipping filings."""

    assert ShardRef("s", None, None).intersects(date(2026, 1, 1), date(2026, 2, 1))


# --- CIK resolution --------------------------------------------------------


async def test_roster_resolves_without_downloading_the_ticker_file() -> None:
    source = FakeSource()
    resolved, unresolved = await resolve_ciks(source, None, ["AAPL", "LMT"], ROSTER)
    assert resolved == {"AAPL": "320193", "LMT": "936468"}
    assert unresolved == ()
    assert source.ticker_calls == 0


async def test_symbols_outside_the_roster_fall_back_to_company_tickers() -> None:
    source = FakeSource()
    resolved, unresolved = await resolve_ciks(source, None, ["AAPL", "RKLB"], ROSTER)
    assert resolved == {"AAPL": "320193", "RKLB": "1819994"}
    assert unresolved == ()
    assert source.ticker_calls == 1


async def test_unresolvable_symbols_are_reported_not_raised() -> None:
    """The eight ETFs in the launch list have no registrant CIK — and file no
    8-Ks either, so a partial resolution is the normal case."""

    source = FakeSource()
    resolved, unresolved = await resolve_ciks(source, None, ["AAPL", "IWM", "XLE"], ROSTER)
    assert set(resolved) == {"AAPL"}
    assert unresolved == ("IWM", "XLE")


def test_roster_inversion_and_default_symbols() -> None:
    assert roster_ciks(ROSTER)["AAPL"] == "320193"
    assert default_symbols(ROSTER) == ("AAPL", "LMT", "NVDA", "TSLA")


# --- pending-row construction ----------------------------------------------


def test_pending_row_carries_live_path_provenance_keys() -> None:
    pending = to_pending(
        _filing("0000320193-26-000018", "8-K", "2026-07-30", ("2.02", "9.01")),
        cik="320193",
        symbol="AAPL",
        company="Apple Inc.",
    )
    assert pending.accession == "0000320193-26-000018"
    assert pending.symbol == "AAPL"
    assert pending.company == "Apple Inc."
    assert pending.filing_date == datetime(2026, 7, 30, tzinfo=UTC)
    assert pending.payload["item_id"] == "edgar:0000320193-26-000018"
    assert pending.payload["url"] == pending.filing_url
    assert pending.payload["declared_items"] == ["2.02", "9.01"]
    assert pending.payload["discovery"] == "sec-submissions-api"


# --- the pass --------------------------------------------------------------


async def test_discovery_queues_pending_rows_under_its_own_feed() -> None:
    store, source = FakeStore(), FakeSource()
    summary = await discover_pass(
        store,
        source,
        None,
        _config(),
        ROSTER,
        symbols=["AAPL"],
        since=date(2026, 1, 1),
        until=date(2026, 8, 1),
        dry_run=False,
        now=NOW,
    )
    feed, pendings = store.recorded[0]
    assert feed == DISCOVERY_FEED
    assert [p.accession for p in pendings] == [
        "0001140361-26-015711",
        "0000320193-26-000011",
        "0000320193-26-000018",
    ]
    assert summary.matched == 3
    assert summary.recorded == 3
    assert summary.unresolved == ()
    # Every queued row is pending-fetch by construction: nothing sets fetched_at.
    assert all(p.filing_url and p.cik == "320193" for p in pendings)


async def test_older_shards_are_fetched_only_when_the_window_reaches_them() -> None:
    store, source = FakeStore(), FakeSource()
    await discover_pass(
        store,
        source,
        None,
        _config(),
        ROSTER,
        symbols=["AAPL"],
        since=date(2026, 1, 1),
        until=date(2026, 8, 1),
        dry_run=False,
        now=NOW,
    )
    assert source.shard_names == []

    store2, source2 = FakeStore(), FakeSource()
    summary = await discover_pass(
        store2,
        source2,
        None,
        _config(),
        ROSTER,
        symbols=["AAPL"],
        since=date(1995, 1, 1),
        until=date(2026, 8, 1),
        dry_run=False,
        now=NOW,
    )
    assert source2.shard_names == ["CIK0000320193-submissions-001.json"]
    assert summary.matched == 5  # 3 recent + 2 from the shard
    assert summary.legacy_item_codes == 1  # the 1996 filing declares item "1"


async def test_dry_run_writes_nothing_and_still_reports_what_is_new() -> None:
    """A dry run reports the gap it found, not an empty store (KI from #161)."""

    store = FakeStore(known=["0000320193-26-000018"])
    summary = await discover_pass(
        store,
        FakeSource(),
        None,
        _config(),
        ROSTER,
        symbols=["AAPL"],
        since=date(2026, 1, 1),
        until=date(2026, 8, 1),
        dry_run=True,
        now=NOW,
    )
    assert store.recorded == []
    assert summary.matched == 3
    assert summary.already_known == 1
    assert summary.recorded == 0
    assert summary.dry_run is True
    assert "dry run — nothing was written" in summary.render()


async def test_rerun_over_an_overlapping_range_records_nothing_new() -> None:
    store = FakeStore(
        known=[
            "0000320193-26-000018",
            "0000320193-26-000011",
            "0001140361-26-015711",
        ]
    )
    summary = await discover_pass(
        store,
        FakeSource(),
        None,
        _config(),
        ROSTER,
        symbols=["AAPL"],
        since=date(2026, 1, 1),
        until=date(2026, 8, 1),
        dry_run=False,
        now=NOW,
    )
    assert summary.matched == 3
    assert summary.already_known == 3
    assert summary.recorded == 0


async def test_one_symbol_failing_does_not_abort_the_pass() -> None:
    source = FakeSource(fail_ciks=frozenset({"1045810"}))
    store = FakeStore()
    summary = await discover_pass(
        store,
        source,
        None,
        _config(),
        ROSTER,
        symbols=["AAPL", "NVDA"],
        since=date(2026, 1, 1),
        until=date(2026, 8, 1),
        dry_run=False,
        now=NOW,
    )
    assert source.submission_ciks == ["320193", "1045810"]
    assert summary.symbols_resolved == 2
    assert summary.matched == 3  # AAPL's, NVDA's request failed


async def test_summary_names_the_symbols_it_could_not_resolve() -> None:
    summary = await discover_pass(
        FakeStore(),
        FakeSource(),
        None,
        _config(),
        ROSTER,
        symbols=["AAPL", "IWM"],
        since=date(2026, 1, 1),
        until=date(2026, 8, 1),
        dry_run=False,
        now=NOW,
    )
    assert summary.unresolved == ("IWM",)
    rendered = summary.render()
    assert "IWM" in rendered
    assert "no CIK" in rendered


def test_render_warns_about_legacy_item_codes() -> None:
    from shrap.intelligence.filing_processor.discovery import DiscoverySummary

    rendered = DiscoverySummary(
        symbols_resolved=1,
        unresolved=(),
        filings_seen=10,
        matched=4,
        already_known=0,
        recorded=4,
        legacy_item_codes=2,
        dry_run=False,
    ).render()
    assert "pre-2004 item" in rendered
    assert "score zero items" in rendered


# --- date range ------------------------------------------------------------


def test_until_is_inclusive_of_its_whole_day() -> None:
    since, until = parse_date_range("2026-01-01", "2026-01-31")
    assert since == date(2026, 1, 1)
    assert until == date(2026, 2, 1)


def test_omitted_until_runs_through_today() -> None:
    since, until = parse_date_range("2026-01-01", None)
    assert since == date(2026, 1, 1)
    assert until > datetime.now(UTC).date()


def test_malformed_date_raises() -> None:
    with pytest.raises(ValueError):
        parse_date_range("01/01/2026", None)
