"""Tests for the EDGAR document-body backfill (KI-026).

The question this card exists to fix is not "can we fetch a filing" — the Filing
Processor has fetched them for weeks. It is that the world-changer filter has
been shown a file size and an accession number ~3,700 times and asked whether
that is evidence of a world-changing pattern. So the tests below are mostly
about *what reaches the prompt*.
"""

from __future__ import annotations

from typing import Any

from shrap.research.tech_watcher.edgar_text import (
    SELECT_EDGAR_WITHOUT_TEXT_SQL,
    UPDATE_DOCUMENT_TEXT_SQL,
    EdgarTextReport,
    document_body,
    edgar_text_pass,
)
from shrap.research.tech_watcher.filter import DOCUMENT_PROMPT_CHARS, UnfilteredItem, _item_prompt

# A real EDGAR summary, shape-for-shape. This is the string the filter has been
# judging: it states that a document exists and how large it is.
INDEX_METADATA = (
    "<b>Filed:</b> 2026-07-28 <b>AccNo:</b> 0001234567-26-000123 "
    "<b>Size:</b> 565 KB <br>Item 8.01: Other Events"
)

FULL_SUBMISSION = (
    "UNITED STATES SECURITIES AND EXCHANGE COMMISSION FORM 8-K "
    "CURRENT REPORT cover page boilerplate registrant address commission file number "
    "Item 1.01 Entry into a Material Definitive Agreement. "
    "On July 28 2026 the Company entered into a supply agreement to deliver "
    "40 GWh of cells annually beginning 2028. "
    "Item 9.01 Financial Statements and Exhibits. "
    "Exhibit 10.1 filed herewith."
)


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class FakeHttp:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.urls: list[str] = []

    async def get(
        self, url: str, params: Any = None, headers: Any = None, timeout: float = 30.0
    ) -> FakeResponse:
        self.urls.append(url)
        return self._response


class FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        assert sql == SELECT_EDGAR_WITHOUT_TEXT_SQL
        return list(self._rows)

    async def execute(self, sql: str, *args: object) -> object:
        self.executed.append((sql, args))
        return "OK"


class FakeAcquire:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakePool:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.conn = FakeConn(rows)

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)


def _row(item_id: str = "edgar:0001234567-26-000123") -> dict[str, Any]:
    return {
        "item_id": item_id,
        "url": "https://www.sec.gov/Archives/edgar/data/320193/000123456726000123-index.htm",
    }


# --- what reaches the prompt ---------------------------------------------------


def test_the_prompt_carries_the_document_when_one_exists() -> None:
    item = UnfilteredItem(
        item_id="edgar:1",
        source="sec-edgar",
        kind="8-K",
        title="Acme Corp 8-K",
        summary=INDEX_METADATA,
        document_text="Item 1.01: supply agreement to deliver 40 GWh of cells annually",
    )

    prompt = _item_prompt(item)

    assert "40 GWh" in prompt
    # The index metadata is REPLACED, not appended. Adding "Size: 565 KB" to a
    # document adds noise to the only part of the prompt that carries signal.
    assert "565 KB" not in prompt
    assert "AccNo" not in prompt


def test_the_prompt_says_document_not_summary() -> None:
    item = UnfilteredItem(
        item_id="edgar:1",
        source="sec-edgar",
        kind="8-K",
        title="Acme Corp 8-K",
        summary=INDEX_METADATA,
        document_text="Item 1.01: a material definitive agreement",
    )

    prompt = _item_prompt(item)

    # A model told "Summary:" ahead of six thousand characters of filing text is
    # being told something false about what it is reading.
    assert "Document:" in prompt
    assert "Summary:" not in prompt


def test_an_item_without_a_document_is_unchanged() -> None:
    """Every non-EDGAR source and every row predating the backfill."""

    item = UnfilteredItem(
        item_id="arxiv:1",
        source="arxiv",
        kind="paper",
        title="A paper",
        summary="An abstract about a thing.",
    )

    prompt = _item_prompt(item)

    assert "Summary: An abstract about a thing." in prompt
    assert "Document:" not in prompt


