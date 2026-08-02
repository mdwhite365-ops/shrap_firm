"""Dereference the EDGAR filing link the Tech Watcher has always ignored (KI-026).

The world-changer funnel has judged **zero** of ~3,700 EDGAR filings relevant,
and the bar experiment (2026-07-31) found why. The Atom ``getcurrent`` feed's
``summary`` is index metadata, not a document:

    <b>Filed:</b> 2026-07-28 <b>AccNo:</b> 0001234567-26-000123
    <b>Size:</b> 565 KB <br>Item 8.01: Other Events

Averaged over the corpus that is 179 characters saying a filing exists and how
large it is. USASpending summaries average *fewer* characters (147) and are
admitted at roughly 14%, because they name a recipient, an amount and a purpose.
Length was never the discriminator — content type was. The filter has been
asked, ~3,700 times, whether a file size is evidence of a world-changing
pattern, and has correctly said no every time.

**This module reuses rather than rebuilds.** ``EdgarFilingClient`` already
fetches and de-markups full submission text for the Filing Processor, and its
own docstring names this gap: *"The Tech Watcher's EdgarSource captures the
current-filings Atom feed but never dereferences the filing link."* A second
fetcher would duplicate SEC's ``User-Agent`` convention, the throttle and the
markup handling, and would drift from them.

Why the Filing Processor's table could not simply be joined: ``intelligence.filings``
holds only registrants matched to the **Tier 3 roster** by CIK
(``service.match_candidate`` returns ``None`` otherwise). The world-changer
funnel is looking for a pattern anywhere in the economy, not inside a 50-name
trading universe, so the overlap is a sliver of the feed and the join would
leave the actual problem untouched.

**Item sections, not a head slice.** A full submission runs to hundreds of
kilobytes, most of it exhibits, and it opens with cover-page boilerplate. The
first N characters are therefore close to the worst N characters available.
``split_item_sections`` is reused to keep the declared 8-K item bodies — the part
that says what happened — and the rest is dropped. A filing with no parseable
item codes falls back to a leading slice, which is worse but still a document
rather than a file size.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

from shrap.intelligence.filing_processor.client import (
    EdgarFilingClient,
    FilingFetchError,
    HTTPClient,
    accession_from_item_id,
    parse_cik,
    split_item_sections,
)
from shrap.research.tech_watcher.sources import SOURCE_EDGAR

log = structlog.get_logger(__name__)

# How much of a filing reaches the filter prompt.
#
# The summary path caps at 1,500 characters, which was right for a one-line
# feed abstract and is far too small for a document. 6,000 is ~1,000 words:
# comfortably more than an 8-K item narrative, and roughly 1,500 tokens, so a
# corpus-wide re-filter stays affordable on a tier billed by GPU time.
#
# Truncation is logged (`tech_watcher.edgar_text_stored` carries `truncated`)
# rather than stored, so a clipped filing is visible in the pass output. It is
# NOT recoverable from the row afterwards, which is a real limitation: a verdict
# on a clipped filing cannot later be told from one on a whole filing by reading
# the database. Worth a column if the clip rate turns out to be material.
DEFAULT_MAX_CHARS = 6_000

# `filtered_at` is deliberately NOT a condition. Fetching text for an item that
# has already been scored is the entire point: those ~3,700 verdicts were formed
# without a document and are exactly the ones worth re-forming.
SELECT_EDGAR_WITHOUT_TEXT_SQL = """
SELECT item_id, url
FROM research.raw_source_items
WHERE source = $1
  AND document_text IS NULL
