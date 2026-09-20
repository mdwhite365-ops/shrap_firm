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

Two separate things were wrong, and they took a long time to tell apart because
both present as the same empty-bodied 406 from arXiv's Fastly edge (`via:
varnish`, no `server: Google Frontend`, `x-cache: MISS` — the origin is never
reached).

### 1. Two of the four categories are refused outright

This is the one that killed the `arxiv` feed, and it is **deterministic**.
Measured three clean rounds, eight seconds apart, identical every time:

```
cs.AI               200
cs.LG               200
q-bio.NC            406
cond-mat            406
cond-mat.stat-mech  406
```

The source asked for `cat:cs.AI OR cat:cs.LG OR cat:cond-mat OR cat:q-bio.NC` in
a **single query**, so two refused categories took the two healthy ones down with
them. Forty-six consecutive passes ingested nothing from a feed that was half
fine.

**Fixed by querying one category at a time**, tolerating individual failures and
failing the source only if *every* category fails. Verified against live arXiv:

```
tech_watcher.arxiv_category_failed  category=cond-mat   source=arxiv
tech_watcher.arxiv_category_failed  category=q-bio.NC   source=arxiv
RESULT: NEW arxiv source OK, items = 182
```

**182 items where the old code returned zero.** Cross-listed papers are deduped
by `item_id` across categories.

Why arXiv refuses those two is not established. `cond-mat` is an archive rather
than a category, which would explain that one; `q-bio.NC` looks valid and is
refused anyway. It does not matter much: the design now survives any category
being refused, including ones that are fine today.

### 2. Rate limiting, which is what hit `arxiv-qfin`

arXiv's [terms of use](https://info.arxiv.org/help/api/tou.html):

> make no more than one request every three seconds, and limit requests to a
> single connection at a time

The pass registers **two** `ArxivSource` instances and fetched them back to back
with no delay. A throttled host gets 406 on every cache **miss** while cache
**hits** keep returning 200 — which is why this looked random for an hour:
repeating one query appeared to prove the client was fine, because Fastly was
answering it.

Fixed with a **shared** 3-second throttle across both arXiv sources — shared
because the limit is per host, so a per-instance throttle would satisfy nothing.
It applies inside the retry too. Plus an explicit `Accept: application/atom+xml`
and a descriptive `User-Agent`, both widely reported as necessary and cheap
either way.

`arxiv-qfin` recovered on its own at 22:48 on 2026-09-19 and ingested 4 new
papers — its first in two days — which is consistent with a throttle that
expired.

### What is not established

- **Whether the throttle fix prevents recurrence.** It could not be tested
  against a throttled host, and my own diagnostic probing on 2026-09-19 was
  heavy enough to keep re-tripping the throttle, so live probing from this
  address is contaminated evidence for a while. **Do not diagnose an external
  API by hammering it from the production IP.**
- **Why `cond-mat` and `q-bio.NC` are refused.** The feed no longer depends on
  knowing.

The per-source freshness check is what answers both over time: if
`research.ingest_cursors[arxiv]` goes green and stays green, this worked.

## Adding a per-source check for something else

Set `partition_column` on a `FreshnessTarget` in
`src/shrap/operations/staleness.py`. The column must be a plain identifier — it
is interpolated into SQL, and construction rejects anything else.

Before adding one, ask the question that decided this card: **does the timestamp
advance when the producer works, or only when the producer finds something?** If
it is the second, a quiet producer will alarm forever and the check will be
turned off within a week.
