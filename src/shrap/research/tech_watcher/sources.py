"""Source clients for the Tech Watcher ingest pass.

Two source classes in this slice, both Atom-feed pulls with no auth:

- **SEC EDGAR** current-filings feed per form type (10-K/10-Q/8-K). SEC
  requires a descriptive ``User-Agent`` with contact info; the value comes
  from settings. Item identity is the accession number.
- **arXiv** API query over the spec's categories (cs.AI, cs.LG, cond-mat,
  q-bio.NC), newest first. Item identity is the arXiv id (with version).

Both feeds return the most recent N items; incremental behavior comes from
idempotent upserts keyed on item_id, with the per-source cursor row advanced
in the same transaction (spec: "cursors advanced atomically with the
ingest"). Full-text fetch (filing bodies, paper PDFs) is deferred to the
synthesis slice.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

SOURCE_EDGAR = "sec-edgar"
SOURCE_ARXIV = "arxiv"

EDGAR_CURRENT_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
ARXIV_QUERY_URL = "https://export.arxiv.org/api/query"

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