def test_the_prompt_bounds_the_document_even_if_the_row_is_larger() -> None:
    # The column is a store other passes may fill with a different budget, so
    # the prompt builder does its own bounding rather than trusting the writer.
    item = UnfilteredItem(
        item_id="edgar:1",
        source="sec-edgar",
        kind="8-K",
        title="Acme Corp 8-K",
        summary=None,
        document_text="x" * (DOCUMENT_PROMPT_CHARS * 3),
    )

    assert len(_item_prompt(item)) < DOCUMENT_PROMPT_CHARS * 2


# --- document_body -------------------------------------------------------------


def test_the_body_keeps_item_sections_and_drops_the_cover_page() -> None:
    body = document_body(FULL_SUBMISSION)

    assert "40 GWh" in body
    assert "Item 1.01" in body
    # The first N characters of a submission are close to the worst N available.
    assert "SECURITIES AND EXCHANGE COMMISSION" not in body


def test_a_filing_with_no_item_codes_falls_back_to_a_leading_slice() -> None:
    # A 10-K or an S-1 has no 8-K items. A truncated document still beats a
    # file size, so this degrades rather than returning nothing.
    text = "ANNUAL REPORT pursuant to section 13. Revenue grew 40% on cell demand."

    assert document_body(text).startswith("ANNUAL REPORT")


def test_the_body_is_truncated_to_the_budget() -> None:
    text = "Item 1.01 Entry into agreement. " + ("detail " * 5000)

    assert len(document_body(text, max_chars=500)) <= 500


# --- the pass ------------------------------------------------------------------


async def test_a_fetched_filing_is_stored() -> None:
    pool = FakePool([_row()])
    http = FakeHttp(FakeResponse(FULL_SUBMISSION))
    from shrap.intelligence.filing_processor.client import EdgarFilingClient

    report = await edgar_text_pass(
        pool,  # type: ignore[arg-type]
        EdgarFilingClient("Shrap Test (test@example.com)"),
        http,  # type: ignore[arg-type]
    )

    assert report.fetched == 1
    assert report.failed == 0
    writes = [args for sql, args in pool.conn.executed if sql == UPDATE_DOCUMENT_TEXT_SQL]
    assert len(writes) == 1
    assert "40 GWh" in str(writes[0][1])


async def test_a_failed_fetch_is_counted_and_leaves_the_row_eligible() -> None:
    """A 429 or a withdrawn filing must not consume the item.

    Leaving `document_text` NULL is what makes the pass resumable: the row is
    simply eligible again next time.
    """

    pool = FakePool([_row()])
    http = FakeHttp(FakeResponse("nope", status_code=429))
    from shrap.intelligence.filing_processor.client import EdgarFilingClient

    report = await edgar_text_pass(
        pool,  # type: ignore[arg-type]
        EdgarFilingClient("Shrap Test (test@example.com)"),
        http,  # type: ignore[arg-type]
    )

    assert report.fetched == 0
    assert report.failed == 1
    assert not [sql for sql, _ in pool.conn.executed if sql == UPDATE_DOCUMENT_TEXT_SQL]


async def test_an_unresolvable_row_is_counted_rather_than_skipped_silently() -> None:
    pool = FakePool([{"item_id": "edgar:x", "url": None}])
    http = FakeHttp(FakeResponse(FULL_SUBMISSION))
    from shrap.intelligence.filing_processor.client import EdgarFilingClient

    report = await edgar_text_pass(
        pool,  # type: ignore[arg-type]
        EdgarFilingClient("Shrap Test (test@example.com)"),
        http,  # type: ignore[arg-type]
    )

    # No CIK means no URL will ever resolve, so it is not retryable — but the
    # total must stay honest rather than the corpus silently shrinking.
    assert report.failed == 1
    assert http.urls == []


