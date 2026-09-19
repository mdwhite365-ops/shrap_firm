# Backup and restore

**Verified 2026-09-19** by restoring the real `/mnt/backups` artifact into a
throwaway container and counting rows. Nothing here is written from the script;
every claim below was run.

## What is backed up, and when

| what | how | verified by |
|---|---|---|
| `shrap` (Postgres + TimescaleDB) | `pg_dump -Fc` | `pg_restore --list` parses the TOC |
| Postgres globals (roles, tablespaces) | `pg_dumpall --globals-only` | gzip integrity, 64-byte floor |
| `langfuse` | `pg_dump -Fc` | as above |
| Redis | `BGSAVE`, wait for completion, then tar the volume | gzip integrity |
| Qdrant | tar the volume — **skipped while it holds no collections** | gzip integrity when present |

**Schedule: TrueNAS cron job id 1, daily 02:30 Pacific, as `truenas_admin`**,
appending to `/mnt/backups/backup.log`. Registered through
`midclt call cronjob.create`, so it lives in the TrueNAS config database and is
visible in the UI under Data Protection → Cron Jobs — not in a root crontab that
nobody can see and nothing records.

```bash
midclt call cronjob.query        # confirm it is still there
```

Retention is 30 days (`BACKUP_RETENTION_DAYS`), pruned at the end of each run.

## Why the schedule is the part to check

**There had been no backup for 18 days when this runbook was written**, and the
cause was not the script. `cronjob.query` returned `[]`: nothing was scheduled at
all. The only two artifacts in `/mnt/backups` were dated 2026-08-25 and
2026-09-01, a week apart and then nothing — consistent with two hand-runs rather
than a schedule that stopped.

This is the **third** time the backup has been found not running (KI-034, then
#212–#214, now this). Each time the script was improved and the schedule was
not verified afterwards. **Check `cronjob.query`, not the script.**

## Restoring — the procedure that was actually tested

Restore into a **throwaway container first**. Never test a restore against the
live database.

```bash
docker run -d --name pg_restore_test \
  -e POSTGRES_PASSWORD=t -e POSTGRES_USER=shrap -e POSTGRES_DB=shrap \
  timescale/timescaledb-ha:pg16

docker exec -i pg_restore_test psql -U shrap -d postgres -c 'CREATE DATABASE restoretest;'
docker exec -i pg_restore_test psql -U shrap -d restoretest -c 'CREATE EXTENSION IF NOT EXISTS timescaledb;'
docker exec -i pg_restore_test pg_restore -U shrap -d restoretest --no-owner --no-privileges \
  < /mnt/backups/shrap-postgres-<DATE>.dump
```

Then **count rows** — `pg_restore` exiting 0 is a claim, not proof:

```sql
SELECT 'daily_bars', count(*) FROM market_data.daily_bars
UNION ALL SELECT 'intraday_bars', count(*) FROM market_data.intraday_bars
UNION ALL SELECT 'order_events', count(*) FROM trading.paper_order_events
UNION ALL SELECT 'evaluations', count(*) FROM research.evaluations;
```

Finish with `docker rm -f pg_restore_test`.

## The TimescaleDB question, settled

#214 recorded the concern that `shrap` is TimescaleDB *"being dumped as plain
Postgres"* and that the dump therefore might not restore. Tested directly on
2026-09-19 with a fresh dump of the live database:

```
intraday_bars after restore:  299 chunks, still a hypertable
rows restored                 1,811,780
rows live                     1,811,780
pg_restore exit               0, no errors
```

**A plain `pg_dump -Fc` round-trips the hypertable intact on
`timescale/timescaledb-ha:pg16`** — chunk count and row count both exact. The
`timescaledb_pre_restore()` / `timescaledb_post_restore()` dance the older
TimescaleDB docs describe is not needed here. Re-test this if the image major
version changes.

`pg_dump` emits a warning about circular foreign keys on `continuous_agg` and
suggests the dump may not restore. On this stack that warning is noise — the
restore above succeeded with it present.

## Restoring an older state is restoring an older schema

The 2026-09-01 artifact restores cleanly but has **no `market_data.intraday_bars`
table**, because that table arrived with #236 on 2026-09-18. That is the backup
being faithful, not broken. A restore rolls the schema back as well as the data,
so after restoring an old artifact the services must run their `ensure_schema`
path before they will work.
