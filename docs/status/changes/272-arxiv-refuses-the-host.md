### arXiv refuses the host, not the category (#272)

`arxiv-qfin` stopped ingesting at 07:13 UTC on 2026-09-23, and the per-source
freshness check from #251 caught it and paged Discord — the check worked. The
fix #251 shipped alongside it did not.

#251 read 406s for `cond-mat` and `q-bio.NC` as arXiv refusing those
categories, and split every failed query into one request per category. The
audit asked the same queries from the MacBook in the same hour the Dell was
getting 406 for all of them: **every one returned 200.** The 406 was aimed at
the Dell. #251's "deterministic" table was the cache-hit/cache-miss pattern its
own runbook describes — `cs.AI` and `cs.LG` are busy enough to be cached —
read as a property of the categories.

So the fan-out turned each refused pass into **ten requests, 3.00s apart**, at
a host whose refusal each request prolongs.

Now: one combined query per source; a 406 or 429 starts a cooldown shared by
both arXiv sources (2h, doubling to 12h, cleared by the first 200) in which no
request is made; interval 3s → 5s. **Unverified:** how long arXiv holds a
throttle is not published. The escalation covers a long one, and the freshness
check will show whether it clears.

The lesson is #256's, one level up: counting the requests a fix makes is not
enough if the fix is built on a misread of what the refusal meant. **Before
treating a response as a property of the request, send the same request from
somewhere else.**
