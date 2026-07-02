# Current sprint status

**Last updated:** 2026-07-02
**Phase:** Month 2 / paper-trading spine
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

Merged on `main` through PR #16:

1. Audit Logger and ADR-0006 event substrate.
2. Decision Maker wire stub.
3. Pre-Trade Checker risk gate and reliability fixes.
4. Pre-Trade Checker deployable service.
5. Paper Execution Agent core.
6. Execution Agent deployable service.
7. Alpaca paper order status/fill polling.
8. Full local paper-spine smoke harness.
9. Paper order/fill persistence schema and sink.
10. Paper order-event persistence consumer core.
11. Paper Order Store deployable service (Card 12, PR #16).

## Open work

Card 13 (Reconciliation Agent core) is the active card.

## Local credentials policy

Alpaca paper credentials live only in local ignored `infra/.env`.

- Do not print values.
- Do not commit values.
- Check only presence/length.
- If a key appears in chat or a log, rotate it.

## Next recommended card

Card 13 — Reconciliation Agent core.

Acceptance shape:

- Read Alpaca paper account/orders through the paper-only client.
- Read persisted `trading.paper_order_events` through a narrow repository interface.
- Compare expected broker order IDs/statuses.
- Publish reconciliation completed/discrepancy events through ADR-0006.
- Unit tests with fake broker and fake order repository.
- No service packaging yet (that is Card 14).
