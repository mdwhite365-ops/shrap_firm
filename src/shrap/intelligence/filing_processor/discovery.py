"""8-K discovery straight from EDGAR, independent of the Tech Watcher's feed.

Everything the Filing Processor has ever scored arrived through one door: the
Tech Watcher's ``getcurrent`` Atom pull, which returns only what EDGAR is
publishing *right now*. Nothing that was filed before the Tech Watcher was
deployed — or during any window in which it was down — can ever reach
``intelligence.filings``, and the existing backfill CLI
(:mod:`shrap.intelligence.filing_processor.backfill`) cannot help: it re-drives
the fetch/score path over rows already in ``research.raw_source_items``, so it
inherits the same horizon.

This module opens the other door. It asks EDGAR directly what a registrant has
filed, over an arbitrary date range, via the submissions API:

- ``https://data.sec.gov/submissions/CIK##########.json`` — the registrant's
  filing history, most recent first. ``filings.recent`` holds roughly the last
  1,000 filings; anything older is sharded into ``filings.files``, each shard
  carrying its own ``[filingFrom, filingTo]`` window so only the shards that
  intersect the requested range are fetched.
- ``https://www.sec.gov/files/company_tickers.json`` — ticker → CIK for every
  registrant, so a symbol outside the four-name Tier 3 roster can be named
  without hand-editing config. Fetched only when the roster cannot resolve
  something.

**Discovery only, by design.** It writes pending rows (``fetched_at IS NULL``)
and stops. The live service's ``fetch_pass`` and ``score_pass`` drain the queue
on their own cadence, under their own EDGAR throttle and their own LLM budget.
Re-implementing those stages here would double the code that can drift and
would burst-hammer both EDGAR and Ollama; reusing them means a backfilled 8-K
is scored by exactly the same path as a live one, which is the only way the two
populations stay comparable.

Two things it is careful about, mirroring the existing backfill:

- **Never perturbs the live poll cursor.** Discovery records under
  :data:`DISCOVERY_FEED`, a cursor row distinct from the service's own feed.
- **Idempotent.** ``INSERT ... ON CONFLICT (accession) DO NOTHING`` — a re-run
  over an overlapping range inserts nothing and says so.

Known limitation, reported rather than hidden: the SEC renumbered 8-K item
codes in August 2004. Filings before that declare single-digit items ("1", "5")
which the scorer's ``\\d\\.\\d{2}`` item-code regex does not match, so they will
fetch and then mark scored with zero verdicts. The submissions payload declares
its item codes, so :class:`DiscoverySummary` counts them up front and the
operator learns before queueing 128 filings that some fraction will produce
nothing.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol, cast

import httpx
import structlog

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.intelligence.filing_processor.client import (
    EDGAR_ARCHIVES_BASE,
    FILING_SOURCE,
    HTTPClient,
    Tier3Roster,
    item_id_from_accession,
)
from shrap.intelligence.filing_processor.store import PendingFiling, PostgresFilingStore

log = structlog.get_logger(__name__)

SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# A cursor row distinct from the service's DEFAULT_FEED and from the existing
# backfill's BACKFILL_FEED, so discovery can never move a poll position.
DISCOVERY_FEED = f"{FILING_SOURCE}-8k-discovery"

FORM_8K = "8-K"
FORM_8K_AMENDED = "8-K/A"

# Default form set matches the live poll path exactly (its stored rows carry
# ``kind = '8-K'``), so discovery finds the same population and not a wider one.
# Amendments are opt-in rather than silently added.
DEFAULT_FORMS: tuple[str, ...] = (FORM_8K,)

# Post-August-2004 item numbering: <digit>.<two digits>. The scorer only
# recognises this shape; see the module docstring.
_MODERN_ITEM_PREFIXES = tuple(f"{n}." for n in range(1, 10))


@dataclass(frozen=True, slots=True)
class SubmissionFiling:
    """One row of a registrant's EDGAR filing history."""

    accession: str
    form: str
    filing_date: date
    declared_items: tuple[str, ...]

    def has_modern_item_codes(self) -> bool:
        """Whether the declared items use the post-2004 ``d.dd`` numbering."""

        return any(
            item.startswith(_MODERN_ITEM_PREFIXES) and len(item) == 4
            for item in self.declared_items
        )

    def declares_legacy_item_codes(self) -> bool:
        """Declared items exist and none of them are the modern shape.

        An empty ``items`` field is *not* counted: it means the payload said
        nothing, not that the filing predates the renumbering, and the fetch
        stage still extracts codes from the filing text.
        """

        return bool(self.declared_items) and not self.has_modern_item_codes()


