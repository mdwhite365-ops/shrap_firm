# Current sprint status

**Last updated:** 2026-06-21T15:47:43-07:00
**Phase:** Month 1 / paper-trading spine
**Operating mode:** Paper only. No real-money execution.

## Current focus

Finish the paper-trading spine before switching to Research agents.

The spine target is:

```text
strategy signal
  -> Decision Maker stub
  -> Pre-Trade Checker
  -> Execution Agent
  -> Alpaca paper order
  -> Alpaca paper status/fill
  -> PostgreSQL order event trail
  -> Reconciliation Agent
```

## Main branch state

Merged on `main` through PR #13:

1. Audit Logger and ADR-0006 event substrate.
2. Decision Maker wire stub.
3. Pre-Trade Checker risk gate and reliability fixes.
4. Pre-Trade Checker deployable service.
5. Paper Execution Agent core.
6. Execution Agent deployable service.
7. Alpaca paper order status/fill polling.
8. Full local paper-spine smoke harness.
9. Paper order/fill persistence schema and sink.

## Open work

PR #14 is open and mergeable:

- URL: https://github.com/mdwhite365-ops/shrap_firm/pull/14
- Title: `feat: consume paper order events`
- Scope: consume `execution.order.submitted`, `execution.order.status-updated`, and `execution.order.filled`, map them to `PaperOrderRecord`, and write through `PostgresPaperOrderSink`.

## Local credentials policy

Alpaca paper credentials live only in local ignored `infra/.env`.

- Do not print values.
- Do not commit values.
- Check only presence/length.
- If a key appears in chat or a log, rotate it.

## Next recommended card after PR #14 merges

Card 12 — package the Paper Order Store consumer as a deployable service.

Acceptance shape:

- `shrap-paper-order-store` console script.
- `PAPER_ORDER_STORE_*` settings.
- Dockerfile.
- Compose service.
- Config/deployability tests.
- No reconciliation or position derivation yet.
