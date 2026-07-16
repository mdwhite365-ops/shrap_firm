# Paper spine roadmap tree

**Last updated:** 2026-07-15
**Principle:** Finish the paper-trading spine before Research implementation.
**Spine status: CLOSED** — market-hours smoke passed 9/9 on the Dell 2026-07-15.

## Tree outline

```text
Shrap paper-trading spine
├── 0. Foundations [done]
│   ├── ADR-0006 event envelope [done]
│   ├── Redis Streams helpers [done]
│   ├── Audit Logger -> ops.audit_events [done]
│   └── Health Monitor / basic ops substrate [done]
│
├── 1. Decision and risk path [done for Month 1]
│   ├── Decision Maker paper stub [done]
│   ├── Pre-Trade Checker risk gate [done]
│   ├── Pre-Trade reliability fixes [done]
│   └── Pre-Trade deployable service [done]
│
├── 2. Execution path [done]
│   ├── Execution Agent core [done]
│   ├── Execution Agent deployable service [done]
│   ├── Alpaca paper order submit [done]
│   ├── Alpaca paper status/fill polling [done]
│   ├── Pending-order re-polling until terminal [done, Card 16, PR #22]
│   └── Live fill observed [done, first fill 2026-07-08]
│
├── 3. Persistence path [done]
│   ├── Paper order event schema/sink [done, PR #13]
│   ├── Paper order event consumer core [done, PR #14]
│   ├── Paper Order Store deployable service [done, PR #16]
│   └── Full service-stack persistence smoke [done, Card 15 passed 2026-07-06]
│
├── 4. Reconciliation path [done at order level]
│   ├── Alpaca paper account/order snapshot client [done, PR #18]
│   ├── Compare broker orders vs trading.paper_order_events [done, PR #18]
│   ├── Emit operations.reconciliation-completed [done, PR #18]
│   ├── Emit operations.reconciliation-discrepancy [done, PR #18]
│   ├── Deployable service [done, PR #20]
│   ├── Account snapshots on the bus + per-pass persistence [done, PR #31]
│   ├── Lookback window for pre-spine broker history [done, PR #34–35]
│   └── Later: derive/check current positions [deferred, see KI-005]
│
├── 5. Operational closure [done for paper spine]
│   ├── Full Docker Compose paper-spine smoke [done, Card 15 passed 2026-07-06]
│   ├── Market-hours live fill smoke [done, Card 16 passed 9/9 2026-07-15]
│   ├── Audit trail verification across all streams [done, part of Card 15 smoke]
│   ├── State Manager status files from events [deferred to post-Research]
│   └── Daily briefing input readiness [deferred to Reporting implementation]
│
├── 6. Architecture decision closure [done]
│   ├── ADR-0003 decided: direct Alpaca accepted for paper phase [Card 17]
│   ├── Broker isolation restated: credentials only in broker-facing agents [ADR-0003]
│   └── NautilusTrader re-scoped as live-capital / advanced-execution gate [ADR-0003]
│
└── 7. Research unlock [OPEN — Mike accepted spine status 2026-07-06]
    ├── Regime Classifier minimal statistical implementation [done, Card 18, PR #24–26]
    ├── First autonomous signal path (fixture + decision maker) [done, PR #33 — armed 2026-07-15, first autonomous order submitted]
    ├── Strategy registry / librarian schema [done, PR #38]
    ├── Strategy Librarian service (verdict events → transitions → lifecycle events)
    ├── Hypothesis Generator seed path
    ├── Strategy Evaluator minimal backtest harness
    └── First strategy promotion into paper spine
```

## One-card sequence from here

### Card 12 — Package Paper Order Store service [done, PR #16]

### Card 13 — Reconciliation Agent core

**Acceptance:**

- Read Alpaca paper account/orders through paper-only client.
- Read persisted `trading.paper_order_events` through a narrow repository interface.
- Compare expected broker order IDs/statuses.
- Publish reconciliation completed/discrepancy events through ADR-0006.
- Unit tests with fake broker and fake order repository.

### Card 14 — Reconciliation deployability

**Acceptance:**

- `shrap-reconciliation-agent` console script.
- `RECONCILIATION_AGENT_*` settings.
- Dockerfile.
- Compose service.
- No scheduler complexity beyond a simple interval.

### Card 15 — Full Docker Compose paper-spine smoke

**Acceptance:**

- Redis + Postgres + Audit Logger + Pre-Trade Checker + Execution Agent + Paper Order Store run together.
- Inject one paper signal.
- Verify Redis streams.
- Verify `ops.audit_events` rows.
- Verify `trading.paper_order_events` rows.

### Card 16 — Market-hours fill smoke [done, 9/9 on the Dell 2026-07-15]

**Acceptance:**

- Submit paper order expected to fill.
- Observe `execution.order.filled`.
- Verify persisted fill row.
- Verify reconciliation sees no discrepancy.

### Card 17 — ADR-0003 Nautilus bridge validation

**Acceptance:**

- Decide whether direct Alpaca paper is a Month 1 exception or architecture change.
- Document adapter coverage gaps.
- Update roadmap/architecture if needed.

### Card 18 — Research implementation starts

**Acceptance:**

- Only start after Mike explicitly accepts paper spine status.
- Begin with deterministic/statistical Regime Classifier or Strategy Registry, not LLM-heavy hypothesis generation.