@dataclass(frozen=True, slots=True)
class ShardRef:
    """One older-submissions shard and the filing-date window it covers."""

    name: str
    filing_from: date | None
    filing_to: date | None

    def intersects(self, since: date, until: date) -> bool:
        """Whether this shard can contain a filing in ``[since, until)``.

        Missing bounds are treated as open, so an unparseable shard header
        errs toward fetching it rather than silently skipping filings.
        """

        if self.filing_to is not None and self.filing_to < since:
            return False
        if self.filing_from is not None and self.filing_from >= until:
            return False
        return True


def normalize_cik(cik: str | int) -> str:
    """Canonical CIK digits, no leading zeros (the Archives-path form)."""

    digits = "".join(ch for ch in str(cik) if ch.isdigit()).lstrip("0")
    return digits or "0"


def submissions_url(cik: str | int) -> str:
    """Submissions URL for a CIK. The path form is zero-padded to ten digits."""

    return f"{SUBMISSIONS_BASE}/CIK{normalize_cik(cik).zfill(10)}.json"


def shard_url(name: str) -> str:
    return f"{SUBMISSIONS_BASE}/{name}"


def filing_index_url(cik: str, accession: str) -> str:
    """Human-readable filing index page, stored for provenance."""

    return f"{EDGAR_ARCHIVES_BASE}/{cik}/{accession.replace('-', '')}/{accession}-index.htm"


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _str_list(arrays: Mapping[str, Any], key: str) -> list[str]:
    value = arrays.get(key)
    if not isinstance(value, list):
        return []
    return [v if isinstance(v, str) else "" for v in value]


def parse_filing_arrays(arrays: Mapping[str, Any]) -> list[SubmissionFiling]:
    """Zip the submissions API's parallel arrays into filing records.

    The payload is column-oriented — ``accessionNumber``, ``form``,
    ``filingDate`` and ``items`` are separate equal-length lists. A ragged
    payload is truncated to the shortest column and logged: a misaligned zip
    would attach one filing's date to another's accession, which is worse than
    discovering fewer filings.
    """

    columns = {
        key: _str_list(arrays, key) for key in ("accessionNumber", "form", "filingDate", "items")
    }
    lengths = {key: len(value) for key, value in columns.items()}
    if not lengths or min(lengths.values()) == 0:
        return []
    limit = min(lengths.values())
    if len(set(lengths.values())) > 1:
        log.warning("filing_discovery.ragged_submission_arrays", lengths=lengths, used=limit)

    filings: list[SubmissionFiling] = []
    for index in range(limit):
        accession = columns["accessionNumber"][index].strip()
        filing_date = _parse_date(columns["filingDate"][index])
        if not accession or filing_date is None:
            continue
        raw_items = columns["items"][index]
        filings.append(
            SubmissionFiling(
                accession=accession,
                form=columns["form"][index].strip(),
                filing_date=filing_date,
                declared_items=tuple(part.strip() for part in raw_items.split(",") if part.strip()),
            )
        )
    return filings


def parse_submissions(
    payload: Mapping[str, Any],
) -> tuple[str | None, list[SubmissionFiling], list[ShardRef]]:
    """Split a submissions payload into (registrant name, recent filings, shards)."""

    name = payload.get("name")
    filings_block = payload.get("filings")
    if not isinstance(filings_block, Mapping):
        return (name if isinstance(name, str) else None), [], []

    recent_block = filings_block.get("recent")
    recent = parse_filing_arrays(recent_block) if isinstance(recent_block, Mapping) else []

    shards: list[ShardRef] = []
    files_block = filings_block.get("files")
    if isinstance(files_block, list):
        for entry in files_block:
            if not isinstance(entry, Mapping):
                continue
            shard_name = entry.get("name")
            if not isinstance(shard_name, str) or not shard_name:
                continue
            shards.append(
                ShardRef(
                    name=shard_name,
                    filing_from=_parse_date(entry.get("filingFrom")),
                    filing_to=_parse_date(entry.get("filingTo")),
                )
            )
    return (name if isinstance(name, str) else None), recent, shards


