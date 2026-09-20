"""Tests for the Tech Watcher ingest slice: sources, store, service pass."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from shrap.research.tech_watcher.service import (
    STREAM_HEALTH_ANOMALY,
    STREAM_INGESTION_HEARTBEAT,
    ingest_pass,
)
from shrap.research.tech_watcher.sources import (
    ARXIV_RETRY,
    ARXIV_THROTTLE,
    DEFAULT_QFIN_CATEGORIES,
    DEFAULT_RETRY,
    ArxivSource,
    DoeNewsroomSource,
    EdgarSource,
    FederalRegisterSource,
    RawSourceItem,
    RequestThrottle,
    RetryPolicy,
    SourceError,
    UsaSpendingSource,
)
from shrap.research.tech_watcher.store import (
    INSERT_RAW_ITEM_SQL,
    UPSERT_CURSOR_SQL,
    PostgresRawItemStore,
)

EDGAR_FEED = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Latest Filings</title>
<entry>
<title>8-K - ACME CORP (0000123456) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/acme-index.htm"/>
<summary type="html">Filed: 2026-07-17</summary>
<updated>2026-07-17T16:05:10-04:00</updated>
<id>urn:tag:sec.gov,2008:accession-number=0000123456-26-000042</id>
</entry>
</feed>
"""

ARXIV_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry>
<id>http://arxiv.org/abs/2607.01234v1</id>
<title>Scaling Laws for  Photonic
 Interconnects</title>
<summary>We study photonic interconnect scaling.</summary>
<published>2026-07-16T17:00:00Z</published>
<link href="http://arxiv.org/abs/2607.01234v1" rel="alternate" type="text/html"/>
<arxiv:primary_category term="cs.LG"/>
</entry>
</feed>
"""


USASPENDING_JSON = """{
  "results": [
    {
      "Award ID": "DENE0009234",
      "Recipient Name": "VALAR ATOMICS, INC.",
      "Award Amount": 28000000.0,
      "Description": "REACTOR PILOT PROGRAM - ADVANCED REACTOR DEMONSTRATION",
      "Start Date": "2026-06-01",
      "Awarding Agency": "Department of Energy",
      "generated_internal_id": "CONT_AWD_DENE0009234_8900"
    },
    {
      "Award ID": "IGNORED",
      "Recipient Name": null,
      "generated_internal_id": "CONT_AWD_NO_RECIPIENT"
    }
  ]
}"""

DOE_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xml:base="https://www.energy.gov/">
  <channel>
    <title>Energy News</title>
    <item>
      <title>DOE  Celebrates Second Advanced Reactor
 Achieving Criticality</title>
      <link>https://www.energy.gov/articles/doe-celebrates-second-advanced-reactor</link>
      <description>&lt;p&gt;The Department of Energy announced
&lt;b&gt;criticality&lt;/b&gt; today.&lt;/p&gt;</description>
      <pubDate>Thu, 18 Jun 2026 09:30:00 -0400</pubDate>
    </item>
    <item>
      <title>No link item</title>
    </item>
  </channel>
</rss>
"""


class FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class FakeHTTP:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = responses
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.post_bodies: list[dict[str, Any]] = []

    async def get(
        self, url: str, *, params: dict[str, str], headers: dict[str, str], timeout: float
    ) -> FakeResponse:
        self.requests.append((url, params))
        return self._responses[len(self.requests) + len(self.post_bodies) - 1]

    async def post(
        self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> FakeResponse:
        self.post_bodies.append(json)
        return self._responses[len(self.requests) + len(self.post_bodies) - 1]


@pytest.fixture(autouse=True)
def _no_real_arxiv_sleeps() -> Any:
    """Neutralise the shared arXiv throttle for tests that are not testing it.

    `ARXIV_THROTTLE` is module-level and shared on purpose — the rate limit is
    per host, so per-instance throttles would satisfy nothing. That makes it
    shared state across tests, and at its real 3-second interval it turned this
    file from 0.1s into 15s. The interval is neutered rather than the object
    replaced, so the identity assertions still mean what they say.
    """

    original = ARXIV_THROTTLE._min_interval
    ARXIV_THROTTLE._min_interval = 0.0
    ARXIV_THROTTLE._last = None
    yield
    ARXIV_THROTTLE._min_interval = original
    ARXIV_THROTTLE._last = None


# --- sources -------------------------------------------------------------------


async def test_edgar_parses_entry_and_extracts_accession() -> None:
    http = FakeHTTP([FakeResponse(200, EDGAR_FEED)])
    source = EdgarSource(user_agent="test-agent (test@example.com)", forms=("8-K",))

    items = await source.fetch(http)

    assert len(items) == 1
    item = items[0]
    assert item.item_id == "edgar:0000123456-26-000042"
    assert item.source == "sec-edgar"
    assert item.kind == "8-K"
    assert item.title.startswith("8-K - ACME CORP")
    assert item.url == "https://www.sec.gov/Archives/acme-index.htm"
    assert item.external_ts is not None and item.external_ts.year == 2026
    _url, params = http.requests[0]
    assert params["type"] == "8-K"
    assert params["output"] == "atom"


async def test_edgar_dedupes_across_form_queries() -> None:
    http = FakeHTTP([FakeResponse(200, EDGAR_FEED), FakeResponse(200, EDGAR_FEED)])
    source = EdgarSource(user_agent="test-agent", forms=("10-K", "8-K"))

    items = await source.fetch(http)

    assert len(http.requests) == 2
    assert len(items) == 1  # same accession from both queries


async def test_edgar_non_200_raises_source_error() -> None:
    http = FakeHTTP([FakeResponse(403, "forbidden")])
    source = EdgarSource(user_agent="test-agent", forms=("8-K",))

    with pytest.raises(SourceError, match="403"):
        await source.fetch(http)


async def test_arxiv_parses_entry_with_category_and_whitespace_normalized() -> None:
    http = FakeHTTP([FakeResponse(200, ARXIV_FEED), FakeResponse(200, ARXIV_FEED)])
    source = ArxivSource(categories=("cs.AI", "cs.LG"))

    items = await source.fetch(http)

    # One request per category, and the same paper from both is one item.
    assert [p["search_query"] for _u, p in http.requests] == ["cat:cs.AI", "cat:cs.LG"]
    assert len(items) == 1
    item = items[0]
    assert item.item_id == "arxiv:2607.01234v1"
    assert item.kind == "cs.LG"
    assert item.title == "Scaling Laws for Photonic Interconnects"
    assert item.summary == "We study photonic interconnect scaling."


async def test_arxiv_garbage_body_raises_source_error() -> None:
    http = FakeHTTP([FakeResponse(200, "not xml at all")])
    source = ArxivSource(categories=("cs.AI",))

    with pytest.raises(SourceError, match="not parseable"):
        await source.fetch(http)


async def test_usaspending_parses_award_and_skips_recipientless_row() -> None:
    http = FakeHTTP([FakeResponse(200, USASPENDING_JSON)])
    source = UsaSpendingSource(
        agencies=("Department of Energy",), min_amount=5_000_000.0, lookback_days=30
    )

    items = await source.fetch(http)

    assert len(items) == 1
    item = items[0]
    assert item.item_id == "usaspending:CONT_AWD_DENE0009234_8900"
    assert item.source == "usaspending"
    assert item.kind == "award"
    assert item.title == "Department of Energy award to VALAR ATOMICS, INC. ($28,000,000)"
    assert item.summary == "REACTOR PILOT PROGRAM - ADVANCED REACTOR DEMONSTRATION"
    assert item.url == "https://www.usaspending.gov/award/CONT_AWD_DENE0009234_8900"
    assert item.external_ts is not None and item.external_ts.year == 2026
    body = http.post_bodies[0]
    assert body["filters"]["award_amounts"] == [{"lower_bound": 5_000_000.0}]
    assert body["filters"]["agencies"][0]["name"] == "Department of Energy"


async def test_usaspending_non_200_raises_source_error_after_retrying() -> None:
    """A 500 is retried, then given up on — and the error says how many tries."""

    http = FakeHTTP([FakeResponse(500, "oops")] * 3)
    source = UsaSpendingSource(
        agencies=("Department of Energy",), retry=RetryPolicy(attempts=3, backoff_seconds=0)
    )

    with pytest.raises(SourceError, match="500 after 3 attempts"):
        await source.fetch(http)
    assert len(http.post_bodies) == 3


async def test_usaspending_garbage_json_raises_source_error() -> None:
    http = FakeHTTP([FakeResponse(200, "not json")])
    source = UsaSpendingSource(agencies=("Department of Energy",))

    with pytest.raises(SourceError, match="not parseable"):
        await source.fetch(http)


async def test_doe_newsroom_parses_item_and_strips_html() -> None:
    http = FakeHTTP([FakeResponse(200, DOE_RSS)])
    source = DoeNewsroomSource()

    items = await source.fetch(http)

    assert len(items) == 1  # the link-less item is dropped
    item = items[0]
    assert item.item_id == "doe-news:/articles/doe-celebrates-second-advanced-reactor"
    assert item.source == "doe-newsroom"
    assert item.kind == "article"
    assert item.title == "DOE Celebrates Second Advanced Reactor Achieving Criticality"
    assert item.summary == "The Department of Energy announced criticality today."
    assert item.external_ts is not None and item.external_ts.month == 6


async def test_doe_newsroom_garbage_body_raises_source_error() -> None:
    http = FakeHTTP([FakeResponse(200, "<<<not xml")])
    source = DoeNewsroomSource()

    with pytest.raises(SourceError, match="not parseable"):
        await source.fetch(http)


FEDREG_JSON = """{
  "count": 2,
  "results": [
    {
      "document_number": "2026-14565",
      "title": "Constellation Energy Generation, LLC;\\n  R.E. Ginna Subsequent License Renewal",
      "type": "Notice",
      "abstract": "The NRC is considering an application for subsequent license renewal.",
      "publication_date": "2026-07-20",
      "html_url": "https://www.federalregister.gov/documents/2026/07/20/2026-14565/constellation",
      "agencies": [{"slug": "nuclear-regulatory-commission"}]
    },
    {
      "title": "No document number, dropped",
      "type": "Notice"
    }
  ]
}"""


async def test_federal_register_parses_document() -> None:
    http = FakeHTTP([FakeResponse(200, FEDREG_JSON)])
    source = FederalRegisterSource(agencies=("nuclear-regulatory-commission",), max_results=50)

    items = await source.fetch(http)

    assert len(items) == 1  # the number-less result is dropped
    item = items[0]
    assert item.item_id == "fedreg:2026-14565"
    assert item.source == "federal-register"
    assert item.kind == "notice"
    assert item.title == (
        "Constellation Energy Generation, LLC; R.E. Ginna Subsequent License Renewal"
    )
    assert item.summary is not None and item.summary.startswith("The NRC is considering")
    assert item.url is not None and item.url.endswith("2026-14565/constellation")
    assert item.external_ts == datetime(2026, 7, 20, tzinfo=UTC)
    assert item.payload["agencies"] == ["nuclear-regulatory-commission"]
    _url, params = http.requests[0]
    assert params["conditions[agencies][]"] == "nuclear-regulatory-commission"
    assert params["per_page"] == "50"


async def test_federal_register_dedupes_across_agency_queries() -> None:
    http = FakeHTTP([FakeResponse(200, FEDREG_JSON), FakeResponse(200, FEDREG_JSON)])
    source = FederalRegisterSource(
        agencies=("nuclear-regulatory-commission", "department-of-energy")
    )

    items = await source.fetch(http)

    assert len(http.requests) == 2
    assert len(items) == 1  # same document number from both queries


async def test_federal_register_non_200_raises_source_error_after_retrying() -> None:
    http = FakeHTTP([FakeResponse(503, "unavailable")] * 3)
    source = FederalRegisterSource(
        agencies=("nuclear-regulatory-commission",),
        retry=RetryPolicy(attempts=3, backoff_seconds=0),
    )

    with pytest.raises(SourceError, match="503 after 3 attempts"):
        await source.fetch(http)
    assert len(http.requests) == 3


async def test_federal_register_garbage_body_raises_source_error() -> None:
    http = FakeHTTP([FakeResponse(200, "<<<not json")])
    source = FederalRegisterSource(agencies=("nuclear-regulatory-commission",))

    with pytest.raises(SourceError, match="not JSON"):
        await source.fetch(http)


# --- store ---------------------------------------------------------------------


class FakeTransaction:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> object:
        self._conn.transactions_entered += 1
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.insert_results: list[str] = []
        self.transactions_entered = 0

    async def execute(self, sql: str, *args: object) -> object:
        self.executed.append((sql, args))
        if sql == INSERT_RAW_ITEM_SQL and self.insert_results:
            return self.insert_results.pop(0)
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> dict[str, Any] | None:
        return None

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        return []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)


