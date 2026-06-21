# Paper spine audit — 2026-06-21

**Audit date:** 2026-06-21T15:47:43-07:00
**Scope:** Month 1 paper-trading spine through PR #13 on `main`, with PR #14 open.
**Mode:** Paper only. No real-money path reviewed or approved.

## Executive summary

The paper spine has moved from isolated components to a locally composable event path:

```text
strategy signal -> decision intent -> risk approval -> paper order submission -> order status/fill -> paper order event trail
```

The strongest parts are the deterministic event contracts, paper-only Alpaca guardrails, unit/integration coverage, and append-only persistence design. The main remaining gaps before Research should start are service packaging for order persistence, reconciliation against Alpaca paper, full Docker stack smoke, and the unresolved NautilusTrader bridge decision.

## Current implemented path on main

| Segment | Status | Evidence |
|---|---|---|
| ADR-0006 event envelope | Implemented | `src/shrap/events/__init__.py` and event tests. |
| Audit Logger | Implemented | `ops.audit_events` sink and service. |
| Decision Maker stub | Implemented | emits paper `trading.decision.intent`. |
| Pre-Trade Checker | Implemented + packaged | risk gate, reliability fixes, service packaging. |
| Execution Agent | Implemented + packaged | submits Alpaca paper orders, status/fill polling. |
| Local end-to-end paper smoke | Implemented | `tests/integration/test_paper_spine_smoke.py`. |
| Paper order event persistence | Implemented | `trading.paper_order_events` schema/sink. |
| Paper order event consumer | Open PR | PR #14, not yet on `main`. |

## Quality snapshot

Most recent broad gate for PR #14 branch:

```text
81 passed
ruff check: passed
ruff format --check: passed
mypy: passed, 43 source files
```

Most recent `main` includes PR #13 and previously passed full suite for the paper spine through order persistence.

## Paper-only guardrails

- Alpaca settings reject non-paper hosts.
- Execution Agent refuses non-paper approved intents.
- Credentials are stored only in ignored `infra/.env`.
- Secret values are not printed; presence/length checks only.
- Live smoke used Alpaca paper and old key was rotated afterward.

## Audit findings

### A1 — Month 1 paper spine is close, but not operationally closed

The code path is locally composable and live Alpaca account/order/status calls were proven, but the full service topology is not yet complete.

**Impact:** Medium. The paper spine is not yet a deploy-and-watch system.

**Next action:** Merge PR #14, package the Paper Order Store service, then run Docker Compose stack smoke.

### A2 — NautilusTrader bridge remains unresolved

The implemented execution path uses direct Alpaca paper. Roadmap/architecture still mention NautilusTrader as the execution interface and ADR-0003 remains open.

**Impact:** High for architecture accuracy, low for immediate paper smoke.

**Next action:** Add a focused Nautilus bridge validation/decision card after the service stack is running.

### A3 — No current-position state exists yet

`trading.paper_order_events` is an event trail, not a position ledger.

**Impact:** Medium. Reconciliation cannot yet compare derived positions, only order events.

**Next action:** Reconciliation Agent should start by comparing Alpaca orders to persisted order events, then later derive positions.

### A4 — Filled live path is unit-tested but not live-observed

Live Alpaca smoke got `status=accepted` and `filled_qty=0`; it did not observe a fill.

**Impact:** Low/Medium. Status path is live-proven; fill stream needs live confirmation.

**Next action:** Run a market-hours fill smoke after order persistence consumer is packaged.

### A5 — Research is not ready to start implementation

Research docs/specs exist, but the operational paper spine still has higher-value gaps.

**Impact:** High if focus shifts early.

**Next action:** Finish service packaging, reconciliation, Docker stack smoke, and ADR-0003 decision before coding Research agents.

## Recommended next cards

1. Card 12 — Package Paper Order Store service.
2. Card 13 — Reconciliation Agent core against Alpaca paper orders/account.
3. Card 14 — Full Docker Compose paper-spine smoke.
4. Card 15 — Live market-hours fill smoke and persistence verification.
5. Card 16 — ADR-0003 NautilusTrader bridge validation/decision.
6. Card 17 — Minimal State Manager / daily status files from audit/order events.
7. Card 18 — Then start Research implementation.
