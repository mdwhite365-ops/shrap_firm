# Current sprint status

**Last updated:** 2026-07-15
**Phase:** Month 3 / paper spine closed → Research implementation
**Operating mode:** Paper only. No real-money execution.

## Current focus

**The paper spine is closed.** The market-hours smoke passed 9/9 on the Dell
on 2026-07-15: intent → risk approval → Alpaca submission → live fill →
persistence → audit trail → clean reconciliation, all through the deployed
services. Card 16 is done and KI-003 is resolved. The next build work is the
Research middle loop and the consumer-groups infrastructure card.

## Main branch state

Merged on `main` through PR #35. Since the 2026-07-06 status:

1. PR #28–29: status reconciliation and doc-drift audit after the poison fix.
2. PR #30: Redis-backed order-rate guardrails in the pre-trade gate — daily
   cap + per-symbol cooldown, persisted across restarts (blunts the
   replay-reapproval hazard of KI-006).
3. PR #31: account snapshots — reconciliation publishes an account summary on
   the bus and persists per pass to `ops.account_snapshots`.
4. PR #32: poison-skip hardening extended to Paper Order Store, Audit Logger,
   and the shared `EventSubscriber` (completes the pattern from PR #27).
5. PR #33: first autonomous signal path — strategy fixture + decision maker
   service, **disarmed by default** (`STRATEGY_FIXTURE_ENABLED=false`).
6. PR #34–35: reconciliation lookback window (default 7 days) so pre-spine
   June orders don't flag as discrepancies forever, plus the percent-encoding
   fix for the Alpaca `after` timestamp (found live on 2026-07-15 when every
   reconciliation pass silently failed).

## Spine verification record

- **2026-07-08:** first live fill observed (AAPL x1 @ 313.33) — 8/9, the
  reconciliation check flagged a June-era order predating persistence.
- **2026-07-15:** 9/9 PASS — fill AAPL x1 @ 326.28, `reconciliation:
  clean=True discrepancies=0`. Spine closed.

## Open work

- **Consumer groups / persisted offsets card (KI-006):** agents replay full
  stream history on restart. Replay is now safe everywhere (PR #32) but
  wasteful; consumer groups with acknowledged offsets are the proper fix.
  Include retry-backoff for systemic errors. Was the deferred Month-1
  decision; now the top infrastructure card.
- **First autonomous trade (Mike's switch):** set
  `STRATEGY_FIXTURE_ENABLED=true` in `infra/.env`, rebuild
  `strategy-fixture` + `decision-maker`, and the fixture path produces the
  firm's first non-smoke trade through the full spine.
- **Regime threshold watch:** v0.1 calibration is single-day evidence. A
  historical feature backfill would earn the thresholds.

## Local credentials policy

Alpaca paper credentials live only in local ignored `infra/.env`.

- Do not print values.
- Do not commit values.
- Check only presence/length.
- If a key appears in chat or a log, rotate it.

## Next recommended card

Consumer groups / persisted offsets (retire KI-006), or strategy registry /
librarian schema (Research middle loop) — Mike's call on ordering.
