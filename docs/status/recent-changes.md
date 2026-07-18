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
- PR #39 — Status reconciliation: middle loop open, KI-006 resolved,
  first autonomous order recorded.
- PR #40 — Strategy Librarian deployable service: verdict events →
  registry transitions → `research.strategy.*` lifecycle events. Idles
  until an Evaluator exists; safe to deploy now.
- PR #41 — Status + Evaluator ruling: Framework #1 before the Evaluator;
  in-house walk-forward engine (VectorBT PRO re-gated).
- PR #42 — LLM tier client: ADR-0009 registry-driven wrapper, Ollama
  backend, cloud tiers fail loudly until billing exists.
- PR #43 — Registry seed correction: `qwen2.5:9b` never existed →
  `qwen3.5:9b-q4_K_M`; Ollama image pin bumped off 2024-era 0.3.12.
- PR #44 — Dell GPU swap docs (GTX 1080 → RTX 2070 Super) + first
  autonomous fill recorded.
- PR #45 — Ollama pin drift committed: 0.32.0 deployed and GPU-verified
  (CUDA needs driver 570+; driver 550 = silent CPU-only fallback).

## Open

- No open implementation PRs. Next: Tech Watcher seed (Framework #1
  opener, first LLM-calling agent, local-only on the tier client).
  Design note carried from the field: qwen3.5 is a thinking model — the
  tier client wants a `think: false` toggle for bulk classification.
- Dell is current through PR #45 (2026-07-17): full stack rebuilt on
  consumer groups + librarian, RTX 2070 Super verified serving
  `qwen3.5:9b-q4_K_M` on GPU, fixture disarmed.

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
  the order queued at Alpaca overnight via Card 16 re-polling.
- **2026-07-16 (first autonomous fill):** the SPY order filled at the open —
  the firm's first trade with no human anywhere in the loop, signal through
  fill.
- **2026-07-17 (upgrade session):** fixture disarmed, full-stack rebuild
  (PRs #36–45: consumer groups, librarian, ollama 0.32.0), GTX 1080 →
  RTX 2070 Super per the hardware-doc procedure. GPU inference verified:
  CUDA compute=7.5, `qwen3.5:9b` at 85% GPU util / 6.5 GB VRAM. Found:
  the 1080 host (driver 550) had been silently CPU-only under ollama
  0.32 — the swap fixed inference, not just speed. Post-rebuild spine
  smoke ran after hours (16:59 ET): submission → persistence → audit
  passed on the new stack; the order queued at Alpaca and the fill +
  clean-reconciliation close-out lands at the Monday 2026-07-20 open.

## Security notes

- Old Alpaca paper key was rotated after appearing in chat.
- New credentials are local-only in ignored `infra/.env`.
- Do not print, commit, or paste Alpaca key/secret values.