def parse_company_tickers(payload: Mapping[str, Any]) -> dict[str, str]:
    """Build TICKER → CIK from ``company_tickers.json``.

    The file is keyed by row index rather than ticker, so it is a dict of
    ``{"0": {"cik_str": ..., "ticker": ...}, ...}``. First occurrence wins:
    the file is ordered by size, so a duplicated ticker resolves to the larger
    registrant.
    """

    by_ticker: dict[str, str] = {}
    for entry in payload.values():
        if not isinstance(entry, Mapping):
            continue
        ticker = entry.get("ticker")
        cik = entry.get("cik_str")
        if not isinstance(ticker, str) or cik is None:
            continue
        key = ticker.strip().upper()
        if key and key not in by_ticker:
            by_ticker[key] = normalize_cik(cik)
    return by_ticker


def select_filings(
    filings: Iterable[SubmissionFiling],
    forms: Sequence[str],
    since: date,
    until: date,
) -> list[SubmissionFiling]:
    """Filings of the requested forms filed in ``[since, until)``, oldest first."""

    wanted = {form.upper() for form in forms}
    picked = [f for f in filings if f.form.upper() in wanted and since <= f.filing_date < until]
    return sorted(picked, key=lambda f: (f.filing_date, f.accession))


def to_pending(
    filing: SubmissionFiling, *, cik: str, symbol: str, company: str | None
) -> PendingFiling:
    """Build the pending row the fetch/score stages already know how to drain.

    ``item_id`` and ``url`` are carried in the payload under the same keys the
    live poll path uses, so provenance reads the same whichever door a filing
    came through; ``discovery`` marks which door that was.
    """

    index_url = filing_index_url(cik, filing.accession)
    return PendingFiling(
        accession=filing.accession,
        cik=cik,
        symbol=symbol,
        title=f"{filing.form} - {company or symbol}",
        company=company,
        filing_url=index_url,
        filing_date=datetime.combine(filing.filing_date, datetime.min.time(), tzinfo=UTC),
        payload={
            "item_id": item_id_from_accession(filing.accession),
            "url": index_url,
            "form": filing.form,
            "declared_items": list(filing.declared_items),
            "discovery": "sec-submissions-api",
        },
    )


@dataclass(frozen=True, slots=True)
class DiscoverySummary:
    """Outcome of one discovery pass, in the shape the CLI prints."""

    symbols_resolved: int
    unresolved: tuple[str, ...]
    filings_seen: int
    matched: int
    already_known: int
    recorded: int
    legacy_item_codes: int
    dry_run: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbols_resolved": self.symbols_resolved,
            "unresolved": ",".join(self.unresolved) or "-",
            "filings_seen": self.filings_seen,
            "matched": self.matched,
            "already_known": self.already_known,
            "recorded": self.recorded,
            "legacy_item_codes": self.legacy_item_codes,
            "dry_run": self.dry_run,
        }

    def render(self) -> str:
        """One-line summary, plus the caveats that would otherwise go unsaid."""

        head = " ".join(f"{key}={value}" for key, value in self.as_dict().items())
        notes: list[str] = []
        if self.unresolved:
            notes.append(
                f"! {len(self.unresolved)} symbol(s) had no CIK and were skipped: "
                f"{', '.join(self.unresolved)}"
            )
        if self.legacy_item_codes:
            notes.append(
                f"! {self.legacy_item_codes} discovered filing(s) declare pre-2004 item "
                "codes; the scorer's d.dd item regex will not match them and they will "
                "score zero items"
            )
        if self.dry_run:
            notes.append("dry run — nothing was written")
        return "\n".join([head, *notes])


class SubmissionsSource(Protocol):
    async def company_tickers(self, http: HTTPClient, timeout: float) -> dict[str, str]: ...

    async def submissions(
        self, http: HTTPClient, cik: str, timeout: float
    ) -> Mapping[str, Any]: ...

    async def shard(self, http: HTTPClient, name: str, timeout: float) -> Mapping[str, Any]: ...


class SubmissionsFetchError(Exception):
    """A submissions-API request returned a non-200 status."""

    def __init__(self, url: str, status_code: int) -> None:
        super().__init__(f"edgar submissions fetch failed: HTTP {status_code} for {url}")
        self.url = url
        self.status_code = status_code