class FakeAcquire:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakePool:
    def __init__(self) -> None:
        self.conn = FakeConn()

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)


def _item(item_id: str, ts: datetime | None) -> RawSourceItem:
    return RawSourceItem(
        item_id=item_id,
        source="sec-edgar",
        kind="8-K",
        title="t",
        summary=None,
        url=None,
        external_ts=ts,
        payload={"k": "v"},
    )


async def test_upsert_batch_counts_new_rows_and_advances_cursor_atomically() -> None:
    pool = FakePool()
    pool.conn.insert_results = ["INSERT 0 1", "INSERT 0 0", "INSERT 0 1"]
    store = PostgresRawItemStore(pool)  # type: ignore[arg-type]
    fetched_at = datetime(2026, 7, 17, 21, 0, tzinfo=UTC)
    older = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    newer = datetime(2026, 7, 17, 20, 0, tzinfo=UTC)

    inserted = await store.upsert_batch(
        "sec-edgar",
        [_item("edgar:a", newer), _item("edgar:b", None), _item("edgar:c", older)],
        fetched_at,
    )

    assert inserted == 2  # the duplicate counted zero
    assert pool.conn.transactions_entered == 1
    cursor_calls = [args for sql, args in pool.conn.executed if sql == UPSERT_CURSOR_SQL]
    assert len(cursor_calls) == 1
    source, newest_ts, last_item_id, items_seen, updated_at = cursor_calls[0]
    assert source == "sec-edgar"
    assert newest_ts == newer  # max external_ts among genuinely inserted rows
    assert last_item_id == "edgar:c"
    assert items_seen == 2
    assert updated_at == fetched_at


