# A dead ingest source

**Written 2026-09-19, after arXiv had been dead for two days and nothing said
so.**

## What happened

Between 2026-09-17 14:17 and 2026-09-19 22:48, both arXiv feeds returned HTTP
406 on **46 consecutive hourly passes**. Nothing alarmed. The only trace was the
Hypothesis Generator logging `sweep_empty` every hour — which is exactly what it
logs during a genuinely quiet week.

The firm's only source of quantitative-finance literature was off for two days
and the monitoring said everything was fine.

## Why nothing caught it

There *was* a freshness check on `research.raw_source_items`, with a six-hour
threshold. Its own rationale, written when it was added, says:

> Six hours is six consecutive passes in which EDGAR, arXiv, USASpending, DOE
> and the Federal Register **all** returned nothing.

That "all" is the bug. The check reads `max(fetched_at)` over the whole table.
EDGAR kept inserting roughly a thousand items a week throughout, so the table's
newest row was never more than minutes old and the check stayed green for the
entire outage.

**A table-level maximum is an AND across every producer that writes to the
table.** It can only see a total outage. This is the same shape as the filing
roster covering 4 of 50 names and node-exporter never being scraped: an
aggregate stayed healthy while a component inside it was dead.

## What now catches it

`research.ingest_cursors`, checked **per source**, three-hour threshold.

Two choices make it work, and both matter:

- **Per source**, so one dead feed among five healthy ones is visible. The
  reading is the *oldest* per-source maximum, and the alarm names the source, so
  it says `arxiv` rather than "the table is stale".
- **On the cursor table, not the items table.** `raw_source_items` upserts `ON
  CONFLICT (item_id) DO NOTHING`, so its `fetched_at` advances only when a
  genuinely *new* item appears. USASpending has inserted nothing since
  2026-08-27 while working perfectly — its feed keeps returning the same award.
  Per-source freshness over the items table would alarm on that forever.
  `ingest_cursors` upserts `updated_at = EXCLUDED.updated_at` on every
  *successful* pass, so it distinguishes "fetched, nothing new" from "did not
  fetch".

Verified against the live database on 2026-09-19, mid-outage:

```
 last_row_at                   | partition | has_rows
-------------------------------+-----------+----------
 2026-09-17 14:17:30.502988+00 | arxiv     | t
```

Two days and eight hours, against a three-hour threshold, naming the feed.

## Checking it by hand

```bash
ssh dell 'docker exec shrap_postgres psql -U shrap -d shrap -c \
  "select source, updated_at, now()-updated_at as age \
   from research.ingest_cursors order by updated_at"'
```

The oldest row is the answer. Anything over ~2 hours is a source that has missed
a pass; anything over 3 hours should already have alarmed.

To see *why* a source is failing:

```bash
ssh dell 'docker logs --since 6h shrap_tech_watcher 2>&1 | grep source_failed'
```

## About the arXiv 406 specifically

**arXiv's edge answers a throttled host with 406 and an empty body.** Not 429,
and not from the API — from Fastly. A 406 response carries `via: varnish` and no
`server: Google Frontend` header, so the origin is never reached.

The behaviour that took longest to see, and the reason this looked random for an
hour: **a throttled host gets 406 on every cache MISS while cache HITS keep
returning 200.** Repeating one query appeared to prove the client was fine,
because Fastly was answering it. Any fresh query failed. Ruled out along the way,
all by experiment: the URL, the user-agent, container-vs-host, the public IP,
HTTP/1.1-vs-2, sync-vs-async, the category set and the query shape.

### What we were doing wrong

arXiv's terms of use (https://info.arxiv.org/help/api/tou.html):

> make no more than one request every three seconds, and limit requests to a
> single connection at a time

The ingest pass registers **two** `ArxivSource` instances — `arxiv` and
`arxiv-qfin` — and the pass loop fetches them back to back with no delay. Two
requests inside 100 ms, twice an hour, for months. That is a documented
violation, and it is the most plausible reason this host is throttled.

Three changes, in order of confidence:

1. **A shared 3-second throttle across both arXiv sources** (`ARXIV_THROTTLE`).
   Shared because the limit is per host — a per-instance throttle would satisfy
   nothing. It applies inside the retry too, since a retry that ignored the
   limit is the fastest way back into a throttle. This is a straightforward
   compliance fix and is the one worth trusting.
2. **Explicit `Accept: application/atom+xml` and a descriptive `User-Agent`.**
   Widely reported as the fix for arXiv 406s, cheap, and good practice
   regardless.
3. **406 treated as retryable for arXiv only.** Elsewhere it is a genuine
   content-negotiation failure and retrying would turn one wrong request into
   three. `403` is never retried anywhere: SEC bans clients that keep knocking.

### What is not established

**None of the three was verified against a working arXiv, because the host was
throttled for the whole session.** Once throttled, all four header combinations
returned 406, and ten retries over sixty seconds returned 406. Testing during
the outage could not distinguish a good fix from a bad one.

Worse, and worth saying plainly: **the diagnostic probing done on 2026-09-19 was
itself heavy enough to keep tripping the throttle**, so live probing from this
address is contaminated evidence for a while. The original outage began
2026-09-17 14:17, before any of it.

So the honest status is: the rate-limit violation is real and is fixed; whether
it was *the* cause is unproven. **The check that tells us is the per-source
freshness target** — if `research.ingest_cursors[arxiv]` goes green and stays
green, the fix worked. If it goes stale again, it did not, and this runbook is
where the next attempt starts.

## Adding a per-source check for something else

Set `partition_column` on a `FreshnessTarget` in
`src/shrap/operations/staleness.py`. The column must be a plain identifier — it
is interpolated into SQL, and construction rejects anything else.

Before adding one, ask the question that decided this card: **does the timestamp
advance when the producer works, or only when the producer finds something?** If
it is the second, a quiet producer will alarm forever and the check will be
turned off within a week.
