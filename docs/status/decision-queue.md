# Decision queue

**Last updated:** 2026-06-21T15:47:43-07:00

## Active decisions

### DQ-001 — NautilusTrader bridge boundary

**Question:** Does Month 1 completion require actual NautilusTrader routing, or is direct Alpaca paper acceptable for the first end-to-end paper spine?

**Current state:** The implemented spine submits directly to Alpaca paper through the paper-only client. `docs/02-architecture.md` and ADR-0003 still describe NautilusTrader-to-Redis bridge coverage as an unresolved architecture question.

**Recommendation:** Treat direct Alpaca paper as acceptable for the Month 1 smoke, but keep ADR-0003 open and schedule a dedicated bridge-validation card before calling the Trading Floor architecture settled.

### DQ-002 — Position state derivation boundary

**Question:** Should `trading.paper_order_events` remain append-only event history only, or should Card 13 derive current positions?

**Current state:** PR #13 added append-only paper order events. PR #14 adds the consumer core. No current-position table exists yet.

**Recommendation:** Keep Card 13 as Reconciliation/position-state design, not a hidden addition to Card 12 deployability.

### DQ-003 — Research start gate

**Question:** When do Research agents start?

**Current state:** Research specs exist, but no implementation exists. Mike explicitly agreed to finish the paper trading spine first.

**Recommendation:** Start Research only after: order-store service, reconciliation, full Docker stack smoke, and an explicit decision on the Nautilus bridge boundary.

## Deferred decisions

- Whether to use Redis consumer groups/ACKs during Month 1 or defer to Month 2.
- Whether the first live paper strategy seed comes from Mike's historical/manual setups or a minimal deterministic strategy fixture.
- Whether `Daily Briefing Agent` waits until reconciliation exists or starts earlier from audit/order events only.