async def test_upsert_batch_all_duplicates_still_touches_cursor_with_zero() -> None:
    pool = FakePool()
    pool.conn.insert_results = ["INSERT 0 0"]
    store = PostgresRawItemStore(pool)  # type: ignore[arg-type]

    inserted = await store.upsert_batch(
        "arxiv", [_item("arxiv:x", None)], datetime(2026, 7, 17, tzinfo=UTC)
    )

    assert inserted == 0
    cursor_calls = [args for sql, args in pool.conn.executed if sql == UPSERT_CURSOR_SQL]
    assert cursor_calls[0][3] == 0


# --- service pass --------------------------------------------------------------


class FakeSource:
    def __init__(self, name: str, items: list[RawSourceItem] | None, error: bool = False) -> None:
        self._name = name
        self._items = items or []
        self._error = error

    @property
    def name(self) -> str:
        return self._name

    async def fetch(self, http: object, timeout: float = 30.0) -> list[RawSourceItem]:
        if self._error:
            raise SourceError(f"{self._name} unavailable")
        return self._items


class FakeStore:
    def __init__(self) -> None:
        self.batches: list[tuple[str, int]] = []

    async def upsert_batch(self, source: str, items: object, fetched_at: object) -> int:
        n = len(items)  # type: ignore[arg-type]
        self.batches.append((source, n))
        return n


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[str] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.published.append(stream)
        return f"{len(self.published)}-0"


async def test_ingest_pass_publishes_heartbeat_per_healthy_source() -> None:
    store = FakeStore()
    redis = FakeRedis()
    sources = [
        FakeSource("sec-edgar", [_item("edgar:a", None)]),
        FakeSource("arxiv", [_item("arxiv:b", None)]),
    ]

    counts = await ingest_pass(sources, http=object(), store=store, redis=redis)  # type: ignore[arg-type]

    assert counts == {"sec-edgar": 1, "arxiv": 1}
    assert redis.published == [STREAM_INGESTION_HEARTBEAT, STREAM_INGESTION_HEARTBEAT]
    assert store.batches == [("sec-edgar", 1), ("arxiv", 1)]


async def test_ingest_pass_isolates_a_failing_source_and_continues() -> None:
    store = FakeStore()
    redis = FakeRedis()
    sources = [
        FakeSource("sec-edgar", None, error=True),
        FakeSource("arxiv", [_item("arxiv:b", None)]),
    ]

    counts = await ingest_pass(sources, http=object(), store=store, redis=redis)  # type: ignore[arg-type]

    assert counts == {"arxiv": 1}  # edgar absent, not zero — it failed, not empty
    assert redis.published == [STREAM_HEALTH_ANOMALY, STREAM_INGESTION_HEARTBEAT]
    assert store.batches == [("arxiv", 1)]


