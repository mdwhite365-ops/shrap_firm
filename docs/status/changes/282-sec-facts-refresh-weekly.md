### SEC share counts and filed figures refresh weekly (#282)

Both SEC backfills were one-shots. Share counts (#258), which market cap is
built from, were last run by hand on 2026-09-20. Filed accounting figures
(#280) were run once, on 2026-09-27. Every filing after a hand run was invisible
to the panel, and nothing would have said so.

`sec-facts-refresh` is now an always-on service that runs both backfills weekly.
It sends a real SEC contact rather than the CLIs' placeholder User-Agent. Each
table has an eight-day freshness target, because the upsert moves `fetched_at`
on every pass, so the target measures the refresh rather than the filing
calendar.
