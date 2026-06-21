# Known issues

**Last updated:** 2026-06-21T15:47:43-07:00

## KI-001 — Stacked PRs can be marked merged without reaching main

**Status:** Known workflow hazard.

PR #10 was marked merged while its changes landed in the stacked base branch rather than `main`. PR #11 recovered the Card 8 changes onto `main`.

**Mitigation:** After stacked PRs merge, verify main inclusion with:

```bash
git merge-base --is-ancestor <feature_commit> origin/main
```

Do not run live/deploy smoke until the feature commit is actually on `origin/main`.

## KI-002 — NautilusTrader bridge is still unresolved

**Status:** Open architectural decision.

The current Month 1 paper spine uses direct Alpaca paper order submission. Architecture docs still expect NautilusTrader as the execution interface. ADR-0003 remains the place to resolve bridge coverage.

**Mitigation:** Add a focused ADR-0003 validation card after the paper service stack is running.

## KI-003 — Fill event live path is not yet observed with a real fill

**Status:** Partially verified.

Unit tests cover `execution.order.filled`; live Alpaca smoke observed `execution.order.status-updated` for an accepted order with `filled_qty=0`.

**Mitigation:** Run a live paper fill during market hours or with an instrument/order type likely to fill in paper, then verify `execution.order.filled` and persistence.

## KI-004 — Paper order persistence consumer is not packaged yet

**Status:** Consumer core merged in PR #14; service packaging still missing.

`PostgresPaperOrderSink` and the paper order-event consumer core exist on `main`, but there is no Docker/Compose service yet.

**Mitigation:** Card 12 should package `shrap-paper-order-store` as a service.

## KI-005 — Current position state and reconciliation do not exist yet

**Status:** Not implemented.

`trading.paper_order_events` is an append-only order event trail, not current portfolio state.

**Mitigation:** Reconciliation Agent should compare Alpaca paper account/orders against persisted order events and produce discrepancy events before Research is started.
