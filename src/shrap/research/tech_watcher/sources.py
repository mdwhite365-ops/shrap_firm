"""Source clients for the Tech Watcher ingest pass.

Four source classes, none requiring credentials:

- **SEC EDGAR** current-filings feed per form type (10-K/10-Q/8-K). SEC
  requires a descriptive ``User-Agent`` with contact info; the value comes
  from settings. Item identity is the accession number.
- **arXiv** API query over the spec's categories (cs.AI, cs.LG, cond-mat,
  q-bio.NC), newest first. Item identity is the arXiv id (with version).
- **USASpending** award search (POST JSON API) filtered to configured
  awarding agencies above a dollar threshold, over a lookback window.
  Item identity is the award's ``generated_internal_id``. This is the
  gov-sources card (2026-07-18 ruling): program awards are the primary
  paper trail for private-company signals (the Valar Atomics case).
- **DOE newsroom** RSS feed (energy.gov Energy News). Item identity is
  the article link path.

SAM.gov (solicitations) is spec'd but deferred: it requires an API key.

All sources return the most recent N items; incremental behavior comes from
idempotent upserts keyed on item_id, with the per-source cursor row advanced
in the same transaction (spec: "cursors advanced atomically with the
ingest"). Full-text fetch (filing bodies, paper PDFs) is deferred to the
synthesis slice.
"""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import urlparse

SOURCE_EDGAR = "sec-edgar"
SOURCE_ARXIV = "arxiv"
SOURCE_USASPENDING = "usaspending"
SOURCE_DOE_NEWS = "doe-newsroom"

EDGAR_CURRENT_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
ARXIV_QUERY_URL = "https://export.arxiv.org/api/query"
USASPENDING_SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
DOE_NEWS_FEED_URL = "https://www.energy.gov/articles/rss.xml"

# Contract award type codes: definitive contracts + purchase orders + IDVs.
_USASPENDING_AWARD_TYPES = ["A", "B", "C", "D"]