class SecSubmissionsClient:
    """Read-only EDGAR submissions client under SEC's User-Agent convention."""

    def __init__(self, user_agent: str) -> None:
        self._headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}

    async def _get_json(self, http: HTTPClient, url: str, timeout: float) -> Mapping[str, Any]:
        response = await http.get(url, params={}, headers=self._headers, timeout=timeout)
        if response.status_code != 200:
            raise SubmissionsFetchError(url, response.status_code)
        parsed = json.loads(response.text)
        if not isinstance(parsed, dict):
            raise SubmissionsFetchError(url, response.status_code)
        return cast(Mapping[str, Any], parsed)

    async def company_tickers(self, http: HTTPClient, timeout: float = 30.0) -> dict[str, str]:
        return parse_company_tickers(await self._get_json(http, COMPANY_TICKERS_URL, timeout))

    async def submissions(
        self, http: HTTPClient, cik: str, timeout: float = 30.0
    ) -> Mapping[str, Any]:
        return await self._get_json(http, submissions_url(cik), timeout)

    async def shard(self, http: HTTPClient, name: str, timeout: float = 30.0) -> Mapping[str, Any]:
        return await self._get_json(http, shard_url(name), timeout)


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    """Knobs for one discovery pass."""

    forms: tuple[str, ...] = DEFAULT_FORMS
    throttle_seconds: float = 0.2
    http_timeout: float = 30.0


def roster_ciks(roster: Tier3Roster) -> dict[str, str]:
    """Invert the CIK-keyed roster into TICKER → CIK."""

    return {ticker.upper(): cik for cik, ticker in roster.by_cik.items()}


