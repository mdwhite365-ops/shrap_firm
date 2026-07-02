# Recent changes

**Last updated:** 2026-07-02

## Merged since the inner-loop paper spine push began

- PR #7 — Pre-Trade Checker deployability.
- PR #8 — Paper Execution Agent core.
- PR #9 — Execution Agent deployability.
- PR #11 — Alpaca paper order status/fill polling recovery onto `main`.
- PR #12 — Full local paper-spine smoke harness.
- PR #13 — Paper order/fill persistence schema and sink.
- PR #14 — Paper order-event persistence consumer core.
- PR #15 — Status/audit/roadmap reconciliation after PR #14.
- PR #16 — Paper Order Store deployable service (Card 12).

## Open

- No open implementation PRs. Card 13 (Reconciliation Agent core) is next.

## Live smoke notes

A live Alpaca paper account/order smoke succeeded after credentials were staged locally in ignored `infra/.env`:

- Account reachable and active.
- AAPL paper market buy qty 1 was accepted.
- Order status lookup returned accepted with `filled_qty=0`.
- Card 8 status-event path emitted `execution.order.status-updated` with correct correlation.

The first live `execution.order.filled` event is still pending a real fill.

## Security notes

- Old Alpaca paper key was rotated after appearing in chat.
- New credentials are local-only in ignored `infra/.env`.
- Do not print, commit, or paste Alpaca key/secret values.
