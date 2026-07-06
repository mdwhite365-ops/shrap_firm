# Current sprint status

**Last updated:** 2026-07-06
**Phase:** Month 2 / paper spine closure → Research unlock
**Operating mode:** Paper only. No real-money execution.

## Current focus

Close out the paper spine (live fill smoke) and start Research implementation
(Card 18: Regime Classifier).

The spine passed its full-stack compose smoke on the Dell on 2026-07-06
(6/6 checks: intent → risk approval → submission → status → persistence →
audit trail). Card 15 is done.

## Main branch state

Merged on `main` through PR #21:

1. Audit Logger and ADR-0006 event substrate.
2. Decision Maker wire stub.
3. Pre-Trade Checker risk gate, reliability fixes, deployable service.
4. Paper Execution Agent core and deployable service.
5. Alpaca paper order submit/status/fill polling.
6. Full local paper-spine smoke harness.
7. Paper order/fill persistence schema, sink, consumer, deployable service.
8. Reconciliation Agent core (Card 13, PR #18) and deployable service
   (Card 14, PR #20 — recovered after PR #19 hit the KI-001 stacking trap).
9. Live compose-stack smoke tool `shrap-spine-smoke` + runbook
   (Card 15, PR #21). **Passed on the Dell 2026-07-06.**

## Open work

- **Card 16 (PR #22):** Execution Agent pending-order re-polling — fixes the
  root cause of KI-003 (status was checked exactly once per order). After
  merge, run during market hours:
  `docker compose exec paper-order-store shrap-spine-smoke --wait-fill --wait-reconciliation`
- **Card 17:** ADR-0003 resolution (this PR) — direct Alpaca paper accepted
  for the paper phase; NautilusTrader re-scoped as a live-capital /
  advanced-execution gate.
- **Card 18:** Research implementation begins — deterministic Regime
  Classifier first.

## Local credentials policy

Alpaca paper credentials live only in local ignored `infra/.env`.

- Do not print values.
- Do not commit values.
- Check only presence/length.
- If a key appears in chat or a log, rotate it.

## Next recommended card

Card 18 — Regime Classifier, minimal statistical implementation
(deterministic, no LLM), per `docs/agents/intelligence/regime-classifier.md`.
