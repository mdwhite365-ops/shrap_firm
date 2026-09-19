"""Source clients for the Tech Watcher ingest pass.

Five source classes, none requiring credentials:

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
- **Federal Register** documents API filtered to configured agency slugs
  (NRC at launch) — the regulator leg of the 2026-07-18 ruling. The NRC's
  own newsroom RSS sits behind Akamai bot protection that 403s
  non-browser clients (verified 2026-07-19 from www and ww2 hosts), so
  licensing throughput reads the regulator's substantive paper trail —
  license applications/renewals, rules, notices — from the FR API
  instead: open JSON, no key. Item identity is the FR document number.

SAM.gov (solicitations) is spec'd but deferred: it requires an API key.

All sources return the most recent N items; incremental behavior comes from
idempotent upserts keyed on item_id, with the per-source cursor row advanced
in the same transaction (spec: "cursors advanced atomically with the
ingest"). Full-text fetch (filing bodies, paper PDFs) is deferred to the
synthesis slice.
"""

from __future__ import annotations

import asyncio
import html
import json
import random
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import urlparse

import structlog

log = structlog.get_logger(__name__)

SOURCE_EDGAR = "sec-edgar"
SOURCE_ARXIV = "arxiv"
SOURCE_ARXIV_QFIN = "arxiv-qfin"
SOURCE_USASPENDING = "usaspending"
SOURCE_DOE_NEWS = "doe-newsroom"
SOURCE_FED_REGISTER = "federal-register"

# arXiv's quantitative-finance sections, for the Hypothesis Generator's feed.
# Four of the nine, chosen for what the firm can act on:
#
#   q-fin.PM  Portfolio Management — cross-sectional selection, factor results
#   q-fin.ST  Statistical Finance — empirical work on return predictability
#   q-fin.TR  Trading and Market Microstructure — volume, liquidity, flow
#   q-fin.GN  General Finance — anomalies that fit no other section
#
# Left out, deliberately: MF and PR are derivative pricing mathematics, CP is
# numerical method work, RM is risk measurement, and EC is macroeconomics. None
# of the four produces the kind of claim this firm can implement as a ranking
# over 50 equities, so ingesting them would spend filter calls to reject them.
DEFAULT_QFIN_CATEGORIES: tuple[str, ...] = ("q-fin.PM", "q-fin.ST", "q-fin.TR", "q-fin.GN")

EDGAR_CURRENT_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
ARXIV_QUERY_URL = "https://export.arxiv.org/api/query"
USASPENDING_SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
DOE_NEWS_FEED_URL = "https://www.energy.gov/articles/rss.xml"
FED_REGISTER_DOCUMENTS_URL = "https://www.federalregister.gov/api/v1/documents.json"

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


# Statuses worth a second attempt: the server is saying "not now", not "no".
# 4xx is otherwise excluded on purpose — a 400, 401, 403 or 404 will still be
# wrong on the third try, and retrying a 403 against SEC is how a client earns
# a ban.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# **arXiv answers 406 when it means "you are throttled", and it is the edge
# talking, not the API.** Measured on 2026-09-19: empty body, served by arXiv's
# Fastly edge (`via: varnish`, no `server: Google Frontend` header, so the
# origin is never reached), and `x-cache: MISS` on every one.
#
# The behaviour that took longest to see: **a throttled host gets 406 on every
# cache MISS while cache HITS keep returning 200.** That is why this looked
# random for an hour — repeating one query appeared to "work" because Fastly was
# answering it, while any fresh query failed. Ruled out along the way, all by
# experiment: the URL, the user-agent, container-vs-host, the public IP,
# HTTP/1.1-vs-2, sync-vs-async, the category set and the query shape.
#
# **A retry does not rescue a sustained one** — 10 attempts over 60 seconds all
# returned 406, and so did all four header combinations once the host was
# throttled. 406 is listed here for the brief version only. The sustained
# version is answered by not getting throttled (see ARXIV_MIN_INTERVAL_SECONDS)
# and by the per-source freshness target in `shrap.operations.staleness`.
ARXIV_RETRYABLE_STATUSES = RETRYABLE_STATUSES | {406}

DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How hard to try again before giving the source up for this pass.

    Deliberately shallow. The pass runs hourly, so the cost of giving up is one
    hour, and the cost of hammering a source that is rate-limiting us is that it
    keeps rate-limiting us. ``backoff_seconds=0`` disables the wait, which is
    what the tests use.
    """

    attempts: int = DEFAULT_RETRY_ATTEMPTS
    backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS
    statuses: frozenset[int] = RETRYABLE_STATUSES

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("retry attempts must be at least 1")
        if self.backoff_seconds < 0:
            raise ValueError("retry backoff must not be negative")

    def delay_for(self, attempt: int) -> float:
        """Exponential, with jitter so several sources never resynchronise.

        Full jitter rather than a fixed multiple: three sources that failed in
        the same pass would otherwise retry in the same instant, which is the
        pattern a rate limiter is looking for.
        """

        if self.backoff_seconds <= 0:
            return 0.0
        return random.uniform(0.0, self.backoff_seconds * (2 ** (attempt - 1)))


DEFAULT_RETRY = RetryPolicy()
ARXIV_RETRY = RetryPolicy(statuses=ARXIV_RETRYABLE_STATUSES)


# **arXiv's terms of use, which this module was violating on every pass.**
#
#   "make no more than one request every three seconds, and limit requests to
#   a single connection at a time"
#   — https://info.arxiv.org/help/api/tou.html
#
# The ingest pass registers two ArxivSource instances (`arxiv` and
# `arxiv-qfin`) and fetches them back to back in a loop with no delay: two
# requests inside 100ms, twice an hour, for months. That is a documented
# violation, and arXiv's answer to a throttled host is to serve **406 with an
# empty body on every cache miss** — which is exactly the shape of the
# 2026-09-17 outage, and why waiting inside a pass never cleared it.
ARXIV_MIN_INTERVAL_SECONDS = 3.0

# arXiv's edge is reported to refuse requests that do not name a format or a
# client. Both are cheap, both are good practice, and neither can be verified
# from a host that is already throttled — see the runbook.
ARXIV_ACCEPT = "application/atom+xml"
DEFAULT_ARXIV_USER_AGENT = "Shrap Research (mdwhite365@gmail.com)"


class RequestThrottle:
    """A minimum interval between requests, shared across source instances.

    Per process and in-memory, which is the right scope: the Tech Watcher is a
    single long-lived service and it is the only thing here that talks to
    arXiv. A distributed limiter would be machinery for a problem the firm does
    not have.

    The clock is injectable so the tests can assert the wait without taking it.
    """

    def __init__(
        self,
        min_interval_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._min_interval = min_interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None
        # Serialises callers, which is the "single connection at a time" half
        # of the terms. Without it two concurrent sources would both read the
        # same `_last` and both decide they were clear to go.
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """Wait until the next request is allowed. Returns the seconds waited."""

        async with self._lock:
            now = self._clock()
            waited = 0.0
            if self._last is not None:
                remaining = self._min_interval - (now - self._last)
                if remaining > 0:
                    await self._sleep(remaining)
                    waited = remaining
            self._last = self._clock()
            return waited


ARXIV_THROTTLE = RequestThrottle(ARXIV_MIN_INTERVAL_SECONDS)
"""Shared by every :class:`ArxivSource`, because the limit is per *host*.

