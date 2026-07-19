# Known issues

**Last updated:** 2026-07-18

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

**Status:** Resolved 2026-07-15. PR #37 moved all stream consumers to Redis consumer groups.

Stream consumers held their offsets in memory and read from `start_id=0-0` on restart, replaying the entire history — the cause of the 2026-07-06 poison-event incident. PR #27/#32 made replay safe (poison-skip); PR #30's rate guardrails blunted the replay-reapproval hazard; PR #37 fixed the root cause with consumer groups and acknowledged, persisted offsets (`src/shrap/events/groups.py`).

One residual, tracked in `current-sprint.md` open work: retry-backoff for systemic errors (broker/DB down) was scoped into this card's mitigation but did not ship in PR #37. The second residual (Dell running pre-#36 containers) was resolved by the 2026-07-17 upgrade session: full-stack rebuild through PR #45, consumer groups live in production.

## KI-007 — Pre-synthesis funnel rejections leave no persistent trace

**Status:** Open. Found 2026-07-18 during the v2 re-filter audit.

The Tech Watcher's rejection graveyard (`research.world_changers`, status
`rejected`) only records candidates that reach synthesis. A cluster killed
earlier by the two-source triangulation rule writes no row, and a re-filter
overwrites `filter_result` in place, so the prior prompt version's verdicts
are destroyed. Container logs were the only remaining record of the first
batch's six v1 keeps, and they did not survive the PR #49 redeploy.

Concrete cost: after the v2 re-filter (0/246 kept), the one borderline-real
v1 item could not be identified to audit whether v2 rejected it on principle
(economic-evidence rule) or misread it — the false-negative check the
re-filter comparison existed for. This violates "the denominator is never
hidden" and principle 8 (audit everything).

**Mitigation (candidate card):** persist cluster-stage rejections as
graveyard rows before synthesis, and retain filter verdict history per
prompt version (append, don't overwrite) so re-filter comparisons are
queryable after the fact.
