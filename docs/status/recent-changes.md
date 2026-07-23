# Recent changes

**Last updated:** 2026-07-23 (afternoon)

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

- PR #46 — Status close-out of the 2026-07-17 upgrade session.
- PR #47 — Tech Watcher ingest slice: EDGAR + arXiv Atom pulls into
  `research.raw_source_items` with atomic cursor advance, heartbeats,
  single-source failure isolation.
- PR #48 — Tech Watcher synthesis slice: local bulk filter
  (`think:false`), archetype clustering with the two-source
  triangulation rule, strict-schema candidate synthesis + deterministic
  validator, rejection graveyard, `shrap-tech-watcher-review` page.
- PR #49 — Filter prompt v2 after the first live batch: full recognition
  grammar (signature signals + impostor lists) in the prompt,
  economic-evidence hard rule, prompt-version stamping.
- PR #50 — Status close-out: funnel live, first pipeline run, calibration.
- PR #51 — Doc-drift reconciliation + v2 re-filter results (0/246 kept,
  spot-check passed) + KI-007 (pre-synthesis rejections leave no trace).
- PR #52 — 2026-07-18 reorder ruling (DQ-007): widen the web before
  deepening the funnel; gov sources + Intelligence Dept pulled forward.
- PR #53 — Gov-sources ingest: USASpending awards (DOE + DoD, $5M floor,
  30-day lookback) + DOE newsroom RSS as Tech Watcher source classes;
  filter prompt v3 (item types widened). SAM.gov deferred on API key.
- PR #54 — Promotion workflow: `shrap-tech-watcher-promote`
  promote/kill/seed CLI; promoted/killed events; decided_at +
  decision_note columns; review page shows promoted + kill graveyard.
- PR #55 — Status close-out of the 2026-07-18 session: gov sources
  deployed, first Mike-seed live.
- PR #56 — Market Phase Scheduler: deterministic XNYS calendar clock
  publishing `operations.market-phase` (pre-open/open/after-hours/
  overnight/closed-day; `pandas-market-calendars`, DST-tested). Deploy +
  weekend certification pending; consumers come in later cards.
- PR #57 — ADR-0012 accepted: tiered universe — Discovery (market-wide),
  Watch (evidence-gated, not tradeable), Active (hard-capped 50,
  Mike-approved). Tier transitions become bus events; Pre-Trade Tier 3
  check is a follow-up card. Motivated by the RKLB/Iridium hand-run
  analysis (2026-07-19 handoff).
- PR #58 — Status refresh recording #56–57.
- PR #59 — Regulator leg via the Federal Register API (NRC at launch,
  agency filter is config). Rechecked live before building: nrc.gov RSS
  is Akamai bot-blocked to every non-browser client tested, so the FR
  API carries the regulator's substantive paper trail instead — license
  applications/renewals, rules, notices.
- PR #60 — KI-007 fix: append-only `research.filter_verdict_history`
  (prompt-version-stamped, written before the in-place mark) +
  `research.tech_watcher_cluster_log` (every cluster's disposition
  logged before any synthesis LLM call).
- PR #61 — Source-class independence taxonomy v1 (spec): triangulation
  keys on originating institution (issuer / research / gov:<agency>)
  with hard/soft classes; promotable = >=2 origins + >=1 hard leg.
- PR #62 — News Analyzer spec (Intelligence Month 2 seed #1): Alpaca
  news vendor accepted; materiality-only signals on
  `intelligence.signal`; market-phase-driven cadence; no direction
  hints in v1.
- PR #63 — Taxonomy enforced in code: `derive_origin` from ingest
  payloads, new `Cluster.promotable` predicate, unmapped origins never
  count. DOE press + DOE award can no longer fake triangulation
  (regression-tested).
