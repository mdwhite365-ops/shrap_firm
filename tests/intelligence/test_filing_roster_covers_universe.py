"""The Filing Processor's roster and the locked universe must not drift apart.

They did, for eight weeks. The roster held four names, `research.universe_tiers`
held fifty, and the Filing Processor dropped every other registrant at
``roster.ticker_for(cik)``. EDGAR ingest was healthy the whole time — roughly a
thousand items a week arriving, ``matched: 0`` on every pass — so the only
outward sign was a filings table that stopped growing, which reads exactly like
a quiet week in the market.

Nothing tested the relationship between the two lists, because they lived in
different packages and each was individually correct. That is the gap these
tests close.
"""

from __future__ import annotations

from shrap.agents.intelligence.filing_processor.config import Settings
from shrap.intelligence.filing_processor.client import parse_roster
from shrap.intelligence.filing_processor.service import match_candidate
from shrap.research.universe_curator.launch_list import (
    LAUNCH_CIKS,
    LAUNCH_CIKS_UNRESOLVED,
    LAUNCH_LIST,
    roster_env_value,
)


def test_default_roster_is_derived_from_the_launch_list() -> None:
    """One list, computed from the other — never two maintained by hand."""

    roster = parse_roster(Settings().roster)

    assert len(roster) == len(LAUNCH_CIKS)
    for ticker, cik in LAUNCH_CIKS.items():
        assert roster.ticker_for(cik) == ticker


def test_every_launch_name_is_either_covered_or_explicitly_unresolved() -> None:
    """No launch name may fall out silently.

    A ticker missing from both sets is the failure this file exists for: it
    would simply never match a filing, with nothing anywhere saying why.
    """

    covered = set(LAUNCH_CIKS) | set(LAUNCH_CIKS_UNRESOLVED)
    missing = {name.ticker for name in LAUNCH_LIST} - covered

    assert not missing, f"launch names neither resolved nor declared unresolved: {sorted(missing)}"


def test_roster_env_value_round_trips() -> None:
    """The string handed to the env must parse back to the same mapping."""

    assert parse_roster(roster_env_value()).by_cik == {
        cik: ticker for ticker, cik in LAUNCH_CIKS.items()
    }


def test_roster_env_value_is_stable() -> None:
    """Sorted, so a deployed container's config does not churn between runs."""

    assert roster_env_value() == roster_env_value()
    tickers = [pair.split(":")[0] for pair in roster_env_value().split(",")]
    assert tickers == sorted(tickers)


class _Candidate:
    """The shape ``match_candidate`` reads off a Tech Watcher row."""

    def __init__(self, item_id: str, url: str) -> None:
        self.item_id = item_id
        self.url = url
        self.title = "Form 8-K - SOME REGISTRANT (0001234567) (Filer)"
        self.filing_date = None
        self.fetched_at = None


def test_a_name_the_old_roster_dropped_now_matches() -> None:
    """The regression, stated as the case that used to fail.

    MSFT is in the locked fifty and was absent from the four-name roster, so a
    Microsoft 8-K was discarded. This is that filing.
    """

    roster = parse_roster(Settings().roster)
    candidate = _Candidate(
        item_id="edgar:0000789019-26-000001",
        url="https://www.sec.gov/Archives/edgar/data/789019/000078901926000001/0000789019-26-000001-index.htm",
    )

    pending = match_candidate(candidate, roster)  # type: ignore[arg-type]

    assert pending is not None
    assert pending.symbol == "MSFT"


def test_a_registrant_outside_the_universe_is_still_dropped() -> None:
    """Widening the roster must not turn it into "accept everything".

    ADR-0012's rule is that tier filters apply where per-name cost is incurred,
    and every admitted filing costs an LLM scoring call.
    """

    roster = parse_roster(Settings().roster)
    candidate = _Candidate(
        item_id="edgar:0001999999-26-000001",
        url="https://www.sec.gov/Archives/edgar/data/1999999/000199999926000001/0001999999-26-000001-index.htm",
    )

    assert match_candidate(candidate, roster) is None  # type: ignore[arg-type]


def test_unresolved_names_have_no_cik_to_match_on() -> None:
    """Stated so that "XLF never matches" reads as intended, not as a bug."""

    roster = parse_roster(Settings().roster)
    for ticker in LAUNCH_CIKS_UNRESOLVED:
        assert ticker not in LAUNCH_CIKS
        assert ticker not in set(roster.by_cik.values())