async def test_a_dry_run_fetches_nothing_and_claims_nothing() -> None:
    pool = FakePool([_row(), _row("edgar:2")])
    http = FakeHttp(FakeResponse(FULL_SUBMISSION))
    from shrap.intelligence.filing_processor.client import EdgarFilingClient

    report = await edgar_text_pass(
        pool,  # type: ignore[arg-type]
        EdgarFilingClient("Shrap Test (test@example.com)"),
        http,  # type: ignore[arg-type]
        dry_run=True,
    )

    assert report.eligible == 2
    assert http.urls == []
    assert not pool.conn.executed

    rendered = report.render()
    # Third time this shape has been caught here (#183, #187). A dry run must
    # not print counts it never measured in the shape of a result.
    assert "NOT" in rendered
    assert "fetched," not in rendered


def test_the_report_reads_as_a_result_when_it_is_one() -> None:
    assert EdgarTextReport(eligible=10, fetched=8, failed=2, dry_run=False).render() == (
        "EDGAR full text: 8 fetched, 2 failed, of 10 eligible"
    )


# --- service wiring ------------------------------------------------------------


async def test_documents_are_fetched_after_ingest_and_before_the_filter() -> None:
    """The stage order is the fix, not an implementation detail.

    A filing filtered before its body is fetched is judged on an accession
    number and a file size — which is exactly the defect this card ends. Getting
    the order wrong would reintroduce it for every newly ingested item while the
    backfilled ones looked fine.
    """

    import asyncio

    from shrap.research.tech_watcher.service import LLMStages, run_loop

    order: list[str] = []
    stop = asyncio.Event()

    class FakeStore:
        async def upsert_batch(self, *args: Any, **kwargs: Any) -> int:
            return 0

    class FakeRedis:
        async def xadd(self, stream: str, fields: dict[str, str]) -> str:
            return "1-0"

    async def _fetch_documents() -> object:
        order.append("documents")
        return "fetched"

    async def _run_filter() -> object:
        order.append("filter")
        stop.set()  # one iteration is enough
        return []

    async def _run_synthesis() -> object:  # pragma: no cover - not reached
        return None

    async def _synthesis_due() -> bool:
        return False

    await run_loop(
        [],  # no sources: ingest is a no-op that still runs first
        object(),  # type: ignore[arg-type]
        FakeStore(),  # type: ignore[arg-type]
        FakeRedis(),  # type: ignore[arg-type]
        stop=stop,
        interval_seconds=0.01,
        llm_stages=LLMStages(
            run_filter=_run_filter,
            run_synthesis=_run_synthesis,
            synthesis_due=_synthesis_due,
        ),
        fetch_documents=_fetch_documents,
    )

    assert order == ["documents", "filter"]


async def test_a_failing_document_fetch_does_not_stop_the_filter() -> None:
    """SEC being unreachable must not take the funnel down with it."""

    import asyncio

    from shrap.research.tech_watcher.service import LLMStages, run_loop

    order: list[str] = []
    stop = asyncio.Event()

    class FakeStore:
        async def upsert_batch(self, *args: Any, **kwargs: Any) -> int:
            return 0

    class FakeRedis:
        async def xadd(self, stream: str, fields: dict[str, str]) -> str:
            return "1-0"

    async def _fetch_documents() -> object:
        raise RuntimeError("SEC returned 503")

    async def _run_filter() -> object:
        order.append("filter")
        stop.set()
        return []

    async def _run_synthesis() -> object:  # pragma: no cover - not reached
        return None

    async def _synthesis_due() -> bool:
        return False

    await run_loop(
        [],
        object(),  # type: ignore[arg-type]
        FakeStore(),  # type: ignore[arg-type]
        FakeRedis(),  # type: ignore[arg-type]
        stop=stop,
        interval_seconds=0.01,
        llm_stages=LLMStages(
            run_filter=_run_filter,
            run_synthesis=_run_synthesis,
            synthesis_due=_synthesis_due,
        ),
        fetch_documents=_fetch_documents,
    )

    assert order == ["filter"]
