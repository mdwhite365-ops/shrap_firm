# Recent changes

**Last updated:** 2026-07-06

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
- PR #17 — Status reconciliation after Card 12.
- PR #18 — Reconciliation Agent core (Card 13).
- PR #20 — Reconciliation Agent deployable service (Card 14; recovered after
  PR #19 hit the KI-001 stacking trap).
- PR #21 — Live compose-stack spine smoke tool `shrap-spine-smoke` (Card 15).
- PR #22 — Execution Agent pending-order re-polling (Card 16 enabler, KI-003).
- PR #23 — ADR-0003 resolved: direct Alpaca accepted for paper phase (Card 17).
- PR #24 — Regime Classifier statistical layer (Card 18) — first Research-unlock agent.
- PR #25 — Dell compose drift committed; per-tick feature/profile logging.
- PR #26 — Regime vol-threshold calibration v0.1 (melt-up/crisis-recovery adjoin at 0.18).
- PR #27 — Execution Agent poison-event handling (fixed the post-restart replay stall).

## Open

- No open implementation PRs. Next: consumer groups / persisted offsets card
  (replaces full-history replay on restart), then strategy registry schema.

## Live smoke notes

- **2026-07-06 (first full-stack run):** Card 15 smoke PASSED 6/6 on the Dell —
  intent → risk approval → Alpaca submission → status → `trading.paper_order_events`
  → `ops.audit_events`, all through the deployed services.
- **2026-07-06 (later):** container rebuild exposed the poison-event stall
  (restart replay re-submitted a duplicate order, Alpaca 422, loop stuck).
  Fixed in PR #27 and re-verified live: fresh smoke passed 6/6 through
  submission/persistence/audit after the fix.
- **Regime Classifier live:** backfilled 2,466 daily bars, computed all 7
  features, and produced the firm's first debounced regime transition
  (`unknown → crisis-recovery`, 19:04 UTC, confidence 0.67).
- The first live `execution.order.filled` event is still pending a
  market-hours run: `shrap-spine-smoke --wait-fill --wait-reconciliation`.

## Security notes

- Old Alpaca paper key was rotated after appearing in chat.
- New credentials are local-only in ignored `infra/.env`.
- Do not print, commit, or paste Alpaca key/secret values.
