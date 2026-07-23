"""EDGAR full-text fetch and 8-K parsing (spec Processing steps 2-4).

The Tech Watcher's ``EdgarSource`` captures the current-filings Atom feed but
never dereferences the filing link. This module is the other shape: given a
Tier 3-matched item it resolves the registrant CIK from the stored Archives
URL, fetches the filing's full submission text under SEC's descriptive
``User-Agent`` convention (mirrored from ``EdgarSource``), and splits it by the
filing's declared 8-K item codes so each item is scored as its own event.

Everything except :meth:`EdgarFilingClient.fetch_filing_text` is a pure
function so tests can target CIK resolution, roster matching, item-code
extraction, and section splitting without a network or DB.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

FILING_SOURCE = "sec-edgar"
FILING_KIND = "8-K"
EDGAR_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

_ITEM_ID_PREFIX = "edgar:"
_CIK_FROM_URL_RE = re.compile(r"/edgar/data/(\d+)/", re.IGNORECASE)
# 8-K item numbers are always <digit>.<two digits> (1.01, 2.02, 5.02, 9.01).
_ITEM_CODE_RE = re.compile(r"item\s+(\d\.\d{2})\b", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


class HTTPResponse(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...


class HTTPClient(Protocol):
    """The slice of httpx.AsyncClient the EDGAR fetch needs."""

    async def get(
        self, url: str, *, params: dict[str, str], headers: dict[str, str], timeout: float
    ) -> HTTPResponse: ...


class FilingFetchError(Exception):
    """A filing full-text fetch returned a non-200 status."""

    def __init__(self, url: str, status_code: int) -> None:
        super().__init__(f"edgar full-text fetch failed: HTTP {status_code} for {url}")
        self.url = url
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class Tier3Roster:
    """CIK → ticker map for the Tier 3 launch names (ADR-0012)."""

    by_cik: Mapping[str, str]

    def ticker_for(self, cik: str | None) -> str | None:
        if cik is None:
            return None
        return self.by_cik.get(_normalize_cik(cik))

    def __len__(self) -> int:
        return len(self.by_cik)


def _normalize_cik(cik: str) -> str:
    """Reduce a CIK to its canonical digits (no leading zeros)."""

    digits = "".join(ch for ch in cik if ch.isdigit())
    stripped = digits.lstrip("0")
    return stripped if stripped else ("0" if digits else "")


def parse_roster(raw: str) -> Tier3Roster:
    """Parse a ``TICKER:CIK,TICKER:CIK`` roster string, keyed by CIK.

    Malformed pairs (no colon, blank side) are skipped rather than raising, so
    a single typo in the env never takes the whole roster down.
    """

    by_cik: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        ticker, _, cik = pair.partition(":")
        ticker = ticker.strip().upper()
        cik_n = _normalize_cik(cik)
        if ticker and cik_n:
            by_cik[cik_n] = ticker
    return Tier3Roster(by_cik=by_cik)


def accession_from_item_id(item_id: str) -> str | None:
    """Extract the EDGAR accession number from a Tech Watcher ``edgar:`` id."""

    if not item_id.startswith(_ITEM_ID_PREFIX):
        return None
    accession = item_id[len(_ITEM_ID_PREFIX) :].strip()
    return accession or None


def parse_cik(url: str | None) -> str | None:
    """Resolve the registrant CIK from an EDGAR Archives index URL."""

    if not url:
        return None
    match = _CIK_FROM_URL_RE.search(url)
    if not match:
        return None
    return _normalize_cik(match.group(1))


def filing_txt_url(cik: str, accession: str) -> str:
    """Build the full-submission text URL for one accession under a CIK."""

    nodashes = accession.replace("-", "")
    return f"{EDGAR_ARCHIVES_BASE}/{cik}/{nodashes}/{accession}.txt"


def derive_company(title: str | None) -> str | None:
    """Pull the registrant name out of an EDGAR item title.

    Titles look like ``8-K - APPLE INC (0000320193) (Filer)``; strip the form
    prefix and the trailing ``(CIK) (Filer)`` marker.
    """

    if not title:
        return None
    text = title
    if " - " in text:
        text = text.split(" - ", 1)[1]
    text = text.split(" (", 1)[0]
    return text.strip() or None


def derive_headline(item_code: str, company: str | None) -> str:
    """Derived signal headline, e.g. ``8-K Item 5.02 — APPLE INC``."""

    return f"8-K Item {item_code} — {company or 'filer'}"


def strip_markup(value: str) -> str:
    """Turn an SGML/HTML full-submission body into readable plain text."""

    stripped = html.unescape(_TAG_RE.sub(" ", value))
    return _WS_RE.sub(" ", stripped)


def extract_item_codes(text: str) -> list[str]:
    """Return the distinct declared 8-K item codes, in first-seen order."""

    seen: list[str] = []
    for match in _ITEM_CODE_RE.finditer(text):
        code = match.group(1)
        if code not in seen:
            seen.append(code)
    return seen


def split_item_sections(text: str) -> dict[str, str]:
    """Split a filing into per-item-code sections for separate scoring.

    Each declared item's section runs from its header to the next item header;
    a code repeated in the filing has its sections concatenated.
    """

    matches = list(_ITEM_CODE_RE.finditer(text))
    if not matches:
        return {}
    sections: dict[str, list[str]] = {}
    for index, match in enumerate(matches):
        code = match.group(1)
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.setdefault(code, []).append(text[start:end].strip())
    return {code: "\n".join(parts).strip() for code, parts in sections.items()}


class EdgarFilingClient:
    """Fetch full 8-K submission text from EDGAR Archives."""

    def __init__(self, user_agent: str) -> None:
        self._headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}

    async def fetch_filing_text(
        self, http: HTTPClient, cik: str, accession: str, timeout: float = 30.0
    ) -> str:
        """Fetch and de-markup one filing's full submission text.

        Raises :class:`FilingFetchError` on any non-200 so the caller can back
        off on 429/403 and retry the item next pass.
        """

        url = filing_txt_url(cik, accession)
        response = await http.get(url, params={}, headers=self._headers, timeout=timeout)
        if response.status_code != 200:
            raise FilingFetchError(url, response.status_code)
        return strip_markup(response.text)


__all__ = [
    "EDGAR_ARCHIVES_BASE",
    "FILING_KIND",
    "FILING_SOURCE",
    "EdgarFilingClient",
    "FilingFetchError",
    "HTTPClient",
    "Tier3Roster",
    "accession_from_item_id",
    "derive_company",
    "derive_headline",
    "extract_item_codes",
    "filing_txt_url",
    "parse_cik",
    "parse_roster",
    "split_item_sections",
    "strip_markup",
]