# --- settings ------------------------------------------------------------------


def test_settings_parse_env_and_split_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    from shrap.agents.research.tech_watcher.config import Settings

    monkeypatch.setenv("TECH_WATCHER_EDGAR_FORMS", "8-K, 10-K")
    monkeypatch.setenv("TECH_WATCHER_INTERVAL_SECONDS", "600")
    monkeypatch.setenv("TECH_WATCHER_USASPENDING_AGENCIES", "Department of Energy, NASA")
    settings = Settings()

    assert settings.edgar_forms_tuple() == ("8-K", "10-K")
    assert settings.arxiv_categories_tuple() == ("cs.AI", "cs.LG", "cond-mat", "q-bio.NC")
    assert settings.usaspending_agencies_tuple() == ("Department of Energy", "NASA")
    assert settings.gov_sources_enabled is True
    assert settings.interval_seconds == 600.0
    assert settings.redacted()["postgres_dsn"] == "***"


async def test_usaspending_requests_new_awards_newest_first() -> None:
    """The 2026-07-27 fix, verified live against the API.

    Without ``new_awards_only`` the window matches any transaction activity, so
    decades-old national-lab umbrella contracts (1993 Lockheed $48B, 1999
    UT-Battelle $42B) qualify on a routine modification; and the API's default
    ordering favours the largest awards, which are exactly those. Page 1 was
    therefore a fixed set of ancient contracts that deduped to nothing on every
    pull — a leg that looks alive while being structurally blind to new awards.
    """

    http = FakeHTTP([FakeResponse(200, USASPENDING_JSON)])
    source = UsaSpendingSource(agencies=("Department of Energy",), lookback_days=30)

    await source.fetch(http)

    body = http.post_bodies[0]
    period = body["filters"]["time_period"][0]
    assert period["date_type"] == "new_awards_only"
    assert body["sort"] == "Start Date"
    assert body["order"] == "desc"
    # The sort field must be one the API is asked to return, or it errors.
    assert "Start Date" in body["fields"]


async def test_usaspending_still_scopes_by_window_agency_and_floor() -> None:
    # The fix must not loosen the existing filters.
    http = FakeHTTP([FakeResponse(200, USASPENDING_JSON)])
    source = UsaSpendingSource(
        agencies=("Department of Energy", "Department of Defense"),
        min_amount=5_000_000.0,
        lookback_days=30,
    )

    await source.fetch(http)

    period = http.post_bodies[0]["filters"]["time_period"][0]
    assert period["start_date"] < period["end_date"]
    assert [a["name"] for a in http.post_bodies[0]["filters"]["agencies"]] == [
        "Department of Energy",
        "Department of Defense",
    ]
    assert http.post_bodies[0]["filters"]["award_amounts"] == [{"lower_bound": 5_000_000.0}]


# ---------------------------------------------------------------------------
# Retry: a transient blip must not cost a source its whole hour
#
# Before 2026-09-19 every source raised on the first non-200, and the ingest
# pass skips a source that raises. One blip therefore cost that source a full
# hour. EDGAR, the DOE newsroom and the Federal Register each blipped at least
# once in the 48 hours to 2026-09-19.
# ---------------------------------------------------------------------------


async def test_arxiv_retries_a_406_and_succeeds() -> None:
    """The measured arXiv failure, and the case this card exists for.

    arXiv's edge answers 406 where it means 429 — verified 2026-09-19, when the
    identical request succeeded 8/8 in one window and failed 6/6 twenty minutes
    later from the same address.
    """

    http = FakeHTTP([FakeResponse(406, ""), FakeResponse(200, ARXIV_FEED)])
    source = ArxivSource(
        categories=("q-fin.PM",),
        # Keep arXiv's own status set; only the sleep is removed.
        retry=RetryPolicy(attempts=3, backoff_seconds=0, statuses=ARXIV_RETRY.statuses),
    )

    items = await source.fetch(http)

    assert len(http.requests) == 2
    assert [i.source for i in items] == ["arxiv"]