ORDER BY external_ts DESC NULLS LAST, item_id
LIMIT $2
""".strip()

UPDATE_DOCUMENT_TEXT_SQL = """
UPDATE research.raw_source_items
SET document_text = $2
WHERE item_id = $1
""".strip()


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...

    async def fetch(self, sql: str, *args: object) -> Sequence[Any]: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


def document_body(full_text: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Reduce a full submission to the part that says what happened.

    Keeps the declared 8-K item sections, labelled and in document order, and
    drops the cover page and exhibits. Falls back to a leading slice when no
    item codes parse — a 10-K or an S-1 has no 8-K items, and a truncated
    document still beats a file size.
    """

    sections = split_item_sections(full_text)
    if sections:
        joined = "\n\n".join(f"Item {code}: {body}" for code, body in sections.items() if body)
        if joined.strip():
            return joined[:max_chars].strip()
    return full_text.strip()[:max_chars]


@dataclass(frozen=True, slots=True)
class EdgarTextReport:
    """One backfill pass, reported without claiming anything it did not measure."""

    eligible: int
    fetched: int
    failed: int
    dry_run: bool

    def render(self) -> str:
        if self.dry_run:
            # Third time this shape has been caught in this repo (#183, #187):
            # a dry run returns before doing the work, so every count except
            # `eligible` would be a zero derived from nothing and printed in the
            # exact shape of a result.
            return (
                f"[dry-run] EDGAR full text: {self.eligible} filing(s) eligible and NOT "
                "fetched — nothing was requested from SEC, so nothing is known about "
                "how many would succeed.\n"
                "  Re-run without --dry-run to fetch them. If this equals --limit, "
                "raise it to see the true eligible count."
            )
        return (
            f"EDGAR full text: {self.fetched} fetched, {self.failed} failed, "
            f"of {self.eligible} eligible"
        )


async def edgar_text_pass(
    pool: AsyncPool,
    client: EdgarFilingClient,
    http: HTTPClient,
    *,
    max_items: int = 200,
    max_chars: int = DEFAULT_MAX_CHARS,
    dry_run: bool = False,
) -> EdgarTextReport:
    """Fetch and store filing bodies for EDGAR items that have none.

    One filing per request, sequentially. SEC's fair-access policy is a rate
    limit with a real ban behind it, and the corpus is a few thousand filings
    fetched once — there is nothing to gain by parallelising into a block.

    A fetch failure is counted and skipped, never raised: leaving
    ``document_text`` NULL means the item is simply eligible again next pass,
    which is the correct behaviour for a 429 or a withdrawn filing alike.
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(SELECT_EDGAR_WITHOUT_TEXT_SQL, SOURCE_EDGAR, max_items)

    if dry_run:
        return EdgarTextReport(eligible=len(rows), fetched=0, failed=0, dry_run=True)

    fetched = 0
    failed = 0
    for row in rows:
        item_id = str(row["item_id"])
        accession = accession_from_item_id(item_id)
        cik = parse_cik(None if row["url"] is None else str(row["url"]))
        if accession is None or cik is None:
            # Not retryable: the row has no resolvable Archives URL, so no
            # number of passes will produce one. Counted as a failure so the
            # total stays honest rather than silently shrinking the corpus.
            failed += 1
            log.warning(
                "tech_watcher.edgar_text_unresolvable",
                item_id=item_id,
                has_accession=accession is not None,
                has_cik=cik is not None,
            )
            continue
        try:
            full_text = await client.fetch_filing_text(http, cik, accession)
        except FilingFetchError as exc:
            failed += 1
            log.warning("tech_watcher.edgar_text_fetch_failed", item_id=item_id, error=str(exc))
            continue
        body = document_body(full_text, max_chars=max_chars)
        async with pool.acquire() as conn:
            await conn.execute(UPDATE_DOCUMENT_TEXT_SQL, item_id, body)
        fetched += 1
        log.info(
            "tech_watcher.edgar_text_stored",
            item_id=item_id,
            chars=len(body),
            truncated=len(body) >= max_chars,
        )

    return EdgarTextReport(eligible=len(rows), fetched=fetched, failed=failed, dry_run=False)


__all__ = [
    "DEFAULT_MAX_CHARS",
    "SELECT_EDGAR_WITHOUT_TEXT_SQL",
    "UPDATE_DOCUMENT_TEXT_SQL",
    "EdgarTextReport",
    "document_body",
    "edgar_text_pass",
]
