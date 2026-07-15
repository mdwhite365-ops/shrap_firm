# Known issues

**Last updated:** 2026-07-15

## KI-001 — Stacked PRs can be marked merged without reaching main

**Status:** Known workflow hazard. Recurred with PR #19 (Card 14), recovered by PR #20.

PR #10 was marked merged while its changes landed in the stacked base branch rather than `main`. PR #11 recovered the Card 8 changes onto `main`. The same failure repeated with PR #19, recovered by PR #20. Prefer independent branches off `main` over stacking.

**Mitigation:** After stacked PRs merge, verify main inclusion with:

```bash
git merge-base --is-ancestor <feature_commit> origin/main
```

Do not run live/deploy smoke until the feature commit is actually on `origin/main`.

## KI-002 — NautilusTrader bridge is still unresolved

**Status:** Resolved 2026-07-06 by ADR-0003 (Accepted).

Direct Alpaca paper access is the accepted broker interface for the paper phase. Broker credentials live only in the Execution Agent and Reconciliation Agent containers. NautilusTrader adoption is a gate triggered by live capital or by execution needs beyond market/day orders — see `docs/decisions/0003-nautilus-redis-bridge-coverage.md`.

## KI-003 — Fill event live path is not yet observed with a real fill

**Status:** Resolved 2026-07-15. Market-hours smoke passed 9/9 on the Dell.

Root cause found during Card 16: the Execution Agent checked order status exactly once, immediately after submission, so a fill landing later was never published. Pending-order re-polling (5s interval, publish on change) shipped in PR #22. The first live fill was observed 2026-07-08 (AAPL x1 @ 313.33); the full 9/9 close — `order-filled`, `fill-persisted`, and `reconciliation: clean=True` — landed 2026-07-15 after the lookback fixes (PR #34–35).

## KI-004 — Paper order persistence consumer is not packaged yet

**Status:** Resolved 2026-07-02. Card 12 packaged `shrap-paper-order-store` as a deployable service (PR #16): console script, `PAPER_ORDER_STORE_*` settings, Dockerfile, and Compose service are on `main`.

## KI-005 — Current position state and reconciliation do not exist yet

**Status:** Order-level reconciliation shipped (Cards 13–14); position state still deferred.

The Reconciliation Agent compares Alpaca paper orders against `trading.paper_order_events` on a 300s interval and publishes `operations.reconciliation-completed` / `-discrepancy`. Current-position derivation remains unimplemented; the order trail is still append-only history.

**Mitigation:** Position-state derivation becomes its own card when the first Research strategy needs portfolio state, or before live capital — whichever comes first.

## KI-006 — Agents replay full stream history on every restart

**Status:** Made safe (PR #27); proper fix pending.

Stream consumers (Execution Agent, Paper Order Store, Audit Logger) hold their offsets in memory and read from `start_id=0-0` on restart, replaying the entire history. This caused the 2026-07-06 incident: a container rebuild replayed an approved intent, Alpaca rejected the duplicate `client_order_id` (422), and the loop stalled forever on the poisoned event — blocking all subsequent orders until PR #27 taught it to skip duplicates and malformed events.

Replay is now safe everywhere but wasteful: PR #32 extended the poison-skip pattern to the Paper Order Store, Audit Logger, and the shared `EventSubscriber`, and PR #30's Redis-persisted rate guardrails (daily cap + per-symbol cooldown) blunt the replay-reapproval hazard at the risk gate.

**Mitigation:** Redis consumer groups with acknowledged, persisted offsets — the deferred Month-1 decision from the decision queue, now the top infrastructure card. Include retry-backoff for systemic errors (broker/DB down) in the same card.