def test_arxiv_source_treats_406_as_transient_without_being_told_to() -> None:
    """The wiring, not just the policy: a default ArxivSource must retry a 406.

    Asserted separately because the test above passes a policy in, so it would
    still pass if the constructor's default were the wrong one — which is the
    only way this fix reaches production.
    """

    assert ArxivSource(categories=("q-fin.PM",))._retry is ARXIV_RETRY
    assert DoeNewsroomSource()._retry is DEFAULT_RETRY


async def test_a_406_is_not_retried_for_sources_other_than_arxiv() -> None:
    """406 is only transient because arXiv makes it so; elsewhere it is final.

    Retrying a genuine content-negotiation failure would turn one wrong request
    into three.
    """

    http = FakeHTTP([FakeResponse(406, "")] * 3)
    source = DoeNewsroomSource(retry=RetryPolicy(attempts=3, backoff_seconds=0))

    with pytest.raises(SourceError, match="not retryable"):
        await source.fetch(http)
    assert len(http.requests) == 1


async def test_a_403_is_never_retried() -> None:
    """SEC bans clients that keep knocking after a 403; so do not knock."""

    http = FakeHTTP([FakeResponse(403, "banned")] * 3)
    source = EdgarSource(
        user_agent="Shrap Research (test)",
        forms=("8-K",),
        retry=RetryPolicy(attempts=3, backoff_seconds=0),
    )

    with pytest.raises(SourceError, match="403"):
        await source.fetch(http)
    assert len(http.requests) == 1


async def test_retry_gives_up_rather_than_looping_forever() -> None:
    http = FakeHTTP([FakeResponse(503, "")] * 5)
    source = ArxivSource(categories=("q-fin.PM",), retry=RetryPolicy(attempts=2, backoff_seconds=0))

    with pytest.raises(SourceError, match="after 2 attempts"):
        await source.fetch(http)
    assert len(http.requests) == 2


def test_arxiv_policy_treats_406_as_transient_and_the_default_does_not() -> None:
    assert 406 in ARXIV_RETRY.statuses
    assert 406 not in DEFAULT_RETRY.statuses
    # Both agree on the genuinely transient ones.
    for status in (429, 500, 502, 503, 504):
        assert status in DEFAULT_RETRY.statuses
        assert status in ARXIV_RETRY.statuses


def test_backoff_is_bounded_and_grows() -> None:
    policy = RetryPolicy(attempts=4, backoff_seconds=2.0)
    # Full jitter: each delay is somewhere in [0, base * 2**(n-1)].
    for attempt, ceiling in ((1, 2.0), (2, 4.0), (3, 8.0)):
        for _ in range(50):
            assert 0.0 <= policy.delay_for(attempt) <= ceiling


def test_zero_backoff_never_sleeps() -> None:
    policy = RetryPolicy(attempts=3, backoff_seconds=0)
    assert all(policy.delay_for(n) == 0.0 for n in (1, 2, 3))


def test_a_policy_must_allow_at_least_one_attempt() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        RetryPolicy(attempts=0)


# ---------------------------------------------------------------------------
# arXiv rate limiting
#
# arXiv's terms of use: "make no more than one request every three seconds, and
# limit requests to a single connection at a time."
# The ingest pass registers two ArxivSource instances and fetched them back to
# back with no delay — two requests inside 100ms, twice an hour, for months.
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


async def test_the_first_request_is_not_delayed() -> None:
    clock = _FakeClock()
    throttle = RequestThrottle(3.0, clock=clock, sleep=clock.sleep)

    assert await throttle.acquire() == 0.0
    assert clock.slept == []


async def test_a_second_request_waits_out_the_interval() -> None:
    clock = _FakeClock()
    throttle = RequestThrottle(3.0, clock=clock, sleep=clock.sleep)

    await throttle.acquire()
    waited = await throttle.acquire()

    assert waited == 3.0
    assert clock.slept == [3.0]


async def test_no_wait_when_the_interval_has_already_passed() -> None:
    clock = _FakeClock()
    throttle = RequestThrottle(3.0, clock=clock, sleep=clock.sleep)

    await throttle.acquire()
    clock.now += 10.0
    waited = await throttle.acquire()

    assert waited == 0.0
    assert clock.slept == []