Giving each source its own throttle would let two instances issue simultaneous
requests and satisfy nothing — which is the bug as it stands.
"""


async def _send_with_retry(
    send: Callable[[], Awaitable[HTTPResponse]],
    *,
    context: str,
    policy: RetryPolicy = DEFAULT_RETRY,
) -> HTTPResponse:
    """Call ``send`` until it returns 200, the status is final, or attempts run out.

    Before this existed every source raised on the first non-200, so a single
    transient blip cost that source a full hour. EDGAR, the DOE newsroom and
    the Federal Register each blipped at least once in the 48 hours to
    2026-09-19; none of those needed to cost an hour.
    """

    last = ""
    for attempt in range(1, policy.attempts + 1):
        response = await send()
        if response.status_code == 200:
            if attempt > 1:
                log.info("tech_watcher.fetch_recovered", source=context, attempt=attempt)
            return response
        last = f"HTTP {response.status_code}"
        final = response.status_code not in policy.statuses
        if final or attempt == policy.attempts:
            raise SourceError(
                f"{context}: {last}"
                + (f" after {attempt} attempts" if attempt > 1 else "")
                + (" (not retryable)" if final and attempt == 1 else "")
            )
        delay = policy.delay_for(attempt)
        log.info(
            "tech_watcher.fetch_retrying",
            source=context,
            attempt=attempt,
            status=response.status_code,
            delay_seconds=round(delay, 2),
        )
        await asyncio.sleep(delay)
    raise SourceError(f"{context}: {last} after {policy.attempts} attempts")  # pragma: no cover


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


def _arxiv_authors(entry: ET.Element) -> list[str]:
    """Author names from an arXiv entry.

    Dropped until 2026-07-30, and the omission was load-bearing downstream: the
    Hypothesis Generator refuses any proposal that cannot name an author and a
    year, and it was being asked to find them in an abstract. On the first live
    run it refused a paper with *"No identifiable authors provided"* — correctly,
    against a prompt that had never been given any.
    """

    names: list[str] = []
    for author in entry.findall(f"{_ATOM_NS}author"):
        name = author.find(f"{_ATOM_NS}name")
        if name is not None and name.text:
            text = " ".join(name.text.split())
            if text and text not in names:
                names.append(text)
    return names


def _parse_feed(xml_text: str, context: str) -> list[ET.Element]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise SourceError(f"{context}: feed is not parseable XML: {e}") from e
    return root.findall(f"{_ATOM_NS}entry")


class EdgarSource:
    """SEC EDGAR current-filings Atom feed, one query per form type."""

    def __init__(
        self,
        user_agent: str,
        forms: tuple[str, ...],
        max_results: int = 100,
        retry: RetryPolicy = DEFAULT_RETRY,
    ) -> None:
        self._headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        self._forms = forms
        self._retry = retry
        self._max_results = max_results

    @property
    def name(self) -> str:
        return SOURCE_EDGAR

    async def fetch(self, http: HTTPClient, timeout: float = 30.0) -> list[RawSourceItem]:
        items: list[RawSourceItem] = []
        seen: set[str] = set()
        for form in self._forms:
            response = await _send_with_retry(
                lambda form=form: http.get(  # type: ignore[misc]
                    EDGAR_CURRENT_URL,
                    params={
                        "action": "getcurrent",
                        "type": form,
                        "count": str(self._max_results),
                        "output": "atom",
                    },
                    headers=self._headers,
                    timeout=timeout,
                ),
                context=f"sec-edgar[{form}]",
                policy=self._retry,
            )
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
    """arXiv API query over a set of categories, newest first.

    Instantiated **twice** in the service, against disjoint category sets and
    under different source names: once for Framework #1 world-changer signal
    (cs.AI, cs.LG, cond-mat, q-bio.NC) and once for the quantitative-finance
    sections that feed the Hypothesis Generator.

    Two instances rather than one widened query, for a reason found by looking
    at the volumes. cs.AI and cs.LG produce several hundred papers a day and
    q-fin produces a few dozen; a single newest-first query capped at
    ``max_results`` would let a busy day in machine learning crowd out the
    finance section entirely, and the leg would report a healthy fetch while
    delivering nothing to the funnel that needed it. Separate sources give each
    its own budget and its own cursor.

    **The source name is part of the item id.** A paper cross-listed in cs.LG
    and q-fin.ST therefore occupies one row per source rather than one row
    total. That is deliberate: the two funnels judge by different bars — the
    world-changer filter asks whether an archetype is playing out in the world,
    the literature filter asks whether a market effect is testable — and a
    cross-listed paper is a legitimate candidate for both. One row would hand
    the paper to whichever leg fetched it first.
    """

    def __init__(
        self,
        categories: tuple[str, ...],
        max_results: int = 100,
        name: str = SOURCE_ARXIV,
        retry: RetryPolicy = ARXIV_RETRY,
        throttle: RequestThrottle = ARXIV_THROTTLE,
        user_agent: str = DEFAULT_ARXIV_USER_AGENT,
    ) -> None:
        self._categories = categories
        self._max_results = max_results
        self._name = name
        self._retry = retry
        self._throttle = throttle
        self._headers = {"User-Agent": user_agent, "Accept": ARXIV_ACCEPT}

    @property
    def name(self) -> str:
        return self._name

    async def fetch(self, http: HTTPClient, timeout: float = 30.0) -> list[RawSourceItem]:
        query = " OR ".join(f"cat:{c}" for c in self._categories)

        async def send() -> HTTPResponse:
            # Inside `send`, so it applies to retries too. A retry that ignored
            # the limit would be the fastest way back into the throttle.
            await self._throttle.acquire()
            return await http.get(
                ARXIV_QUERY_URL,
                params={
                    "search_query": query,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                    "start": "0",
                    "max_results": str(self._max_results),
                },
                headers=self._headers,
                timeout=timeout,
            )

        response = await _send_with_retry(send, context=self._name, policy=self._retry)
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
            item_id=f"{self._name}:{arxiv_id}",
            source=self._name,
            kind=primary_category,
            title=title,
            summary=_text(entry, "summary"),
            url=_link_href(entry) or entry_id,
            external_ts=_parse_ts(published),
            payload={
                "entry_id": entry_id,
                "primary_category": primary_category,
                "authors": _arxiv_authors(entry),
            },
        )


class UsaSpendingSource:
    """USASpending award search: configured agencies, above a dollar floor.

    Returns **newly signed** awards, newest first. Both qualifiers are
    load-bearing and were missing until 2026-07-27: ``time_period`` alone
    matches any transaction activity in the window, and the API's default
    ordering favours the largest awards, so the leg spent its life re-fetching
    the same handful of decades-old national-lab management contracts. They
    deduped to nothing on every pull, which reads as a healthy leg producing no
    new rows — and it hid, among other things, a $900M uranium-enrichment award
    dated 2026-07-06, sitting directly on the promoted fission thesis's
    critical path.

    Trade-off on record: ``new_awards_only`` also excludes large *modifications*
    to existing awards (an option exercise on a live contract can be real
    signal). Base awards are the higher-signal set for a world-changer funnel,
    and the previous behaviour surfaced neither.
    """

    def __init__(
        self,
        agencies: tuple[str, ...],
        min_amount: float = 5_000_000.0,
        lookback_days: int = 30,
        max_results: int = 100,
        retry: RetryPolicy = DEFAULT_RETRY,
    ) -> None:
        self._agencies = agencies
        self._min_amount = min_amount
        self._lookback_days = lookback_days
        self._max_results = min(max_results, 100)  # API page-size cap
        self._retry = retry

    @property
    def name(self) -> str:
        return SOURCE_USASPENDING

    async def fetch(self, http: HTTPClient, timeout: float = 30.0) -> list[RawSourceItem]:
        end = datetime.now(UTC).date()
        start = end - timedelta(days=self._lookback_days)
        body = {
            "filters": {
                "time_period": [
                    {
                        "start_date": start.isoformat(),
                        "end_date": end.isoformat(),
                        # Without this the window matches ANY transaction
                        # activity, so decades-old umbrella contracts qualify on
                        # a routine modification. Verified live 2026-07-27: a
                        # plain 30-day DOE window returned the 1993 Lockheed
                        # ($48B), 2017 Sandia ($42B) and 1999 UT-Battelle ($42B)
                        # national-lab management contracts. New awards only.
                        "date_type": "new_awards_only",
                    }
                ],
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
            # Newest first. The API's default ordering favours the largest
            # awards, and the largest DOE awards are those same legacy lab
            # umbrellas — so page 1 was a fixed set of ancient contracts that
            # deduped to nothing on every pull, and a genuinely new award could
            # never displace them.
            "sort": "Start Date",
            "order": "desc",
            "limit": self._max_results,
            "page": 1,
        }
        response = await _send_with_retry(
            lambda: http.post(
                USASPENDING_SEARCH_URL,
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            ),
            context=SOURCE_USASPENDING,
            policy=self._retry,
        )
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

    def __init__(
        self, feed_url: str = DOE_NEWS_FEED_URL, retry: RetryPolicy = DEFAULT_RETRY
    ) -> None:
        self._feed_url = feed_url
        self._retry = retry

    @property
    def name(self) -> str:
        return SOURCE_DOE_NEWS

    async def fetch(self, http: HTTPClient, timeout: float = 30.0) -> list[RawSourceItem]:
        response = await _send_with_retry(
            lambda: http.get(self._feed_url, params={}, headers={}, timeout=timeout),
            context=SOURCE_DOE_NEWS,
            policy=self._retry,
        )
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


class FederalRegisterSource:
    """Federal Register documents API, one GET per configured agency slug.

    The regulator leg. NRC's own newsroom RSS is Akamai bot-blocked to
    non-browser clients (403, verified 2026-07-19), so licensing throughput
    reads license applications/renewals, rules, and notices from the FR API.
    """

    def __init__(
        self,
        agencies: tuple[str, ...],
        max_results: int = 100,
        retry: RetryPolicy = DEFAULT_RETRY,
    ) -> None:
        self._agencies = agencies
        self._max_results = max_results
        self._retry = retry

    @property
    def name(self) -> str:
        return SOURCE_FED_REGISTER

    async def fetch(self, http: HTTPClient, timeout: float = 30.0) -> list[RawSourceItem]:
        items: list[RawSourceItem] = []
        seen: set[str] = set()
        for agency in self._agencies:
            params = {
                "conditions[agencies][]": agency,
                "order": "newest",
                "per_page": str(min(self._max_results, 100)),
            }
            response = await _send_with_retry(
                lambda params=params: http.get(  # type: ignore[misc]
                    FED_REGISTER_DOCUMENTS_URL, params=params, headers={}, timeout=timeout
                ),
                context=f"federal-register[{agency}]",
                policy=self._retry,
            )
            try:
                data = json.loads(response.text)
            except json.JSONDecodeError as e:
                raise SourceError(f"federal-register[{agency}]: response is not JSON: {e}") from e
            for result in data.get("results") or []:
                item = self._result_to_item(result)
                if item is not None and item.item_id not in seen:
                    seen.add(item.item_id)
                    items.append(item)
        return items

    def _result_to_item(self, result: dict[str, Any]) -> RawSourceItem | None:
        document_number = result.get("document_number")
        title = result.get("title")
        if not document_number or not title:
            return None
        doc_type = result.get("type")
        kind = "-".join(str(doc_type).lower().split()) if doc_type else "document"
        publication_date = result.get("publication_date")
        external_ts: datetime | None = None
        if publication_date:
            try:
                external_ts = datetime.strptime(str(publication_date), "%Y-%m-%d").replace(
                    tzinfo=UTC
                )
            except ValueError:
                external_ts = None
        abstract = result.get("abstract")
        agency_slugs = [
            a["slug"]
            for a in (result.get("agencies") or [])
            if isinstance(a, dict) and a.get("slug")
        ]
        return RawSourceItem(
            item_id=f"fedreg:{document_number}",
            source=SOURCE_FED_REGISTER,
            kind=kind,
            title=" ".join(str(title).split()),
            summary=" ".join(str(abstract).split()) if abstract else None,
            url=result.get("html_url"),
            external_ts=external_ts,
            payload={
                "document_number": document_number,
                "type": doc_type,
                "publication_date": publication_date,
                "html_url": result.get("html_url"),
                "agencies": agency_slugs,
            },
        )
