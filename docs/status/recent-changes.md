# Recent changes

**Last updated:** 2026-07-15

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
- PR #28 — Status reconciliation after the poison fix (PRs #22–27).
- PR #29 — Doc-drift audit against deployed reality.
- PR #30 — Redis-backed order-rate guardrails in the pre-trade gate.
- PR #31 — Account snapshots published per reconciliation pass.
- PR #32 — Poison-skip hardening for Paper Order Store, Audit Logger, and
  `EventSubscriber`.
- PR #33 — First autonomous signal path: strategy fixture + decision maker
  service (disarmed by default).
- PR #34 — Reconciliation lookback window (default 7 days).
- PR #35 — Percent-encode the lookback timestamp in Alpaca order queries
  (found live 2026-07-15: the raw `+00:00` offset broke every pass).
- PR #36 — Spine close-out docs: Card 16 9/9, KI-003 resolved.
- PR #37 — All stream consumers moved to Redis consumer groups (KI-006).
- PR #38 — Strategy registry schema + lifecycle state machine — first
  Research middle-loop card. Draft Strategy Librarian spec included.

## Open

- No open implementation PRs. Next: Strategy Librarian service card
  (consume Evaluator verdicts, apply registry transitions, publish
  `research.strategy.*` lifecycle events).
- Dell redeploy pending: running containers predate PR #36 (`git pull` +
  full `docker compose up -d --build` after the pending SPY fill lands).

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
- **2026-07-08 (first live fill):** market-hours smoke reached 8/9 — first
  live `execution.order.filled` observed (AAPL x1 @ 313.33, KI-003 mechanism
  proven). Reconciliation flagged a June-era order predating persistence →
  lookback window (PR #34).
- **2026-07-15 (spine closed):** after merging PR #34 the smoke timed out on
  reconciliation — the raw RFC3339 `+00:00` in the Alpaca `after` query
  decoded to a space and every pass failed silently (PR #35). With the fix
  deployed: **9/9 PASS**, fill AAPL x1 @ 326.28, `reconciliation: clean=True
  discrepancies=0`. Card 16 closed; the paper spine is fully verified.
- **2026-07-15 (first autonomous signal):** Mike armed the strategy fixture
  (`STRATEGY_FIXTURE_ENABLED=true`). It fired immediately at 23:32 UTC —
  regime gate passed on `late-cycle-melt-up` — and the full chain ran with
  no human in the loop: signal → intent → risk approval → Alpaca submission
  (SPY buy x1, order `6315af3f`, ~5 seconds end to end). Market was closed;
  fill expected at the 2026-07-16 open via Card 16 re-polling. Plan: verify
  fill + clean reconciliation, then disarm the fixture — its job is done
  once the autonomous path is proven; the next thing to arm the trading
  path should be a real strategy promoted out of the registry.

## Security notes

- Old Alpaca paper key was rotated after appearing in chat.
- New credentials are local-only in ignored `infra/.env`.
- Do not print, commit, or paste Alpaca key/secret values.