async def test_both_arxiv_sources_share_one_throttle() -> None:
    """The limit is per host, so per-instance throttles would satisfy nothing.

    This is the actual defect: `arxiv` and `arxiv-qfin` are two instances that
    hit one API back to back in the same pass.
    """

    assert ArxivSource(categories=("cs.AI",))._throttle is ARXIV_THROTTLE
    assert ArxivSource(categories=DEFAULT_QFIN_CATEGORIES)._throttle is ARXIV_THROTTLE


async def test_the_throttle_is_applied_before_each_arxiv_request() -> None:
    clock = _FakeClock()
    throttle = RequestThrottle(3.0, clock=clock, sleep=clock.sleep)
    http = FakeHTTP([FakeResponse(200, ARXIV_FEED), FakeResponse(200, ARXIV_FEED)])
    first = ArxivSource(categories=("cs.AI",), throttle=throttle)
    second = ArxivSource(categories=("q-fin.PM",), throttle=throttle)

    await first.fetch(http)
    await second.fetch(http)

    assert clock.slept == [3.0]


async def test_a_retry_also_waits_rather_than_hammering() -> None:
    """A retry that ignored the limit is the fastest way back into a throttle."""

    clock = _FakeClock()
    throttle = RequestThrottle(3.0, clock=clock, sleep=clock.sleep)
    http = FakeHTTP([FakeResponse(406, ""), FakeResponse(200, ARXIV_FEED)])
    source = ArxivSource(
        categories=("cs.AI",),
        throttle=throttle,
        retry=RetryPolicy(attempts=3, backoff_seconds=0, statuses=ARXIV_RETRY.statuses),
    )

    await source.fetch(http)

    assert len(http.requests) == 2
    assert clock.slept == [3.0]


async def test_arxiv_names_a_format_and_a_client() -> None:
    """Both are cheap, both are asked for, and neither was being sent."""

    source = ArxivSource(categories=("cs.AI",))

    assert source._headers["Accept"] == "application/atom+xml"
    assert "@" in source._headers["User-Agent"]


# ---------------------------------------------------------------------------
# One refused arXiv category must not cost the whole feed
#
# Measured 2026-09-19, three clean rounds 8s apart, deterministic every time:
#
#     cs.AI               200
#     cs.LG               200
#     q-bio.NC            406
#     cond-mat            406
#     cond-mat.stat-mech  406
#
# The `arxiv` source asked for cs.AI OR cs.LG OR cond-mat OR q-bio.NC in one
# query, so two refused categories took the two healthy ones down with them —
# 46 consecutive passes ingesting nothing from a feed that was half fine.
# ---------------------------------------------------------------------------


async def test_a_refused_category_does_not_lose_the_healthy_ones() -> None:
    """The live failure: cs.AI and cs.LG work, cond-mat and q-bio.NC 406."""

    http = FakeHTTP(
        [
            FakeResponse(200, ARXIV_FEED),  # cs.AI
            FakeResponse(200, ARXIV_FEED),  # cs.LG
            FakeResponse(406, ""),  # cond-mat
            FakeResponse(406, ""),  # q-bio.NC
        ]
    )
    source = ArxivSource(
        categories=("cs.AI", "cs.LG", "cond-mat", "q-bio.NC"),
        retry=RetryPolicy(attempts=1, backoff_seconds=0, statuses=ARXIV_RETRY.statuses),
    )

    items = await source.fetch(http)

    assert len(http.requests) == 4
    assert len(items) == 1  # the healthy categories still produced their paper


async def test_every_category_failing_is_still_a_source_failure() -> None:
    """A wholly dead feed must still raise, or the cursor advances over nothing."""

    http = FakeHTTP([FakeResponse(406, "")] * 2)
    source = ArxivSource(
        categories=("cond-mat", "q-bio.NC"),
        retry=RetryPolicy(attempts=1, backoff_seconds=0, statuses=ARXIV_RETRY.statuses),
    )

    with pytest.raises(SourceError, match="every category failed"):
        await source.fetch(http)


async def test_items_are_deduped_across_categories() -> None:
    """A paper cross-listed in cs.AI and cs.LG is one row, not two."""

    http = FakeHTTP([FakeResponse(200, ARXIV_FEED)] * 3)
    source = ArxivSource(categories=("cs.AI", "cs.LG", "q-bio.NC"))

    items = await source.fetch(http)

    assert len(http.requests) == 3
    assert len(items) == 1