_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_ACCESSION_RE = re.compile(r"accession[-_ ]?number=([0-9-]+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RawSourceItem:
    """One raw ingested item, source-agnostic."""

    item_id: str
    source: str
    kind: str | None
    title: str
    summary: str | None
    url: str | None
    external_ts: datetime | None
    payload: dict[str, Any]


class HTTPResponse(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...


class HTTPClient(Protocol):
    """The slice of httpx.AsyncClient the sources need."""

    async def get(
        self, url: str, *, params: dict[str, str], headers: dict[str, str], timeout: float
    ) -> HTTPResponse: ...

    async def post(
        self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> HTTPResponse: ...


class SourceError(Exception):
    """A source fetch or parse failed; the pass continues with other sources."""


def _text(entry: ET.Element, tag: str) -> str | None:
    node = entry.find(f"{_ATOM_NS}{tag}")
    if node is None or node.text is None:
        return None
    return " ".join(node.text.split()) or None


def _link_href(entry: ET.Element) -> str | None:
    for link in entry.findall(f"{_ATOM_NS}link"):
        href = link.get("href")
        if href and link.get("rel") in (None, "alternate"):
            return href
    return None


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_feed(xml_text: str, context: str) -> list[ET.Element]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise SourceError(f"{context}: feed is not parseable XML: {e}") from e
    return root.findall(f"{_ATOM_NS}entry")


class EdgarSource:
    """SEC EDGAR current-filings Atom feed, one query per form type."""

    def __init__(self, user_agent: str, forms: tuple[str, ...], max_results: int = 100) -> None:
        self._headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        self._forms = forms
        self._max_results = max_results

    @property
    def name(self) -> str:
        return SOURCE_EDGAR

    async def fetch(self, http: HTTPClient, timeout: float = 30.0) -> list[RawSourceItem]:
        items: list[RawSourceItem] = []
        seen: set[str] = set()
        for form in self._forms:
            response = await http.get(
                EDGAR_CURRENT_URL,
                params={
                    "action": "getcurrent",
                    "type": form,
                    "count": str(self._max_results),
                    "output": "atom",
                },
                headers=self._headers,
                timeout=timeout,
            )
            if response.status_code != 200:
                raise SourceError(f"sec-edgar: HTTP {response.status_code} for form {form}")
            for entry in _parse_feed(response.text, f"sec-edgar {form}"):
                item = self._entry_to_item(entry, form)
                if item is not None and item.item_id not in seen:
                    seen.add(item.item_id)
                    items.append(item)
        return items

    def _entry_to_item(self, entry: ET.Element, form: str) -> RawSourceItem | None:
        entry_id = _text(entry, "id")
        title = _text(entry, "title")
        if not entry_id or not title:
            return None
        match = _ACCESSION_RE.search(entry_id)
        accession = match.group(1) if match else entry_id
        updated = _text(entry, "updated")
        return RawSourceItem(
            item_id=f"edgar:{accession}",
            source=SOURCE_EDGAR,
            kind=form,
            title=title,
            summary=_text(entry, "summary"),
            url=_link_href(entry),
            external_ts=_parse_ts(updated),
            payload={"entry_id": entry_id, "form": form, "updated": updated},
        )


class ArxivSource:
    """arXiv API query over the spec's categories, newest first."""

    def __init__(self, categories: tuple[str, ...], max_results: int = 100) -> None:
        self._categories = categories
        self._max_results = max_results

    @property
    def name(self) -> str:
        return SOURCE_ARXIV

    async def fetch(self, http: HTTPClient, timeout: float = 30.0) -> list[RawSourceItem]:
        query = " OR ".join(f"cat:{c}" for c in self._categories)
        response = await http.get(
            ARXIV_QUERY_URL,
            params={
                "search_query": query,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "start": "0",
                "max_results": str(self._max_results),
            },
            headers={},
            timeout=timeout,
        )
        if response.status_code != 200:
            raise SourceError(f"arxiv: HTTP {response.status_code}")
        items: list[RawSourceItem] = []
        for entry in _parse_feed(response.text, "arxiv"):
            item = self._entry_to_item(entry)
            if item is not None:
                items.append(item)
        return items

    def _entry_to_item(self, entry: ET.Element) -> RawSourceItem | None:
        entry_id = _text(entry, "id")
        title = _text(entry, "title")
        if not entry_id or not title:
            return None
        arxiv_id = entry_id.rsplit("/", 1)[-1]
        category_node = entry.find("{http://arxiv.org/schemas/atom}primary_category")
        primary_category = category_node.get("term") if category_node is not None else None
        published = _text(entry, "published")
        return RawSourceItem(
            item_id=f"arxiv:{arxiv_id}",
            source=SOURCE_ARXIV,
            kind=primary_category,
            title=title,
            summary=_text(entry, "summary"),
            url=_link_href(entry) or entry_id,
            external_ts=_parse_ts(published),
            payload={"entry_id": entry_id, "primary_category": primary_category},
        )


class UsaSpendingSource:
    """USASpending award search: configured agencies, above a dollar floor."""

    def __init__(
        self,
        agencies: tuple[str, ...],
        min_amount: float = 5_000_000.0,
        lookback_days: int = 30,
        max_results: int = 100,
    ) -> None:
        self._agencies = agencies
        self._min_amount = min_amount
        self._lookback_days = lookback_days
        self._max_results = min(max_results, 100)  # API page-size cap

    @property
    def name(self) -> str:
        return SOURCE_USASPENDING

    async def fetch(self, http: HTTPClient, timeout: float = 30.0) -> list[RawSourceItem]:
        end = datetime.now(UTC).date()
        start = end - timedelta(days=self._lookback_days)
        body = {
            "filters": {
                "time_period": [{"start_date": start.isoformat(), "end_date": end.isoformat()}],
                "award_type_codes": _USASPENDING_AWARD_TYPES,
                "agencies": [
                    {"type": "awarding", "tier": "toptier", "name": agency}
                    for agency in self._agencies
                ],
                "award_amounts": [{"lower_bound": self._min_amount}],
            },
            "fields": [
                "Award ID",
                "Recipient Name",
                "Award Amount",
                "Description",
                "Start Date",
                "Awarding Agency",
                "generated_internal_id",
            ],
            "limit": self._max_results,
            "page": 1,
        }
        response = await http.post(
            USASPENDING_SEARCH_URL,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        if response.status_code != 200:
            raise SourceError(f"usaspending: HTTP {response.status_code}")
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError as e:
            raise SourceError(f"usaspending: response is not parseable JSON: {e}") from e
        results = data.get("results")
        if not isinstance(results, list):
            raise SourceError("usaspending: response has no results list")
        items: list[RawSourceItem] = []
        for result in results:
            item = self._result_to_item(result)
            if item is not None:
                items.append(item)
        return items

    def _result_to_item(self, result: dict[str, Any]) -> RawSourceItem | None:
        award_id = result.get("generated_internal_id")
        recipient = result.get("Recipient Name")
        if not award_id or not recipient:
            return None
        agency = result.get("Awarding Agency") or "unknown agency"
        amount = result.get("Award Amount")
        amount_text = f"${amount:,.0f}" if isinstance(amount, int | float) else "undisclosed"
        description = result.get("Description") or ""
        return RawSourceItem(
            item_id=f"usaspending:{award_id}",
            source=SOURCE_USASPENDING,
            kind="award",
            title=f"{agency} award to {recipient} ({amount_text})",
            summary=" ".join(str(description).split()) or None,
            url=f"https://www.usaspending.gov/award/{award_id}",
            external_ts=_parse_ts(result.get("Start Date")),
            payload={
                "award_id": result.get("Award ID"),
                "recipient": recipient,
                "amount": amount,
                "awarding_agency": agency,
                "start_date": result.get("Start Date"),
            },
        )


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(value: str) -> str | None:
    return " ".join(html.unescape(_TAG_RE.sub(" ", value)).split()) or None


class DoeNewsroomSource:
    """DOE Energy News RSS 2.0 feed (energy.gov redirects to the live feed)."""

    def __init__(self, feed_url: str = DOE_NEWS_FEED_URL) -> None:
        self._feed_url = feed_url

    @property
    def name(self) -> str:
        return SOURCE_DOE_NEWS

    async def fetch(self, http: HTTPClient, timeout: float = 30.0) -> list[RawSourceItem]:
        response = await http.get(self._feed_url, params={}, headers={}, timeout=timeout)
        if response.status_code != 200:
            raise SourceError(f"doe-newsroom: HTTP {response.status_code}")
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as e:
            raise SourceError(f"doe-newsroom: feed is not parseable XML: {e}") from e
        items: list[RawSourceItem] = []
        for entry in root.iter("item"):
            item = self._entry_to_item(entry)
            if item is not None:
                items.append(item)
        return items

    def _entry_to_item(self, entry: ET.Element) -> RawSourceItem | None:
        link = entry.findtext("link")
        title = entry.findtext("title")
        if not link or not title:
            return None
        link = link.strip()
        title = " ".join(title.split())
        pub_date = entry.findtext("pubDate")
        external_ts: datetime | None = None
        if pub_date:
            try:
                external_ts = parsedate_to_datetime(pub_date.strip())
            except (TypeError, ValueError):
                external_ts = None
        description = entry.findtext("description")
        return RawSourceItem(
            item_id=f"doe-news:{urlparse(link).path}",
            source=SOURCE_DOE_NEWS,
            kind="article",
            title=title,
            summary=_strip_html(description) if description else None,
            url=link,
            external_ts=external_ts,
            payload={"link": link, "pub_date": pub_date},
        )