- PR #65 — News Analyzer service: materiality-scored signals on
  `intelligence.signal`, local scoring (`local-classification`) with
  cloud escalation (`cloud-default`) for material items, market-phase-
  driven cadence, append-only `intelligence.news_verdict_history`
  (KI-007), placeholder nine-symbol set (SPY/QQQ/IWM/HYG/TLT/AAPL/NVDA/
  TSLA/LMT — the Regime Classifier's default) pending Tier 3 state.
- PR #66 — Filing Processor spec (Intelligence Month 2 seed #2): Tier 3
  8-K full-text fetch from EDGAR, per-item-code materiality scoring with
  item-code priors, `signal_type: "filing"`.
- PR #67 — Universe README restructured around ADR-0012's three tiers;
  the 50-name list reframed as the Tier 3 launch proposal, still
  awaiting DQ-004 lock-in.
- PR #68 — Filing Processor service: implements the #66 spec against a
  placeholder AAPL/NVDA/TSLA/LMT roster (CIK-keyed); introduced the
  shared `src/shrap/intelligence/market_phase.py` helpers, which the
  News Analyzer now imports too (its container needs recreating at the
  next deploy).
- PR #69 — Universe Curator spec rewritten from derived-only consumer to
  Tier 2/3 owner + transition-event publisher (ADR-0012). Accepted by
  merge: `research.universe_tiers` as the Tier 3 store, events-as-history
  via the Audit Logger, no auto-add path, eviction lands back in
  Discovery. Open question on record: only 6 of the 50 launch names have
  behavioral profiles — grandfather-or-gate ruling pending.
- PR #70 — Pre-Trade Checker Tier 3 membership check (ADR-0012):
  flag-gated on `PRE_TRADE_CHECKER_TIER3_ENFORCEMENT` (default false),
  fail-closed (`TIER3_STATE_UNAVAILABLE` on any query failure, never
  cached), tier literal `'active'` pinned for the Curator's first
  implementation card to match, gated ahead of the rate guardrails. The
  checker gained an asyncpg pool + DSN setting. **Do not flip the flag**
  until the Curator's launch-list load populates
  `research.universe_tiers` — flipping now vetoes every order, including
  the smoke.
- PR #71 — Filing Processor backfill CLI (deferred from #68):
  `shrap-filing-processor-backfill`, docker-exec pattern on the
  `shrap-tech-watcher-promote` precedent; `--rescore` appends new
  verdict-history rows rather than overwriting (KI-007).

## Open

- Next cards: the **Universe Curator service card** (first
  implementation: `research.universe_tiers` + `research.universe_staging`
  stores, the four transition events, the Mike approval CLI, and the
  launch-list load) is blocked on Mike — DQ-004 lock-in and the 6-of-50
  profile-coverage ruling (Universe Curator spec, open questions) both
  gate it, and it in turn gates flipping
  `PRE_TRADE_CHECKER_TIER3_ENFORCEMENT`. Infrastructure Mapper is next in
  the prior queue behind it.
- Dell rebuild: the #56–63 session (tech-watcher FR source/KI-007
  tables/taxonomy rule + market-phase new service) deployed 2026-07-19;
  market-phase has already shown it survives a restart, and weekend
  certification (`closed-day` Sat/Sun, `pre-open` Monday) is due
  2026-07-25/26. A new session is now pending for #65–71: force-recreate
  `filing-processor` (new), `pre-trade-checker` (asyncpg pool), and
  `news-analyzer` (picks up the shared `market_phase` import) — one
  session; the Tier 3 enforcement flag stays off regardless.

## Funnel candidate log

- **2026-07-18 (first Mike-seed):** `Mass-manufactured fission cost-curve
  crossing` — `01KXVVPXDMB4HS1QNRPQWRP1RX`, archetype cost-curve,
  source_class `mike-seed`, falsifier horizon 2027-12. Kill criteria:
  no unsubsidized hyperscaler/industrial nuclear PPA by horizon;
  nth-of-a-kind $/kW flattens across two vendor cohorts; NRC/DOE
  licensing throughput regresses to pre-2025 rates for two consecutive
  quarters. Motivating case: Valar Atomics Ward 250 criticality
  (DOE Reactor Pilot Program, 2026-06-18).

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

## Research funnel notes

- **2026-07-17 (first full pipeline run):** ingest 246 items (146 EDGAR,
  100 arXiv) → filter kept 6 (2.4%) → 1 cluster, **0 promotable** — all
  six were arXiv-only, so the two-source triangulation rule held and no
  candidate was fabricated. Zero synthesis calls spent.
- **Calibration finding:** of the 6 flagged, ~5 were false positives
  (4 ML methods papers + 1 neuromorphic paper that the archetype doc's
  own impostor list names). Root cause: the v1 filter prompt carried
  definitions only — the model was never shown the impostor lists.
  Fixed in PR #49 (full recognition grammar in the prompt); verdict on
  Qwen's quality deferred until the v2 re-filter shows the residual
  error rate. Defense in depth worked as designed: the over-permissive
  filter cost six wasted rows, not a bad proposal.
- **2026-07-18 (v2 re-filter):** all 246 baseline items re-filtered under
  prompt v2 — **0 kept**. The impostor-list false positives are eliminated,
  consistent with the prompt-gap diagnosis. But the comparison's key check —
  did v2 reject the one borderline-real v1 item on principle or by mistake —
  proved unauditable: the re-filter overwrote the v1 verdicts, the
  triangulation-stage rejection never wrote a graveyard row, and the PR #49
  redeploy discarded the container logs holding the v1 keep list (KI-007).
  DQ-006 stays open on spot-check + future live-batch evidence.
- **2026-07-18 (spot-check):** 10 random v2 rejection reasons reviewed —
  all coherent; boilerplate 8-Ks correctly identified, and two ML-methods
  arXiv papers (the exact impostor class that fooled v1) rejected with the
  right archetype and the economic-evidence rule cited. Supports the
  prompt-gap diagnosis; the false-negative direction remains untested.

## Security notes

- Old Alpaca paper key was rotated after appearing in chat.
- New credentials are local-only in ignored `infra/.env`.
- Do not print, commit, or paste Alpaca key/secret values.
