# Current sprint status

**Last updated:** 2026-07-06 (evening)
**Phase:** Month 2 / spine verified → Research implementation
**Operating mode:** Paper only. No real-money execution.

## Current focus

The paper spine is deployed, verified end to end, and hardened against the
first production incident. The Regime Classifier (first Research-unlock
agent) is live and producing labels. One verification remains market-hours
gated; the next build work is the Research middle loop.

## Main branch state

Merged on `main` through PR #27. Everything from the paper spine push plus:

1. Cards 13–14: Reconciliation Agent core + deployable service.
2. Card 15: `shrap-spine-smoke` live compose smoke — **passed 6/6 on the Dell
   2026-07-06, twice** (once before and once after the poison-event fix).
3. Card 16 enabler (PR #22): pending-order re-polling until terminal status.
4. Card 17 (PR #23): ADR-0003 accepted — direct Alpaca is the paper-phase
   broker interface; credentials confined to broker-facing agent containers.
5. Card 18 (PR #24–26): Regime Classifier statistical layer deployed —
   daily-bars ingestion, 7-feature vector, rule-based profiles calibrated
   v0.1 against live readings. Produced the firm's first debounced regime
   transition (`unknown → crisis-recovery`, 2026-07-06 19:04 UTC).
6. PR #27: Execution Agent poison-event handling — restart replay of
   duplicate orders no longer stalls the trading path (found live, fixed,
   re-verified same day).

## Open work

- **Card 16 closure (market hours):** run
  `sudo docker compose exec paper-order-store shrap-spine-smoke --wait-fill --wait-reconciliation`
  during 09:30–16:00 ET. Nine PASS lines closes KI-003 and fully verifies the
  spine. Tip: run inside `tmux` — two runs have died to SSH disconnects.
- **Consumer groups / persisted offsets card:** agents replay full stream
  history on restart (in-memory offsets). PR #27 made replay safe; consumer
  groups are the proper fix. Was the deferred Month-1 decision; now due.
- **Regime threshold watch:** v0.1 calibration is single-day evidence. A
  historical feature backfill (compute the 7 features across the stored
  bar window, eyeball distributions per era) would earn the thresholds.

## Local credentials policy

Alpaca paper credentials live only in local ignored `infra/.env`.

- Do not print values.
- Do not commit values.
- Check only presence/length.
- If a key appears in chat or a log, rotate it.

## Next recommended card

Strategy registry / librarian schema (Research middle loop), or the
consumer-groups infrastructure card — Mike's call on ordering.
