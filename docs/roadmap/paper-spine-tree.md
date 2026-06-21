# Paper spine roadmap tree

**Last updated:** 2026-06-21T15:47:43-07:00
**Principle:** Finish the paper-trading spine before Research implementation.

## Tree outline

```text
Shrap paper-trading spine
├── 0. Foundations [mostly done]
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
├── 2. Execution path [mostly done]
│   ├── Execution Agent core [done]
│   ├── Execution Agent deployable service [done]
│   ├── Alpaca paper order submit [done]
│   ├── Alpaca paper status/fill polling [done]
│   └── Live paper smoke [partial: accepted/status observed, fill not live-observed]
│
├── 3. Persistence path [in progress]
│   ├── Paper order event schema/sink [done, PR #13]
│   ├── Paper order event consumer core [open, PR #14]
│   ├── Paper Order Store deployable service [next]
│   └── Full service-stack persistence smoke [next+1]
│
├── 4. Reconciliation path [not started]
│   ├── Alpaca paper account/order snapshot client
│   ├── Compare broker orders vs trading.paper_order_events
│   ├── Emit operations.reconciliation-completed
│   ├── Emit operations.reconciliation-discrepancy
│   └── Later: derive/check current positions
│
├── 5. Operational closure [not started]
│   ├── Full Docker Compose paper-spine smoke
│   ├── Market-hours live fill smoke
│   ├── Audit trail verification across all streams
│   ├── State Manager status files from events
│   └── Daily briefing input readiness
│
├── 6. Architecture decision closure [not started]
│   ├── ADR-0003 NautilusTrader bridge coverage validation
│   ├── Decide direct-Alpaca Month 1 exception vs immediate Nautilus bridge
│   ├── Document broker isolation implications
│   └── Schedule bridge implementation if required
│
└── 7. Research unlock [blocked until 3-6 are acceptable]
    ├── Regime Classifier minimal statistical implementation
    ├── Strategy registry / librarian schema
    ├── Hypothesis Generator seed path
    ├── Strategy Evaluator minimal backtest harness
    └── First strategy promotion into paper spine
```

## One-card sequence from here

### Card 12 — Package Paper Order Store service

**Depends on:** PR #14 merged.

**Acceptance:**

- `shrap-paper-order-store` console script.
- `PAPER_ORDER_STORE_*` settings.
- Dockerfile.
- Compose service.
- Config/deployability tests.
- No reconciliation or position derivation.

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

### Card 16 — Market-hours fill smoke

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
