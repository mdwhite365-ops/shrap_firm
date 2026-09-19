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

**I could not determine the cause, and the fix does not depend on knowing it.**

Ruled out by experiment on 2026-09-19: the URL, the user-agent, container
vs. host, the public IP, HTTP/1.1 vs. HTTP/2, sync vs. async clients, and the
query shape. The identical request succeeded 8/8 in one window and failed 6/6
twenty minutes later from the same address. The 406 carries an empty body and is
served by arXiv's Fastly edge (`via: varnish`, and no `server: Google Frontend`
header), so the origin is never reached. It correlates with recent request
volume and nothing else I could isolate.

The sources now treat 406 as transient **for arXiv only** and retry it. Two
honest caveats:

- **Retry does not rescue a sustained outage.** Ten attempts over sixty seconds
  all returned 406 during this one. Retry is for the brief version — EDGAR, the
  DOE newsroom and the Federal Register each blipped at least once in the same
  48 hours, and none of those needed to cost an hour.
- **406 is retryable only for arXiv.** Everywhere else it is a genuine
  content-negotiation failure and retrying it would turn one wrong request into
  three. `403` is never retried anywhere: SEC bans clients that keep knocking.

So the real mitigation here is **detection, not prevention**. The firm cannot
stop arXiv returning 406. It can stop losing two days to it.

## Adding a per-source check for something else

Set `partition_column` on a `FreshnessTarget` in
`src/shrap/operations/staleness.py`. The column must be a plain identifier — it
is interpolated into SQL, and construction rejects anything else.

Before adding one, ask the question that decided this card: **does the timestamp
advance when the producer works, or only when the producer finds something?** If
it is the second, a quiet producer will alarm forever and the check will be
turned off within a week.