async def resolve_ciks(
    source: SubmissionsSource,
    http: HTTPClient,
    symbols: Sequence[str],
    roster: Tier3Roster,
    *,
    timeout: float = 30.0,
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Resolve symbols to CIKs: roster first, ``company_tickers.json`` second.

    The 218KB ticker file is only fetched when the roster leaves something
    unresolved. Symbols that resolve nowhere are returned rather than raising —
    the eight ETFs in the launch list have no registrant CIK of their own and
    also file no 8-Ks, so a partial resolution is the normal case, not an error.
    """

    known = roster_ciks(roster)
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for symbol in symbols:
        key = symbol.strip().upper()
        if not key:
            continue
        cik = known.get(key)
        if cik is not None:
            resolved[key] = cik
        else:
            missing.append(key)

    if missing:
        by_ticker = await source.company_tickers(http, timeout)
        still_missing: list[str] = []
        for key in missing:
            cik = by_ticker.get(key)
            if cik is not None:
                resolved[key] = cik
            else:
                still_missing.append(key)
        missing = still_missing

    return resolved, tuple(missing)


class DiscoveryStore(Protocol):
    async def select_filing_states(self, accessions: Sequence[str]) -> Mapping[str, Any]: ...

    async def record_and_advance(
        self,
        feed: str,
        pendings: Sequence[PendingFiling],
        last_fetched_at: datetime | None,
        seen: int,
        now: datetime,
    ) -> int: ...


async def _filings_for_cik(
    source: SubmissionsSource,
    http: HTTPClient,
    cik: str,
    config: DiscoveryConfig,
    since: date,
    until: date,
) -> tuple[str | None, list[SubmissionFiling], int]:
    """Recent filings plus any older shards that intersect the window."""

    payload = await source.submissions(http, cik, config.http_timeout)
    company, recent, shards = parse_submissions(payload)
    seen = len(recent)
    filings = list(recent)

    wanted = [s for s in shards if s.intersects(since, until)]
    for shard in wanted:
        await asyncio.sleep(config.throttle_seconds)
        log.info("filing_discovery.shard", cik=cik, shard=shard.name)
        shard_filings = parse_filing_arrays(
            await source.shard(http, shard.name, config.http_timeout)
        )
        seen += len(shard_filings)
        filings.extend(shard_filings)

    return company, filings, seen


async def discover_pass(
    store: DiscoveryStore,
    source: SubmissionsSource,
    http: HTTPClient,
    config: DiscoveryConfig,
    roster: Tier3Roster,
    *,
    symbols: Sequence[str],
    since: date,
    until: date,
    dry_run: bool,
    now: datetime,
) -> DiscoverySummary:
    """Resolve → ask EDGAR → record pending rows. One symbol's failure is fatal
    to that symbol only; the rest of the pass continues."""

    resolved, unresolved = await resolve_ciks(
        source, http, symbols, roster, timeout=config.http_timeout
    )

    pendings: list[PendingFiling] = []
    filings_seen = 0
    legacy = 0
    for index, (symbol, cik) in enumerate(sorted(resolved.items())):
        if index:
            await asyncio.sleep(config.throttle_seconds)
        try:
            company, filings, seen = await _filings_for_cik(source, http, cik, config, since, until)
        except Exception:
            log.exception("filing_discovery.symbol_failed", symbol=symbol, cik=cik)
            continue
        filings_seen += seen
        picked = select_filings(filings, config.forms, since, until)
        legacy += sum(1 for f in picked if f.declares_legacy_item_codes())
        pendings.extend(to_pending(f, cik=cik, symbol=symbol, company=company) for f in picked)
        log.info(
            "filing_discovery.symbol",
            symbol=symbol,
            cik=cik,
            seen=seen,
            matched=len(picked),
        )

    accessions = [p.accession for p in pendings]
    states = await store.select_filing_states(accessions) if accessions else {}
    already_known = sum(1 for a in accessions if a in states)

    recorded = 0
    if pendings and not dry_run:
        # last_fetched_at stays None: discovery makes no claim about a poll
        # position, and the cursor row exists only as an audit counter.
        recorded = await store.record_and_advance(
            DISCOVERY_FEED, pendings, None, len(pendings), now
        )

    return DiscoverySummary(
        symbols_resolved=len(resolved),
        unresolved=unresolved,
        filings_seen=filings_seen,
        matched=len(pendings),
        already_known=already_known,
        recorded=recorded,
        legacy_item_codes=legacy,
        dry_run=dry_run,
    )


def parse_date_range(since: str, until: str | None) -> tuple[date, date]:
    """Parse ``--since``/``--until`` into a ``[since, until)`` day window.

    ``until`` is inclusive of its whole day; omitted, the window runs to
    tomorrow so today's filings are included.
    """

    since_d = date.fromisoformat(since)
    if until is None:
        return since_d, datetime.now(UTC).date() + timedelta(days=1)
    return since_d, date.fromisoformat(until) + timedelta(days=1)


async def run(
    postgres_dsn: str,
    sec_user_agent: str,
    roster: Tier3Roster,
    config: DiscoveryConfig,
    *,
    symbols: Sequence[str],
    since: date,
    until: date,
    dry_run: bool,
    service_name: str = "filing-processor-discovery",
    log_level: str = "INFO",
) -> DiscoverySummary:
    """Run one discovery pass against real infrastructure.

    No Redis: discovery publishes nothing. The events come later, from the
    service's own score pass, when the queued filings are actually read.
    """

    configure_logging(service_name, log_level)
    log.info(
        "filing_discovery.starting",
        symbols=list(symbols),
        since=since.isoformat(),
        until=until.isoformat(),
        forms=list(config.forms),
        dry_run=dry_run,
    )
    pool = await create_asyncpg_pool(postgres_dsn)
    store = PostgresFilingStore(pool)
    await store.ensure_schema()
    try:
        async with httpx.AsyncClient(timeout=config.http_timeout, follow_redirects=True) as http:
            summary = await discover_pass(
                cast(DiscoveryStore, store),
                SecSubmissionsClient(sec_user_agent),
                cast(HTTPClient, http),
                config,
                roster,
                symbols=symbols,
                since=since,
                until=until,
                dry_run=dry_run,
                now=datetime.now(UTC),
            )
    finally:
        await pool.close()
    log.info("filing_discovery.complete", **summary.as_dict())
    return summary


def default_symbols(roster: Tier3Roster) -> tuple[str, ...]:
    """The roster's tickers, sorted — the default discovery scope."""

    return tuple(sorted(roster_ciks(roster)))


__all__ = [
    "COMPANY_TICKERS_URL",
    "DEFAULT_FORMS",
    "DISCOVERY_FEED",
    "FORM_8K",
    "FORM_8K_AMENDED",
    "SUBMISSIONS_BASE",
    "DiscoveryConfig",
    "DiscoveryStore",
    "DiscoverySummary",
    "SecSubmissionsClient",
    "ShardRef",
    "SubmissionFiling",
    "SubmissionsFetchError",
    "SubmissionsSource",
    "default_symbols",
    "discover_pass",
    "filing_index_url",
    "normalize_cik",
    "parse_company_tickers",
    "parse_date_range",
    "parse_filing_arrays",
    "parse_submissions",
    "resolve_ciks",
    "roster_ciks",
    "run",
    "select_filings",
    "shard_url",
    "submissions_url",
    "to_pending",
]
