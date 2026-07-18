# Decision queue

**Last updated:** 2026-07-18

## Active decisions

### DQ-004 — Universe lock-in

**Question:** Confirm or revise the proposed 50-name list in `docs/universe/README.md`.

**Current state:** The list is drafted and treated as locked by the constraints, but Mike has not explicitly signed off on the names. No deployed agent consumes the universe yet, so there is no operational forcing function — the Hypothesis Generator will be the first consumer.

**Recommendation:** Rule before the Hypothesis Generator card starts.

### DQ-005 — Regime Classifier calibration ownership

**Question:** Who owns the thresholds/sizing bands in `src/shrap/intelligence/regime/profiles.py`, and how are they earned rather than guessed?

**Current state:** v0.1 values are single-day calibrations (PR #26). The spec's open questions (debounce M, epsilon, band derivation) are implemented as defaults pending Mike's ruling. A historical feature backfill would let the thresholds be derived from evidence.

**Recommendation:** Schedule the backfill card before any strategy consumes regime-conditional sizing.

### DQ-006 — Cloud tier for the research filter

**Question:** Is local Qwen (`qwen3.5:9b` on the 2070 Super) good enough for the Tech Watcher filter, or does the filter stage need a cloud tier?

**Current state:** First live batch under prompt v1 over-flagged (6 kept, ~1 real), diagnosed as a prompt gap and fixed in PR #49. The honest datapoint is the residual error rate of the v2 re-filter over the 246-item baseline, which has not run yet.

**Recommendation:** Defer until the v2 re-filter result is in; decide on evidence, not anecdote.

## Resolved decisions

- **DQ-001 — NautilusTrader bridge boundary.** Resolved 2026-07-06 by ADR-0003
  (Accepted): direct Alpaca paper is the broker interface for the paper phase;
  Nautilus is re-gated on live capital or execution needs beyond market/day orders.
- **DQ-002 — Position state derivation boundary.** Resolved by the Cards 13–14
  split: order-level reconciliation shipped; position-state derivation is
  deferred and tracked as KI-005.
- **DQ-003 — Research start gate.** Resolved 2026-07-06: Mike accepted spine
  status and opened the Research unlock. The spine itself closed 9/9 on 2026-07-15.
- **Consumer groups (was deferred).** Resolved by PR #37 — all stream consumers
  moved to Redis consumer groups with persisted offsets (KI-006).
- **First strategy seed (was deferred).** Resolved by PR #33 — a minimal
  deterministic strategy fixture, armed once for the first autonomous trade
  (2026-07-15/16) and disarmed since.

## Deferred decisions

- Whether the Daily Briefing Agent waits until Reporting implementation or
  starts earlier from audit/order events only (unchanged; deferred to
  post-Research per the roadmap).
