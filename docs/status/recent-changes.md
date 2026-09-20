# Recent changes

**Last updated:** 2026-09-18 (`main` at #247 — the intraday path closed end to end, three silent infrastructure faults found and fixed, and IEX vs SIP measured to a negative result)

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

### Backfilled 2026-07-27 — the #72–80 gap

These nine PRs merged on 2026-07-23/24 and were never written up here. The
omission had a cost: the session-handoff command chain for the Evaluator pivot
was drafted without them and named services, tickers, and prerequisites that
the shipped code contradicts. Recorded now so the next chain is drafted against
what exists.

- PR #72 — Status closeout: 07-20 smoke fill confirmed (SPY @ 747.85, full
  chain), #56–63 rebuild recorded with the `--force-recreate` lesson.
- PR #73 — **Mike ruling 2026-07-23: Evaluator resequenced ahead of the
  Mapper/Scout.** The decision that makes the current pivot a return to plan
  rather than a new direction.
- PR #74 — `market_data.daily_bars` store + Alpaca backfill CLI
  (`shrap-market-data-backfill`), the Evaluator's data prerequisite. IEX feed,
  `adjustment=all`, tools-profile `market-data` service.
- PR #75 — **Universe Curator service** (ADR-0012): `research.universe_tiers` +
  `research.universe_staging`, the tier-transition events, and
  `shrap-universe-promote` (seed / stage / approve / reject / extend / expire /
  `load-launch-list` / list). Long-running service — CLI runs via
  `docker compose exec`, not the tools profile.
- PR #76 — Curator compose fix: the #75 block omitted `networks: - shrap_net`,
  so it joined the default network and could not resolve `postgres`
  (`socket.gaierror`), which blocked the launch-list load. Added the network
  and the `postgres` healthy dependency. **Same class of bug as the deploy
  lessons in KI-001's neighborhood: the service existed and looked healthy
  while being unable to reach its database.**
- PR #77 — Tier 3 membership becomes the authoritative universe gate when
  enforcement is on.
- PR #78 — **Strategy Evaluator first card** + eval-protocol v0.1:
  walk-forward (6 folds, 5-year window), realistic costs, friction stress,
  verdict mapping, the deferred set. Gates: 150 trades, Sharpe floor 1.0.
  Tools-profile `strategy-evaluator` service; `shrap-strategy-evaluate`.
- PR #79 — `shrap-strategy-seed` + the fission cost-curve pipeline seed v1
  write-up. **XLE only**, MA(20/100) crossover, anchored on the fission
  world-changer, `strategy_id` `01KYGTRTTQA9X2B2E16N4SBPTG`. Honest framing:
  a pipeline exerciser expected to be killed by the trade-count gate, not an
  edge.
- PR #80 — Paper strategy runner (`shrap-strategy-runner`), the consumer that
  gives a promoted strategy somewhere to execute.

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

## THE 2026-07-28 SESSION — #102 to #115

Fourteen cards. Three of them were findings that would have produced a strategy
that looked fine and was not, each found by measuring rather than reasoning.

| PR | What |
|---|---|
| #102 | Archetype-conditional Evaluator gates (ADR-0013 item 1) |
| #103 | Evaluator trigger + **ADR-0015**: kills apply unattended, promotes wait |
| #104 | Repair a test file the #102/#103 merge mangled — `main` did not run |
| #105/#107 | `make all` could not run the suite on a clean machine (KI-016) |
| #106 | **CI on every push and PR** — the repo had none |
| #108 | Full-firm audit: KI-017, KI-018, KI-019 + the implementation timeline |
| #109 | First honest Framework #3 seed — right archetype, no invented anchor |
| #110 | Cross-sectional strategies, shipped **refused** pending a benchmark |
| #111 | `shrap-strategy-stage` — a human path through the lifecycle |
| #112 | **Benchmark-relative evaluation** — the information ratio gate |
| #113 | `main` red again: decouple the stage tests from `DEFERRED_RULES` |
| #114 | Cross-sectional momentum seed — first strategy with a real prior |
| #115 | Notional position sizing arithmetic + the equity source |

**The three findings.**

1. **The promote gate could not tell skill from market exposure.** Naive
   buy-and-hold with no timing rule scored Sharpe 1.03–1.16 through the engine on
   drifting data — clearing the 1.0 floor purely by being invested. In one run a
   timing rule scored 2.28 against buy-and-hold's 3.22: it destroyed value and
   would have promoted. Fixed in #112; the gate is now an information ratio
   against equal-weight buy-and-hold of the strategy's own universe.
2. **Strategies could only ever trade one ticker.** The engine was always
   cross-sectional — `PricePanel` is "one or more tickers" and `walk_forward`
   counts a trade per ticker per weight change — but one line in the factory
   discarded every ticker but the first. This invalidated a claim repeated four
   times, that a daily-bar rule *cannot* clear the 150-trade gate: true per
   instrument, false across a universe (89 trades on one, 28,139 on fifty).
3. **The Runner never sized positions.** It emitted a fixed one share and never
   read account equity, so a strategy evaluated as equal-weight would trade at
   7.5% of a $10k book for a $750 name and 0.5% for a $50 one — fills
   accumulating under a P&L record matching no tested strategy.

**Two self-inflicted outages, both caught by the CI added mid-session.** #104 and
#113 were the same shape: two correctly independent PRs whose combination broke
`main`. The first (a file-tail merge) sat broken for an hour and was found by
luck; the second (a semantic interaction no merge tool could see) was reported in
under a minute. That is the CI card paying for itself, measured. KI-016 records
the hazard and the habit.

## ARCHETYPE-CONDITIONAL EVALUATOR GATES — 2026-07-28

ADR-0013's item 1, the sequencing's one hard code dependency. Before this the
Evaluator could evaluate exactly **one** archetype, `infra-graph-play` — which
`docs/00-vision.md` §7 assigns to "biases and sizing modifiers — **not** entry
triggers." The class of strategy the firm is designed to trade could not be
submitted for evaluation at all.

**ADR-0013 understated the problem, and the code said so.** The ADR named the
anchor gate as "the single hard code dependency." There were two, and the anchor
check was the *second*: `_check_spec_hygiene` refused every archetype but
`infra-graph-play` and ran three lines earlier, so a `technical-catalyst` record
raised `SpecHygieneError` and produced no verdict at all — not the fake
`KILL / anchor-not-live` the ADR predicted. The correction is annotated in the
ADR rather than edited away, because that gap is the reason gate applicability
is now one table (`ARCHETYPE_POLICIES`) instead of scattered conditionals.

| Archetype | Evaluable | Anchor gate |
|---|---|---|
| `infra-graph-play` | yes | required (unchanged) |
| `technical-catalyst` | **yes (new)** | not applicable |
| `bottleneck-rotation` | refused — no Bottleneck Scout | required |
| anything else | refused, fail-closed | — |

**`anchor_fresh=False` now means two different things**, so
`research.evaluations` gained `anchor_required` (by `ALTER … DEFAULT TRUE`,
correct for every row written when `infra-graph-play` was the only evaluable
archetype). Cards and the CLI summary render `live` / `not-live` /
`not-required`, never a bare boolean: a card reading "anchor: not live" for a
strategy that never claimed a thesis reports a falsification that did not
happen. The dead-anchor set stays queryable as
`anchor_required AND NOT anchor_fresh`.

**This is not a loosening.** The gate is removed from the archetype it was never
about: a `technical-catalyst` strategy's thesis is price and flow structure, so
a world-changer anchor is not a weaker falsifier for it — it is not a falsifier
at all, and requiring one produced anchors invented to satisfy the gate (see the
honesty note in `probe_strategies.py`). Every other gate applies unchanged to
both archetypes.

**`DEFAULT_MIN_TRADES = 150` stays universal**, reversing the mitigation KI-013
originally proposed. See the probe results below: a per-archetype floor would
report noise with more confidence rather than measuring structural strategies
more fairly. Framework #1 needs a different *protocol*, not a different
threshold — its own card.

Two smaller things found on the way: the dry-run summary never printed the
anchor state, so step 5 of the first-verdict runbook asked for a check the
output did not support (`anchor=` added to `summary()`); and the store's
positional binds now have an arity test, since adding a column mid-INSERT
renumbers every later `$N` silently.

## THE PROBE RESULTS — 2026-07-28 06:05 UTC

Two protocol probes (PR #100) evaluated back to back. Together with the
original seed the firm now has **three evaluations of the same rule family on
the same instrument over the same window**, differing only in moving-average
windows.

| Seed | fast/slow | Trades | Base Sharpe | Stress Sharpe | Verdict |
|---|---|---|---|---|---|
| `first` | 20 / 100 | 20 | 0.415 | 0.310 | kill / insufficient-trades |
| `trend-10-50` (control) | 10 / 50 | 43 | **-0.157** | **0.048** | kill / insufficient-trades |
| `trend-3-10` (treatment) | 3 / 10 | **145** | 0.745 | 0.331 | kill / insufficient-trades |

Evaluation ids `01KYKN8RABZ9M2MKZ91SFBJCZY` (control) and
`01KYKN8VS67TXJCBCG0MTQMJ5F` (treatment). All three strategies are now
`killed`; re-running either probe refuses with *"is 'killed'; this card
evaluates only 'hypothesis'-stage strategies"*, which is the terminal-state
guard behaving correctly.

### 1. The experiment isolated its variable

Trade count rose monotonically with window speed — 20, 43, 145 — so trade
frequency responded to the only thing that changed. The control did its job:
`insufficient-trades` reproduces across parameter pairs rather than being an
artifact of 20/100.

### 2. Sharpe did not

**0.415 → -0.157 → 0.745.** Non-monotonic, and it changes sign.

Same rule family, same ticker, same period, same costs. Three parameter choices
produce out-of-sample Sharpes that swing from clearly negative to nearly the
promote floor with no pattern. A parameter chosen on backtest Sharpe would pick
3/10; a parameter chosen one step away would pick a losing strategy.

This is the strongest evidence the firm has produced that these numbers are
**parameter noise, not edge**. A real edge is somewhat robust to parameter
choice; this one flips sign between neighbours. It is the same lesson fold 5 of
the first verdict taught at the fold level (Sharpe 1.712 from one trade), now
visible at the parameter level.

It also sharpens why the 150-trade gate matters. The gate is not a hurdle in
front of otherwise-usable numbers — it is the line below which the numbers are
not measurements.

### 3. The near-miss, and why it must not be tuned away

The treatment landed at **145 trades against a 150 gate**. Five short.

That invites lowering the gate or picking `fast=2/slow=8` to squeak over.
Walking `map_verdict` with a gate of 140 shows what it would buy: trades pass,
base 0.745 > 0 passes, stress 0.331 > 0 passes, and then
`base_sharpe < sharpe_floor` yields **HOLD / below-sharpe-floor**. Not a
promotion. Tuning the gate would purchase a different label on the same
non-edge, which is the "promoting noise" failure the vision names. Recorded
here so the temptation is on the record rather than rediscovered.

### 4. Four verdict branches remain untested

`no-edge`, `fails-friction-stress`, `below-sharpe-floor`, and `promote` have
still never run against real data — all four evaluations died at the trade-count
gate, which fires first. Note the control *would* have reached `no-edge` on its
negative base Sharpe had the gate not fired: reaching the untested branches
needs a probe fast enough to clear 150, not a lower gate.

### 5. First proof of two fixes

- **The #96 card-root fix works.** Two cards written to
  `/cards/<strategy_id>/<timestamp>.md`. Until this run it had been inspected,
  never executed successfully.
- **The terminal-state guard works**, and it means every protocol probe is
  single-use. The seed catalogue will accumulate one entry per experiment, which
  is correct — the graveyard is the denominator.

### Process notes from the run

Three operational defects surfaced, none of them in the evaluator:

1. **`docker compose run` does not rebuild.** The first probe attempt ran the
   previous night's image and reported `invalid choice: 'load-probe'`. Tools
   services need an explicit `build` after any code change — the run-to-
   completion analogue of the `--force-recreate` lesson, and invisible to
   `check-deploy-drift.sh`, which compares services rather than image ages.
2. **`--user` broke a working configuration.** The evaluations directory is
   owned by `10001:10001` exactly as the compose block documents, and the
   container's default user is 10001. Overriding to the host uid (950) fell
   through to `other` (`r-x`) and failed. The setup was already correct; the
   override created the mismatch.
3. Both are now covered by `docs/runbooks/deploying-after-a-code-change.md`.

## THE FIRST VERDICT — 2026-07-27 23:49 UTC

**The Research Department completed a loop for the first time.** Live on the
Dell, end to end, in one session.

```
evaluation_id=01KYJZQ10C3XAP12WMF21YVYVQ
kill (insufficient-trades)   hypothesis -> killed
trades=20  sharpe=0.415  stress_sharpe=0.310  protocol=0.1
anchor: live (world_changer status: promoted)
card=docs/strategies/evaluations/01KYGTRTTQA9X2B2E16N4SBPTG/20260727T234911Z.md
streams=research.strategy.verdict,research.strategy.killed
```

The chain that got there: anchor promoted (`01KXVVPXDMB4HS1QNRPQWRP1RX`,
`proposed -> promoted` — the row had sat undecided since 2026-07-18 while three
docs asserted it was promoted) → launch list loaded (50 names into
`research.universe_tiers`; the table had been empty, which is what gated the
Pre-Trade Tier 3 flag) → seed loaded at `hypothesis` → 1,507 XLE daily bars
backfilled → walk-forward → verdict → registry transition → card → events.

**A kill was the expected and correct outcome.** The distinction that decides
whether the run counted: `KILL / anchor-not-live` with `engine_ran=False` would
have measured nothing. This was `KILL / insufficient-trades` with 20 real
trades — the engine ran a full six-fold walk-forward, measured, and judged.

### The fold table is the real finding

| Fold | Return | Sharpe | Max DD | Trades |
|---|---|---|---|---|
| 0 | 14.44% | 0.701 | 34.87% | 3 |
| 1 | 1.24% | 0.181 | 17.26% | 5 |
| 2 | 16.66% | 1.526 | 10.99% | 3 |
| 3 | -14.83% | -1.566 | 17.12% | 3 |
| 4 | -9.32% | -0.515 | 19.86% | 5 |
| 5 | 29.49% | **1.712** | 14.09% | **1** |

**Fold 5 produced an annualized Sharpe of 1.712 from a single trade.** That
number is not a measurement of anything. It is one position held across a
trending 192-day window, and the statistic is computed from daily returns
while the strategy took one decision.

Fold dispersion runs -1.566 to +1.712. The aggregate 0.415 is the mean of six
numbers that are individually meaningless, and the 45.57% aggregate max
drawdown is real while the Sharpe is not.

**The 150-trade gate did exactly the job it exists to do**, and this card is
the evidence. It is not an arbitrary hurdle — it is the threshold below which
the protocol's own statistics stop carrying information.

### This changes KI-013's mitigation

KI-013 proposed making `min_trades` archetype-conditional so Framework #1
strategies are not auto-killed. **This card argues that fix is wrong.**

The problem is not that 150 is too high for a structural strategy. It is that
a Sharpe-based walk-forward cannot evaluate a strategy that takes 1–5
decisions per fold *at any threshold*. Lowering the gate would not make the
evaluation valid; it would promote a number that means nothing into a decision
that costs money — precisely the "promoting noise" failure the vision names.

The honest conclusion is stronger and less convenient: **Framework #1
strategies may not be evaluable under the current eval protocol at all.**
A structural thesis wants falsification against its stated kill criteria and
holding-period outcomes, not trade-frequency statistics. That is a different
protocol, not a different constant.

This also strengthens the ADR-0013 case rather than weakening it. The Evaluator
was built for the fast layer; the fast layer is what it can actually measure.

*(Mechanically, for the record: with `sharpe=0.415` against a `1.0` floor, a
lowered trade gate would have produced `HOLD / below-sharpe-floor`, not a
promotion. Nothing was going to be promoted here either way.)*

### Two defects found by running it

1. **The Evaluator could never write an evaluation card.**
   `STRATEGY_EVALUATOR_CARD_ROOT` was the relative path
   `docs/strategies/evaluations`, resolved against WORKDIR `/app`, which is
   root-owned while the container runs as `USER shrap` (uid 10001).
   `PermissionError` on `mkdir`. Fixed in this card: absolute path plus a bind
   mount, with the host-ownership requirement documented in the compose block.
   Worth noting the failure ordering was benign — `commit()` writes the card
   *first*, before the registry transition, the evaluation row, and the event
   publish, so both crashed attempts persisted nothing and the retry was clean.
2. **The Strategy Librarian and Strategy Runner have never been deployed.**
   `docker compose ps -a` returns no container for either — not stopped,
   never created. Both are default compose services with no `profiles:` key,
   shipped in PR #40 and PR #80. Recorded as KI-014.

Neither blocked the milestone. The Evaluator performs its own registry
transition in `commit()`; the Librarian is a separate consumer of
`research.strategy.verdict` that transitions again under `expected_from`
guarding, so it acks and skips an already-applied verdict by design
(`librarian_service.py:14`).

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

## Infrastructure Mapper Month-2 arc (2026-07-26/27)

- PR #81 — Infra Mapper graph schema + store: `research.graphs`,
  `graph_nodes`, `graph_node_history`, `graph_node_evidence`, anchored on
  `research.world_changers(candidate_id)`.
- PR #82 — `shrap-infra-mapper` CLI (`load-seed-graph` / `list`) and the
  first hand-seeded graph on the promoted fission thesis. Deliberately small:
  the critical-path fission layers have no Tier-3 representation, so only the
  `end-user` demand side is seeded (MSFT/AMZN/GOOGL/META, `low` confidence,
  `downstream-beneficiary`). Forcing a wrong-layer ticker in would have been
  the Cisco-1999 failure the Mapper exists to prevent.
- PR #83 — deterministic staleness pass (`maintenance`, `--freshness-days`
  default 180, `--dry-run`). Two-way (`active` <-> `stale-evidence`, does not
  latch), idempotent, one `research.graphs-updated` per transition. Owns only
  that axis: `pending-review` / `downgraded` / `removed` are skipped, since
  reactivating them would launder a kill decision. Also fixed #82's loader to
  write true evidence dates.
- PR #84 — repair for seed evidence rows stamped with load time. The Dell had
  loaded the graph under #82's code, and the load is idempotent-by-skip, so
  #83's fix could not reach it. Appending cannot repair a too-*fresh* row —
  `MAX(observed_at)` keeps picking it — so this is the one documented
  in-place-update exception on `graph_node_evidence`, scoped to rows matching
  a seed node exactly, with a history row per correction.
- PR #85 — thesis-level observation log: `research.world_changer_observations`,
  append-only, plus `shrap-world-changer-observe {add,list}`. Named apart from
  the existing `world_changer_evidence` provenance table (*what made us
  propose it* vs. *what has happened since*). Every row declares whether it
  bears on a declared kill criterion; the summary reports that count first and
  warns on zero-falsifier-contact, all-soft evidence, and
  supporting-with-no-contradicting. Dangling criterion indices rejected.
- **Dell verification 2026-07-27:** restamp corrected 4/4 rows (603–938 days
  older), staleness pass flagged all four `active → stale-evidence`, second
  run reported `flagged stale: 0, unchanged: 4` with no writes. Graph now
  reads `(4 nodes, 0 active)` — it proposes zero universe names, which is the
  honest reading of two-year-old evidence.
- **Process note:** PR #84's card was briefly committed onto #83's branch (the
  KI-001 stacking trap), caught before push and cherry-picked onto a fresh
  branch off `main`. Same for #85 relative to #84. The trap is easy to hit
  when cards arrive back-to-back in one session.

## Funnel unblock attempt + Ollama Cloud (2026-07-27 evening)

- PR #86 — Status reconciliation after the Infra Mapper arc; KI-008 (thesis
  memory is manual-only), KI-009 (the funnel is structurally incapable of
  promoting — 8/8 clusters arXiv-only, triangulation needs ≥2 origins + ≥1
  hard leg), KI-010 (ingest legs die silently), DQ-006 updated with the first
  named false negative.
- PR #89 (was #87) — Filter prompt v4: source-class-aware evidentiary bar
  (`attested` vs `claim`), cumulative-evidence rule, reject-only-after-every-
  archetype rule; `shrap-tech-watcher-refilter`; re-filter report shows every
  verdict rather than only flips.
- PR #88 — Tech Watcher routed to Ollama Cloud, ending local-only for that
  agent. `gpt-oss:20b-cloud` (Low Usage) for the bulk filter, `kimi-k3:cloud`
  (Extra High) for synthesis — Ollama bills GPU-time and publishes a usage
  tier per model, so the split is cost-shaped.
- PR #90 — Recovered #88's auth fix onto main (pushed after that PR merged, so
  it missed — KI-001 pattern) and fixed the re-filter to select on **model**
  as well as prompt version. A verdict's identity is the (prompt, model) pair;
  keying on prompt alone meant a model swap selected nothing and the pass
  silently declined to test the change being made.
- PR #91 — USASpending fetches new awards newest-first.
- PR #92 — Session handoff: pivot from Framework #1 to the strategy loop.
- PR #93 — Ollama Cloud authenticated by bearer token, not daemon signin.
- PR #94 — The first-verdict runbook, corrected against the code (an earlier
  draft named a compose service that does not exist, backfilled the wrong
  tickers, and skipped both of the pipeline's hard prerequisites).
- PR #95 — **ADR-0013** (fast layer, cross-lens synthesis, Framework #3) and
  **ADR-0014** (Development Department descope to the three-tier compute
  boundary; no autonomous capability may depend on Tier 3 — a human opening a
  Claude Code session).
- PR #96 — Evaluator card root wired through compose; the firm's first verdict
  recorded.
- PR #97 — Deploy-drift check (`infra/check-deploy-drift.sh`), plus KI-014: the
  Strategy Librarian and Strategy Runner had never been deployed at all.
- PR #98 — The drift check was hiding its own error. `2>/dev/null` swallowed a
  `permission denied … docker.sock`; stderr is surfaced and the sudo cause
  named. Shipped one message after agreeing not to hide unknowns.
- PR #99 — The firm's first evaluation card.
- PR #100 — Librarian logs verdict convergence at INFO rather than ERROR (the
  Evaluator transitions in `commit()`, so the Librarian's second transition is
  a designed no-op, not a failure); control/treatment probe seeds added.
- PR #101 — Probe results: Sharpe is parameter noise at these trade counts.
  KI-015 raised. `docs/runbooks/deploying-after-a-code-change.md` added after
  the Librarian ran stale code — a rebuild must name *every* service the change
  touched.
**These ten (#92–#101) were backfilled on 2026-07-28.** They had gone unrecorded
in both status docs — the identical gap that hid #72–80 and caused a runbook to
be drafted against a repo state that had already moved.

**Outcome, honestly: the funnel is still blocked, and the diagnosis moved
twice.** The 9B local model was confabulating (it called a fission reactor
"fusion ignition" and rejected a *fourth* criticality as "a single
milestone"). The 20B cloud model does not confabulate and gives coherent,
specific reasons — but returned 0 flips on the same 16 items. Reading those
reasons, the rejections are largely **defensible**: `cost-curve` requires unit
cost evidence, and DOE press releases contain none. The remaining question is
a taxonomy one (KI-009), not a model or prompt one.

**The USASpending finding is the session's most concrete result.** The leg was
never dead — `external_ts` there is the award *Start Date*, not fetch time, so
the "18 days stale" reading was wrong. The real defect was that `time_period`
matches any transaction activity and the API's default sort favours the
largest awards, so every pull returned the same decades-old national-lab
umbrella contracts (1993 Lockheed $48B, 1999 UT-Battelle $42B) which deduped
to nothing. Fixing it surfaces **$900M to American Centrifuge Operating
(uranium enrichment), dated 2026-07-06** — hard-source, dollar-denominated, on
the promoted fission thesis's critical path, in the very `raw-inputs` layer
the Infra Mapper flagged as unrepresented.

**Process note:** four wrong calls this session (ingest-not-filter; prompt v4
will fix it; the Ollama auth mechanism; the re-filter selection key). The first
two were hypotheses the data corrected. The last two were shipped without
end-to-end verification, and both surfaced only when Mike ran them on the Dell.

## The three-day gap — #129–#175, backfilled 2026-07-31

**This is the third backfill of this section, and each gap has been larger than
the last: #72–80 (nine), #92–101 (ten), now #129–175 (forty-six).** Three
recurrences of the same failure is a missing mechanism, not three lapses of
diligence — `make doc-drift` is added in this PR so the fourth is caught by a
command rather than by an audit. Found on 2026-07-31 by a full systems check:
every status document was stamped 2026-07-28 or earlier while `main` was at
#175, and `CLAUDE.md` still named `session-handoff.md` as ground truth for what
to pick up next.

(There is no PR #137; it was closed unmerged.)

### Three paper accounts, and the evaluation protocol grows teeth (2026-07-29)

- PR #129 — Runbook for bringing up the three paper accounts.
- PR #130 — psql commands fixed in two runbooks: `.env` vars are not in the
  operator's shell.
- PR #131 — Deploy ordering fixed — a reader outran its writer's migration
  (KI-020).
- PR #132 — A strategy shows its account, and says when it will not trade.
- PR #133 — `--profile tools` is required on the build line, not just the run
  line.
- PR #134 — `load-momentum` wired into the seed CLI; the loader was unreachable.
- PR #135 — Information ratio reported in the verdict summary.
- PR #136 — Panel coverage reported in the verdict — how much history was
  actually tested.
- PR #138 — Ragged price panel: a cross-sectional universe that grows as names
  list.
- PR #139 — `window_years` is a cap, not a default. Use all available history.
- PR #140 — `PROTOCOL_VERSION` bumped to 0.2; #138/#139 changed what the
  numbers mean.
- PR #141 — Strategy lineage: what a revision came from, why, and the size of
  the search behind it.
- PR #142 — Momentum stands down when the whole universe is falling.
- PR #143 — Consistency across year-sets reported; six folds no longer hidden
  behind one number.
- PR #144 — A revision that loses to the strategy it revised is killed.
- PR #145 — Momentum long-short: the half of the effect the rule was missing.

### The Risk Officer, the research ledger, and autonomous proposal (2026-07-30)

- PR #146 — **The Risk Officer** — the firm's veto authority, built. Note it is
  a library enforced inside the Pre-Trade Checker, **not a compose service**.
- PR #147 — Short-horizon reversal: the effect momentum steps around.
- PR #148 — The promote bar rises with the size of the search behind it.
- PR #149 — **The research ledger** — read across attempts, not just within one.
- PR #150 — Four documented factor effects, each its own experiment.
- PR #151 — The Hypothesis Generator's archetype set was two ADRs out of date.
- PR #152 — The ledger crashed on its first real row: asyncpg returns jsonb as
  text.
- PR #153 — Fold consistency persisted, ledger counts corrected, two hazards
  recorded.
- PR #154 — The multiple-testing denominator counts draws, not rows.
- PR #155 — The corpus says what to try next — and is forbidden from saying
  more.
- PR #156 — **The firm proposes its own strategies**, and reports what it
  cannot test.
- PR #157 — Tech Watcher reads arXiv q-fin; the proposer has a feed.
- PR #158 — The axes are discovered from the engine, not from a list.
- PR #159 — Importing the literature contract must not drag in numpy.
- PR #160 — Everything the first live run exposed.
- PR #161 — A dry run reports the gaps it found, not the empty store.
- PR #162 — Network peripherality: the first effect the firm chose for itself.
- PR #163 — **`hypothesis-generator-trigger`** — the proposer runs on its own.
- PR #164 — `shrap-literature-refilter`: a prompt fix reaches the backlog.
- PR #165 — The News Analyzer had never fetched a single item.
- PR #166 — Calibration evidence, and the admission that the key data was
  discarded.
- PR #167 — Zero is an alarm: output-freshness checks in the Health Monitor.
- PR #168 — EDGAR discovery: 8-Ks older than the Filing Processor's own
  deployment.
- PR #169 — Box-wide cloud routing; the local 9B is out of the judgement path.
- PR #170 — Shadow-eval harness: settle model choice with evidence, not priors.

### The first shadow eval, and what it found in our own code (2026-07-31)

- PR #171 — The eval let a model agree by failing. Judgement columns counted
  unparsed answers, so a model that parsed nothing "agreed" with a 90%-negative
  incumbent 90% of the time.
- PR #172 — The filter scored a markdown fence as an unparseable verdict.
  `parse_filter_response` called `json.loads` on the raw completion, so fenced
  JSON became a manufactured `relevant=False`. Confirmed afterwards to have cost
  production **zero** verdicts — the fix is purely prospective.
- PR #173 — KI-009 is the archetype bars asking an aggregate question of one
  document. Card spec for the bar experiment (timeline 1.4).
- PR #174 — The first shadow-eval verdict: `qwen3.5:397b` promoted on the Tech
  Watcher filter binding, and the first entry in `calibration.md` §(e).
- PR #175 — Prompt v4 has admitted nothing, and the number is zero: 0 relevant
  across 2,472 verdicts.

## THE 2026-08-01/03 SESSION — #176 to #190

The session that closed KI-009 and got the firm to a live forward test.

- PR #176 — `make doc-drift`. The status set had gone 46 PRs stale for the third
  time; this compares the highest PR number in each doc against `git log`.
- PR #177 — Nothing automatically ingested price bars (KI-024). Every evaluation
  since 2026-07-29 had read a frozen panel.
- PR #178 — 11,096 reconciliation "discrepancies" were a re-report count, not an
  incident count: 8 orders x 3 agents x 288 passes/day. Fixed at the source by
  making the stream edge-triggered.
- PR #179 — Redis streams grew without bound (KI-025). Trimming lives in the
  Audit Logger: Redis is the transport, Postgres is the record.
- PR #180/#181 — The three-bar archetype harness (timeline 1.4), then a
  stratification fix: a head-of-list `--limit` had produced 600 arXiv items and
  measured a hard-leg admit rate of zero without ever scoring a hard-leg item.
- PR #182 — Correction: the filter *was* partly model-limited. The shadow eval's
  "five models, zero disagreements" was measured on 20 items containing no
  USASpending awards at all.
- PR #183 — A dry run reported verdict counts it never computed.
- PR #184 — A falsifier set you can only add to. The promoted fission thesis had
  collected two hard observations bearing on none of its three kill criteria,
  because all three measured nuclear against itself and none against a
  competitor. Append-only: a thesis that can rewrite its falsifiers has none.
- PR #185 — Intraday 1-min bars (timeline 2.8), Alpaca IEX. Separate table:
  50 names x 390 minutes is ~19,500 rows/day against daily's 50.
- PR #186 — The Runner can act more than once a session (2.9). Cadence is
  declared per strategy and **absence means daily**, so nothing already in the
  registry changed behaviour.
- PR #187 — The *other* dry run was lying too. Same defect as #183, missed
  because the two reports are separate classes.
- PR #188 — Two pieces of operator guidance that were wrong: which container a
  CLI runs in follows its imports, not its subject; and `assign-account` told
  operators to set an env var ADR-0017 had removed.
- **PR #189 — EDGAR full text. The one that closed KI-009.** The feed's summary
  was a file size; with document bodies the corpus admitted 46 items against
  zero, and the funnel proposed its first pipeline candidate the next day.
- PR #190 — The intent forgot which strategy asked for it. The forward test's
  first session emitted 20 signals and had all 20 vetoed `UNKNOWN_STRATEGY`,
  because `strategy_ids` was a Card 2 placeholder that #146 had quietly made
  load-bearing.
- PR #191 — Status docs reconciled after the funnel opened.
- PR #192 — The Runner sold stock the account did not own (KI-030). It read
  "am I invested" from its own record of intent; the Risk Officer scales every
  order, so a recorded 52 GME against 9 actually held made every exit a ~81%
  short. `ops.position_snapshots` is now authoritative. **This is KI-005
  arriving** — deferred until a Research strategy needed portfolio state, which
  happened on its second day of trading.
- PR #193 — The status loop stalled a month on the firm's first order (KI-031).
  An account filter that read *unstamped* as *mine*, plus a 404 classified as
  retryable, jammed all three Execution Agents on stream id `1783203414014-0`
  from 2026-07-04. Six real fills sat behind it. On deploy the backlog drained:
  `execution.order.filled` 47 → 53.

## 2026-08-04 — the firm's first fills

Six orders, six fills, on `PA3KQN57WVXY` at 13:30 UTC. Signal through fill with
no human in the path, and the first two trading defects (above) found and fixed
the same day. Three of the six were exits against positions the account had
never held; those shorts were closed by hand.

## Merged 2026-08-10 to 2026-08-23

- PR #195 — The firm floored every order twice and bought the cheap half. 26 of
  89 decisions vetoed `SIZED_TO_ZERO`, all under six shares, and the executed
  book skewed cheap because an expensive name floors to a smaller share count.
  Quantities are fractional end to end; the only floor left is the one a
  non-fractionable asset forces at the broker.
- PR #196 — The Risk Officer *scaled* exits, and shorted what it could not sell.
  `reduces_position` answered False whenever the book lacked the ticker or held
  less than the sell, so the exit fell through to sizing. Against an empty book a
  6-share sell approved 1.125 — a short, needing no veto.
- PR #197 — Provenance on the fractionable cache, so a failed lookup is
  distinguishable from a real "no". Written before it was needed.
- PR #198 — Exits stranded a residue because held shares were **derived** as
  `market_value / latest_close` rather than read from
  `ops.position_snapshots.quantity`. Two prices, one division, and no exit ever
  completed: 12 and 27 open names for top-ten strategies.
- PR #199 — The audit trail **rounded** every fractional approval to a whole
  share. `INTEGER` columns, fractional quantities since #195, and Postgres
  accepted the write silently.
- PR #200 — The generator wrote kill criteria that fire when the thesis
  *succeeds*. Two of three proposed candidates were unkillable by construction.

## 2026-08-19 — ten sessions of autonomous trading, measured

68 orders, 100% filled, no human in the path. Best account **+0.70%** — against
nothing, because the firm has no control account. `PA3YPMG9AD4Z` looked like one
until its +0.66% turned out to be hand-placed AAPL/SPY smoke-test buys closing
at a profit. The benchmark has to be computed from `market_data.daily_bars`.

The two strategies scored IR 0.306 against a benchmark at 0.876 and were staged
as systems tests, so a weak fortnight is what the evaluation predicted.
**The constraint is research throughput, not execution.**

## Merged 2026-08-23 — the measurement thread

- PR #201 — Status docs reconciled, then corrected twice: the third account is
  neither a control nor a spare but an empty **strategy slot**, which makes it
  the research-throughput constraint stated in hardware.
- PR #202 — Removed a stray `docker-compose.yml` swept into `docs/status/` by
  `git add -A`. No secrets in it; checked before removing.
- PR #203 — Exposure-matched benchmark arithmetic. A fifth-invested book judged
  against a fully invested benchmark measures the Risk Officer's caution, not
  the strategy's skill.
- PR #204 — `shrap-live-benchmark`, wiring that arithmetic to the three tables
  the firm already keeps.
- PR #205 — **Three defects found by the first live run**, invisible to 14
  passing tests: weekends in the window (no bars, so the whole Friday-to-Monday
  move landed in `excess` unoffset), a benchmark that averaged per-period
  returns while calling itself buy-and-hold (+2.403% against +1.825% — a gap
  larger than every excess figure reported), and a flat account rendered as
  having "lost". A fourth, caught by an existing test: a name rejoining
  mid-window dragged its since-inception return into one transition.
- PR #206 — A live information ratio, computed with the promote gate's own
  `sharpe` so the two cannot drift. `None` rather than `0.0` when undefined,
  and flagged `NOT MEANINGFUL` below 20 sessions.

- PR #207 — Status docs reconciled to #206.
- PR #208 — **Langfuse tracing (KI-018).** Langfuse had been deployed for three
  months with a grep for `langfuse` across `src/` returning zero matches.
  `TierLLMClient.complete()` now records each completion as a trace plus a
  generation — full input and output, token counts, model parameters, latency —
  covering all eleven call sites at once. Calls carry a **`task` name** rather
  than only a tier, because llm-routing.md slices the migration sample by task
  and several unrelated jobs share `local-classification`. Tracing **fails
  open**, the inverse of the Risk Officer: a tracer that raised would let an
  observability outage stop the Tech Watcher filtering. **It traces nothing
  until Mike creates API keys in the Langfuse UI** — see the runbook's new
  §3.4a, which treats "reachable" and "traced" as different claims.

  Then audited against **Langfuse's own instrumentation skill**, vendored at
  `.claude/skills/langfuse/`. Four gaps found and fixed: no session grouping
  (a 300-item pass was 300 unrelated traces), the local→cloud escalation read
  as two units of work rather than one item scored twice, task names were not
  verb-first, and masking had never been assessed (it now is — public-source
  text only, ADR-0003 keeps credentials out of this layer).

  The audit also found **KI-032**: the deployed `langfuse/langfuse:2` is **end
  of life**, and no current Langfuse SDK — nor the OTel endpoint the project is
  steering everyone towards — can talk to it. The direct ingestion client is
  therefore the only supported path on this server, not a shortcut. Upgrading
  means ClickHouse, a blob store and a worker container; recommended **not this
  sprint**.

- PR #209 — **Re-land of the Langfuse audit that #208 merged without.** The
  commit passed CI on the branch and was left behind when the PR merged at its
  first commit, so `main` carried the tracer with none of the audit. The failure
  mode the same PR documented: `doc-drift` compares PR numbers, and the number
  was right.
- PR #210 — **One `CompletionClient`, not eight.** Eight modules declared the
  protocol themselves, byte-identical in seven cases. Structural typing makes
  that legal and it reads like decoupling, but it stopped being decoupling the
  moment the copies had to agree: adding `task`/`metadata` (#208) and
  `trace_id`/`session_id` (#209) meant editing eight files twice in two days.
  Same shape as the trading path's defect family — a fact declared in more than
  one place, where the copies are free to disagree. Net −117 lines, and a parity
  test so the single declaration cannot drift from the client it describes.

- PR #211 — **No position under one share could ever be closed (KI-033).** The
  Pre-Trade Checker rejected fractional quantities as malformed — correct when
  written, because every order was whole shares, and never revisited when #195
  made fractional orders normal. Because that gate sits *upstream* of the Risk
  Officer, the fractional arithmetic in #195, #196, #198 and #199 was correct
  and unreachable. The Runner emitted exits daily; the gate truncated them to
  `0` and refused them as `INVALID_QUANTITY`; **52 refusals** accumulated and no
  order was ever submitted. Not just residues — `RIOT` at 0.1875 and `MARA` at
  0.75 were equally untradeable, so since #195 the firm could open positions it
  could not close. Also: the last `INTEGER` quantity column (`last_quantity`)
  widened *and* its `int()` read-cast removed, and sub-$1 exits now refuse with
  `BELOW_BROKER_MINIMUM` rather than being submitted to a broker that rejects
  them.
- PR #211 also — **KI-034**: the runbook's deploy path was wrong in seven
  places, three of them the nightly backup crons. Whether those backups have
  ever run is unverified.

- PR #212 — **The firm has never had a backup (KI-034).** Not "the crons point
  at a wrong path": `crontab -l` returned *no crontab for truenas_admin* and
  `/mnt/backups` did not exist. Every order, fill, verdict and equity point
  since Month 1 has lived in Docker volumes on a single host with no HA, no
  replication and no dumps. The documented procedure would not have worked
  either — it referenced `$SHRAP_DB_USER`, which cron does not set; it
  redirected with `>`, so a failed dump left a truncated `.gz` that looks like a
  backup; and its Qdrant line wrote the snapshot API's *response* to disk while
  the snapshot stayed in the container. `scripts/backup.sh` replaces all four
  cron lines, verifies gzip integrity **and** a minimum size before renaming a
  `.partial` into place, and exits non-zero naming the stage that failed. **Open
  until installed** — a script in the repo is not a backup.
- PR #213 — **The backup script could not reach Docker on the host it runs on.**
  First real run of #212 on the Dell died on `permission denied ... /var/run/docker.sock`:
  `truenas_admin` is not in the `docker` group, which is why every interactive
  command in this project has been `sudo docker`. The script called bare `docker`.
- PR #214 — **The Postgres backup would not have restored.** Past the permission
  error, `pg_dumpall` warned of circular foreign-key constraints on
  `continuous_agg` — a TimescaleDB catalog table. `shrap` is a Timescale
  database and the script was dumping it as plain Postgres, so the artifact it
  produced was not restorable. Checked against Tiger Data's logical-backup
  documentation rather than from memory. **Three PRs to get one backup that
  works, each failure only visible on the next real run** — the pattern worth
  keeping from #212–#214 is that none of them were found by reading the script.

- PR #215–#221 (2026-09-16/17) — **the measurement session.** Regime Router
  (#215, ADR-0010 §4, closing KI-012 §4) · status docs reconciled and the
  stage-fraction tension retired as arithmetic (#216) · intraday panel path
  (#217) and the `--timeframe` flag that made it callable (#219) · the Kelly
  posterior that fills sizing's empty slot (#218) · plus **two self-inflicted
  regressions found only by running on the Dell**: #217 broke
  `shrap-strategy-evaluate` at import including the daily path (#220), and #215
  left the Runner one rebuild away from silently not trading (#221).
- **KI-035, measured the same night:** the autonomous research loop has produced
  **one** strategy in its life, scoring IR −0.006. The Hypothesis Generator is
  starved, not broken — `research.literature_items` holds nine rows total. The
  seven `capability-gap` rows are a prioritised build list and the cheapest item
  is market capitalisation, absent from every table.

**The first trustworthy reading:** IR +0.84 and −0.45 over nine sessions,
t-statistics of +0.16 and −0.09. The tool printed a number above the promote
floor and refused to let it be used, which is the whole point of it.

## 2026-09-17/18 — #222–#231: the firm can size on evidence and take a profit

**Nine PRs. Three of them are findings, not features, and one of the findings
invalidates how the firm read its own gate for months.**

### The promote gate has never been failed on evidence (#228/#229, KI-036)

The gate compares a point estimate of the information ratio against 0.50 and
nobody had asked how precisely that estimate is known. On the firm's own panel
the standard error of an annualised IR over 5.1 years is **±0.47**; the best IR
ever recorded is **0.448**. The distance between them is **0.11 standard
errors**.

Re-measuring the same backtest on rolling three-year windows — identical
decisions, only the measurement period moves — momentum scores anywhere from
**−0.021 to +0.743** and clears the floor in 16% of windows. Volume-shock
changes *sign* depending on the start year.

Separating 0.45 from 0.50 at two sigma needs **~1,800 years** of daily history.
This is the 16-year forward-test arithmetic pointed at the *backtest*, where it
is worse: the gate asks a strategy to separate from 0.50 rather than from zero.

**Two mechanisms were measured and eliminated before this became the finding.**
Momentum with costs set to *zero* still scores 0.487, so turnover reduction
could never have cleared the floor — the monthly-rebalance card was dead before
it was written. And the active-return streams are genuinely uncorrelated
(momentum vs volume-shock **−0.05**), so breadth is available in principle, but
only one of four signals has a positive IR on a common window and breadth
multiplies skill rather than supplying it. Low-volatility and high-proximity
correlate at **+0.85** — one bet, not two, which matters for the "~11
uncorrelated strategies" plan.

**The consequence, ruled by Mike:** connect the backtest to the Kelly
posterior. `posterior_from_backtest` reads the IR as a likelihood carrying its
own standard error against a prior centred on **no skill**, and
`selection_discounted_sd` widens that error by `sqrt(1 + ln attempts)` — the
identical factor the verdict uses to raise the bar, applied to the uncertainty
instead. Every strategy the firm has measured sizes **down**: momentum 126/21
from the flat 0.25 to 0.147. `posterior_sizing` is **off**.

### The firm could not take a profit or cut a loss (#230, KI-037)

`grep` for `stop_loss`, `take_profit`, `profit_target`, `trailing_stop` across
the whole codebase returned **zero matches**. Every filled order in the firm's
history is stamped **09:30:03–09:30:07 ET** — one burst at the open and nothing
for the remaining 390 minutes. The only exit was the next morning's re-rank,
and because every strategy is momentum, a name that spikes moves *up* the
ranking and gets held or bought more.

Alpaca reports `avg_entry_price` and `unrealized_plpc` on every position and the
firm **discarded both**. Now captured from the venue and never reconstructed.

**Armed 2026-09-18** via `infra/.env`: stop-loss at **10%**, intraday
take-profit at **7%**. The since-entry take-profit is deliberately unarmed — it
would sell a position the 126-day momentum signal is actively long, and the
re-rank would buy it back. The intraday one does not conflict: momentum runs
`skip=21` and deliberately ignores the last 21 days because short-horizon
reversal runs opposite to it, so a one-day spike lives in the window the
strategy excludes. Verified against the live book: **zero exits at the open.**

### The accounts are 84% cash, and that costs 1.15 of IR (KI-037)

    account         equity      cash    deployed
    PA3HEG2CLXLU  10,073.99  8,496.02     15.7%
    PA3KQN57WVXY  10,054.58  8,366.94     16.8%
    PA3YPMG9AD4Z  10,066.19 10,066.19      0.0%

Stage `0.25` x regime band `0.75` = **0.1875**, working as configured. Holding
selection perfectly constant, **IR is `-Sharpe(benchmark)` at every exposure
below 1.0** — −1.152 on this panel, independent of the level. The $74 is what
~$1,600 earned in a melt-up.

**The deadlock:** exposure is low because edge is unproven, and a $70 return
cannot prove edge. Posterior sizing would take it to ~0.11, which is the correct
belief and moves against the flat accounts. Both are true. Mike's call.

**The third account is idle because nothing has reached `paper` in 46 days** —
15 strategies created, 13 killed, 2 promoted, last on 2026-08-02. Promotion also
does not assign an account: `assign_account` lives only in the stage CLI, so a
newly promoted strategy sits idle until a human runs it. The Runner logs that at
error level and names the command, so it is visible rather than silent.

### The filter model was eight days from retirement (#231, KI-038)

Ollama retires `qwen3.5:397b` on **2026-09-25**; it carried 1,361 requests the
week before. Shadow eval (calibration.md §(e) Run 2): `kimi-k3` and
`deepseek-v4-pro:0813` both 100% schema with 0 errors, `glm-5.3` **0%** (37 of
40 in prose). Promoted **`kimi-k3`** — 100% self-consistency and 100% pairwise
agreement with the outgoing model.

**The incumbent no longer reproduces its own verdicts.** Half the sample is
items it previously scored relevant and it called **0%** of the sample relevant,
so its 50% "agreement" is agreement on the negatives and disagreement on every
positive. Prompt v4 does not reproduce whatever scored the corpus's 189
positives — the same shape as KI-009: taxonomy, not model.

`kimi-k3` was unreachable at the last eval (402) and now runs clean, which
falsified a compose comment claiming the paid subscription "does not [exist]".
Synthesis has been on `gpt-oss:20b` because of a constraint that is gone;
corrected in place and deliberately not changed, since the verdict was for the
filter task.

### Also shipped

- **#222–#224** shares outstanding and market cap, 1,594 rows back to 2009;
  the first summary reported `unmapped=8` when 26 of 50 names had no count.
- **#225** recovered a lost finding: the stage-fraction tension was not real,
  IR is scale-invariant (identical +0.142060 at 0.1875/0.75/1.00).
- **#226** rank persistence replicated the paper's claim on 50 names —
  volatility rank rho **+0.880** (positive in 100% of 73 months), return rank
  **+0.019**. The return result independently corroborates the Evaluator
  killing momentum.
- **#227** inverse-volatility weighting: **Sharpe 1.016**, the first time
  anything cleared the 1.0 floor — and **IR got worse in every configuration**,
  because lowering a book's volatility lowers its participation in a rising
  benchmark. Risk engineering cannot substitute for alpha.

## 2026-09-18 — #232–#247

**The intraday path closed end to end, and three faults were found that had
each been silently wrong for weeks.** The recurring shape held in every one:
*a component reconstructed a fact that was already recorded, and the
reconstruction disagreed.*

### The intraday path (#233–#242)

- **#233** use the capital; **#234** the Runner resolves a bar reader per
  strategy cadence (`CadenceBarReaders`), ending the assumption that every
  strategy is daily.
- **#235** rate limits became account-scoped and slot-aware; the per-account
  cap was raised to **300/day** on Mike's ruling.
- **#236** intraday bars advance on their own, at the grain strategies declare
  — `resolve_interval` treats the configured sweep as a *ceiling* and lowers it
  to half the finest declared cadence, floored at 60s.
- **#237** the Hypothesis Generator may now propose intraday effects; an
  intraday input named without a cadence returns `missing-data` rather than
  being built daily.
- **#241** the scheduled sweep judges a strategy on the grain it declared.
- **#242** measured whether intraday cadence multiplies a daily edge.
  **It does not** — IR **0.003** at 15-minute cadence against **0.415** daily.
  `IR = IC x sqrt(breadth)` predicts ~8.8x; the realised answer is that IC does
  not survive the shorter horizon.

### Three silent faults

- **#238** two healthchecks that could never have passed (`shrap_qdrant`,
  `shrap_langfuse`) — the qdrant probe used `/dev/tcp` under `/bin/sh`, which
  is dash.
- **#239** the firm can now see when a container is unhealthy or crashlooping
  (`docker-state-exporter`, plus `check_container_health` /
  `check_container_restarts`). `count()` over an empty result set returns *no
  data*, not 0, so both checks carry `or vector(0)`.
- **#244** **node-exporter had never once been scraped.**
  `max_over_time(up{job="node-exporter"}[45d])` was **0** — not flapping, never.
  Two independent faults, each fatal alone: the exporter bound `127.0.0.1`
  under `network_mode: host` (unreachable from any container at any address),
  and Prometheus targeted `172.17.0.1`, the *default* bridge, while this
  project's gateway is `172.16.0.1`. The config's own comment said "typically
  172.17.0.1 on Linux … adjust if your bridge gateway differs" — a guess plus an
  instruction nobody ran. The firm had no host CPU, memory or disk metrics and
  never had.

### Feeds and filings

- **#245** `source` joined the primary key of `daily_bars` and `intraday_bars`.
  The store's own docstring had claimed this since it was written ("part of the
  primary key intent … if a future card ever backfills SIP") while the key did
  not contain it. A SIP backfill would have **overwritten IEX row by row**,
  restating five years of volume in place and silently making every recorded IR
  irreproducible. Not one of the eight bar-reading queries filtered on `source`.
- **#246** **the filing roster covered 4 of the firm's 50 names.** EDGAR ingest
  was healthy throughout — ~1,000 items a week — and every non-roster registrant
  was dropped at `roster.ticker_for(cik)`, so `matched: 0` on every pass and no
  filing recorded for fifteen days. 42 of 50 CIKs resolved from SEC's own
  registry; eight ETF trusts left unresolved rather than guessed. Backfilled:
  **113 → 169 filings, 4 → 34 distinct symbols.**
- **#247** IEX vs SIP, measured. **Negative result** — see
  `docs/research/feed-comparison-experiment.md`.

### Performance

- **#240** the panel's compressed series are precomputed — **~7x end to end**
  (Amdahl; the 24x is the inner loop alone), bit-identical output. A 2.5-hour
  evaluation became ~19 minutes, diagnosed with py-spy against a live container.

### Why #247 is a negative result and not a win

The uncontrolled comparison said SIP scored **IR +0.5372** against IEX's
**+0.2332** — clearing the 0.50 promote floor. It is wrong. IEX has almost no
history before mid-2020, so SIP's panel was 433 sessions longer and included
the COVID crash; the run confounded feed with window.

Controlled on identical 1,433-session panels the sign flips (**IEX −0.1315**,
**SIP +0.2275**) — but SIP is higher in only **3 of 6 folds**, the whole
aggregate advantage is one year, and dropping that fold leaves a mean delta of
**+0.0011**. The feed changes what the strategy *selects*; it does not reliably
change what it *earns*.

## 2026-09-19 — #248–#251

### The corpus index, and Qdrant finally doing something (#250)

Qdrant was deployed 2026-07-02, listed in `CLAUDE.md` under "in production" for
two and a half months, and held **zero collections** the entire time while
164 MB of filing and paper text sat in Postgres searchable only by exact string
match. `shrap-corpus-index` now chunks, embeds (local `nomic-embed-text`, 768
dims, on the Dell's own Ollama) and writes **107,384 points** with full
provenance. See `docs/runbooks/corpus-index.md`.

Three things this card got wrong first, each found by running it rather than by
reading it:

- The first full run died at `httpx.WriteTimeout`. Batching by *document* meant
  twenty 400 KB filings became ~2,000 points — a 34 MB JSON body. Every test
  passed because the fake Qdrant accepted any list size.
- It held the box at "120% CPU", which is 1.2 of twelve cores: `embed` awaited
  each batch in turn, so exactly one request was ever in flight.
- **It contained zero research papers, and exited 0 while doing it.**
  `document_text` is populated for `sec-edgar` and nothing else, so the index
  was 15,487 EDGAR filings and no literature — precisely the corpus that would
  serve the Hypothesis Generator. Found by *querying* (a momentum query returned
  bank-earnings 8-Ks), not by counting. A clean exit and a six-figure point count
  proved nothing.

Retrieval measured at **precision@5 = 86.7%** over 15 queries judged by
`kimi-k3`: 35/35 on corporate events, 30/40 on research literature. The weak
queries are corpus-size artefacts — 287 q-fin papers cannot fill a top-5.

### Backups had still never run on a schedule (#249)

The newest backup was **2026-09-01**, 18 days old. Both existing backups are
stamped 14:24 and 16:54 — afternoon, i.e. hand-run. `/var/log/cron.log` is
unrotated back to 2026-07-17 and contains **zero** `cronjob.run` entries: the
scheduled job had never once fired. The script also exited 1 on every run,
because an empty Qdrant volume tarred to 386 B, below the 1024 B floor, and
`fail` exits before retention pruning.

**Check `/var/log/cron.log` for `cronjob.run`, not `/mnt/backups` for files.**
Files in the destination were never evidence the schedule worked.

### A dead ingest source looked exactly like a quiet market (#251)

Both arXiv feeds returned HTTP 406 on **46 consecutive hourly passes** from
2026-09-17 14:17. The firm's only source of quantitative-finance literature was
off for two days; the sole trace was the Hypothesis Generator logging
`sweep_empty`, which is what it logs during a genuinely quiet week.

There *was* a six-hour freshness check on `research.raw_source_items`. It reads
`max(fetched_at)` over the whole table, and EDGAR kept inserting ~1,000 items a
week throughout, so it stayed green for the entire outage. **A table-level
maximum is an AND across every producer that writes to that table** — the same
shape as the filing roster covering 4 of 50 names and node-exporter never being
scraped, where an aggregate stayed healthy while a component inside it was dead.

Now `research.ingest_cursors` is checked **per source** on a three-hour
threshold, and the alarm names the feed. On the cursor table rather than the
items table, because `raw_source_items` upserts `ON CONFLICT DO NOTHING` — its
`fetched_at` moves only on genuinely new items, and USASpending has legitimately
inserted nothing since 2026-08-27 while working perfectly.

**Two separate faults, both presenting as the same empty-bodied 406 from arXiv's
Fastly edge.**

**Two of the four categories are refused outright**, deterministically —
measured three clean rounds, eight seconds apart, identical every time:
`cs.AI` 200, `cs.LG` 200, `q-bio.NC` 406, `cond-mat` 406, `cond-mat.stat-mech`
406. The source asked for all four in **one OR query**, so two refused
categories took the two healthy ones down with them. Now one request per
category, tolerating individual failures and failing the source only if every
category fails. **Verified against live arXiv: 182 items where the old code
returned zero.**

**And we were violating arXiv's published rate limit on every pass.** Their
terms ask for "no more than one request every three seconds, and… a single
connection at a time"; the pass registers two `ArxivSource` instances and
fetched them back to back with no delay. A throttled host gets 406 on every
cache *miss* while cache *hits* keep returning 200 — which is why this looked
random for an hour. Now a shared 3-second throttle (the limit is per host, so a
per-instance one would satisfy nothing), applied inside the retry too, plus an
explicit `Accept` and a descriptive `User-Agent`. `arxiv-qfin` recovered on its
own at 22:48 and took 4 new papers, consistent with a throttle expiring.

**Not established:** whether the throttle fix prevents recurrence — it could not
be tested against a throttled host, and the diagnostic probing was itself heavy
enough to keep re-tripping it. **Do not diagnose an external API by hammering it
from the production IP.**

See `docs/runbooks/a-dead-ingest-source.md`.

### Market cap: one name was missing because SEC serves two endpoints inconsistently (#253)

`market_data.shares_outstanding` covered **35 of 50** names. Ten of the fifteen
gaps are ETFs, where market cap is meaningless and no CIK resolves (the
`LAUNCH_CIKS_UNRESOLVED` set from #246). The five real gaps were DKNG, META,
MSTR, NET and PYPL — and **PYPL was ours, not SEC's**.

PYPL's `companyconcept` answers **HTTP 200 with `{"units": {"shares": []}}`** for
`dei:EntityCommonStockSharesOutstanding`. The concept exists; it carries nothing.
`companyfacts`, for the same registrant and the same concept, holds **44 rows**
with identical `end`/`val`/`filed`/`form` fields. The concept chain cannot tell
"answered empty" apart from "does not tag this concept" — both yield no rows —
so PYPL was filed under `no_data` while the data sat one endpoint away.

`companyfacts` is now the last link in the chain, tried only when the concept
path comes up dry. The two endpoints agree exactly where both have data
(**AAPL 70/70, MSFT 68/68, NVDA 69/69**), so nothing changes for the names that
already work, and a megabyte-scale payload is only fetched for names that would
otherwise be recorded as empty. Verified against live SEC: **PYPL 0 → 44 rows**
(882,105,493 shares, filed 2026-05-05), AAPL unchanged at 70.

**DKNG, META, MSTR and NET remain genuinely absent, and are not faked.** Their
entire `dei` fact set is `EntityPublicFloat`, and they do not tag
`us-gaap:CommonStockSharesOutstanding` either — multi-class issuers whose
point-in-time count is reported per share class with a dimension that neither
endpoint exposes. The only figures available are
`WeightedAverageNumberOfSharesOutstandingBasic`, which is a *period average*, not
a count on a date. Substituting it would change what market cap means without
saying so, which is the shape of error this project keeps finding. Coverage is
therefore **36 of the 40 non-ETF names**, and the four are a separate card
needing cover-page parsing or another source.

**This closes the ingest half of the market-cap capability gap, not the
consuming half.** `SELECT_MARKET_CAP_SQL` still has zero callers, so
`volatility-rank-forecast` is still not computable. That is the next card.

### The local Langfuse had never held a trace (#254)

`shrap_langfuse` ran for its entire deployed life with **zero traces**. Every one
of the firm's 24 agents carries `LANGFUSE_HOST=https://us.cloud.langfuse.com`,
and Cloud held **4,424 traces** when this was checked — newest at 22:48 the same
day, four `filter-literature-item` calls on the q-fin papers arXiv delivered when
it recovered.

The local instance was `langfuse/langfuse:2` — **2.95.11, OSS v2, end of life** —
costing an 821 MB image, a 68 MB volume, two healthchecks and a nightly backup
leg to store nothing. Same treatment as `ib-gateway` (#243): the compose survives
at `infra/langfuse-local/` so the decision is reversible, the containers do not.

**`DEFAULT_HOST` is now empty, and that is the load-bearing part.** It used to be
`http://langfuse:3000`. Left as a fallback after the container was removed, an
agent with keys set and `LANGFUSE_HOST` unset would log `tracing_enabled` against
a host that no longer exists and post every trace into nothing — **KI-018
exactly**, which is the failure that module's own docstring exists to prevent.
A missing host now disables tracing and names the reason.

The backup script needed no change: #249 already made the Langfuse leg skip
rather than fail when `shrap_langfuse_db` is absent.

**A knock-on worth recording.** `src/shrap/llm/tracing.py` is hand-rolled against
the legacy `/api/public/ingestion` endpoint **because** OSS v2 cannot talk to
Python SDK v3 or v4, or to OTel — Langfuse's own compatibility matrix rules all
three out against a v2 server. Cloud supports them. That constraint is now
obsolete, so replacing the hand-rolled client is an available card; it is not
this one.

### The archetype bar experiment finally ran, and the premise wobbled (#255)

The spec (#173) has sat at step 3 — *"run over the full corpus"* — since July.
A 200-item stratified pilot ran 2026-09-20 on `kimi-k3`, three bars, 600
completions. Full results in `docs/research/archetype-bar-results.md`.

| bar | hard-leg admits / scored |
|---|---|
| `A-incumbent` | 2 / 146 |
| `B-evidence-contribution` | 1 / 146 |
| `C-signal-tagging` | 0 / 146 |

**Those are not distinguishable from each other** and must not be read as a
ranking — the spec predicted exactly this about sampling and was right.

**The finding that does not need statistics:** DQ-006's named false negative, the
DOE fourth-criticality announcement, is admitted by **Bar A — the unmodified
production prompt v4** — under `kimi-k3`, while production rejected it under
`qwen3.5:397b`. *The bar did not change; the model did.* The spec's premise opens
with "four model families, one answer — the filter is not model-limited";
`kimi-k3` was promoted in #231, after that eval, and was never in it.

**And the full run is not affordable as specified.** Measured directly rather
than estimated: 600 completions moved the weekly Ollama allowance from 0.100 to
0.202, so the full 21,231-item corpus × 3 bars = 63,693 completions is **~10.6
weekly allowances**. The spec's "~3% of a week" was computed in July against a
corpus eight times smaller. Both the corpus size and the cheaper alternative are
now recorded in the spec itself so nobody quotes the stale figure.

### The corpus index has its first consumer (#259)

#250 built ~107,000 chunks of the firm's own filing and paper text at a measured
86.7% precision@5, and **nothing read it**. The CLI queried it; no agent did.

The Hypothesis Generator's proposer has always seen exactly one abstract. It now
also sees the passages from the firm's corpus nearest that abstract, and reports
in a new `corroboration` field whether they support, contradict or say nothing
about the effect. Verified against the live index — querying *"Illiquidity and
the cross-section of expected returns"* returns *"Liquidity Premium and
Investment Horizons"* at 0.71, a paper about Kyle's lambda and order flow
predicting cross-sectional returns.

**The hazard this is built around is attribution.** A proposal must name an
author and a year and refuses without one. If retrieved passages could reach that
field the model would have a pile of other people's papers to attribute this
paper's claim to, and the citation would look exactly as well-formed as a true
one. So the context is labelled as context, appended *after* the item, the prompt
says in terms that `prior` must name the authors of the item above, and
`parse_proposal` still reads `prior` from the item's own metadata. A test pins
each of those.

Retrieved hits carry `body_kind` from the index, so an `abstract only` hit is not
presented as a hit on the paper.

**This does not touch the binding constraint, and the module docstring says so.**
The generator is starved because `research.literature_items` holds nine rows
(KI-009, KI-035). Retrieval makes each of those nine better grounded without
producing a tenth.

Off unless `HYPOTHESIS_GENERATOR_RETRIEVAL` is set — opt-in because it adds a
Qdrant and an Ollama dependency to an agent that had neither, and every failure
mode (no retriever, Qdrant down, embedder gone) degrades to the exact prompt the
agent sent before.

**Deploy note:** the `hypothesis-generator` tools image predated #250 and had no
`shrap.common.qdrant_client` at all. `--profile tools build hypothesis-generator`
is required, per `docs/runbooks/deploying-after-a-code-change.md`.

### Market cap became something a strategy can read (#258)

#253 made the data correct. This makes it *reachable*: `PanelWindow` gains
`market_caps()`, and `AVAILABLE_SERIES` — the whole expressible surface of the
firm's strategy engine — goes from `{close, volume}` to `{close, volume, market
cap}`. It is the **first addition to that set**.

Verified end to end: `volatility-rank-forecast`, the cheapest of KI-035's seven
capability gaps, now classifies as **`missing-scorer`** rather than
`missing-data`. Those two mean very different things in
`expressible.py`'s own words — *"an ingestion pipeline, and often a paid feed"*
versus *"cost: an afternoon. This is the queue worth working."*

**Market cap is derived in the panel from the panel's own closes, and that is the
load-bearing decision.** `SELECT_MARKET_CAP_SQL` already existed and joins shares
onto `daily_bars` itself — so using it would mean the panel's price and the
panel's market cap came from two independent reads, each with its own
`adjustment` and `source`. Since #247 the evaluator can run on either feed, so a
panel built on `alpaca-sip` could be handed caps computed from `alpaca-iex`
closes with nothing to say so. That is this project's recurring defect exactly: a
component recomputing a fact the system already holds, and the copies disagreeing.
The reader now returns raw `(filed_at, shares)` and the panel multiplies.

**Selection is on `filed_at`, never `as_of`** — the same no-peek rule
`shares_store` enforces one layer down, applied against the panel's grid. A count
describing 2024-03-31 filed on 2024-05-09 is invisible until 2024-05-09.

Sanity-checked against live data on the latest session: NVDA $5.35T, AAPL
$4.90T, GOOGL $4.27T, MSFT $3.66T. Coverage is **36 of 50 names** — the other 14
are 10 ETFs (market cap is meaningless) and the 4 multi-class issuers SEC
publishes no point-in-time count for (#253). Those carry `nan` throughout, never
zero: zero is a number a size ranking would happily sort to the bottom.

One alias was written and then removed before shipping: **`shares outstanding` is
not a synonym for market cap**, it is a component of it. A strategy ranking on
the raw count is a different strategy from one ranking on size, and the module's
standing rule is that a wording difference may be normalised while a construction
difference must not.

### Correction: all three archetype bars already ran in July (#260)

#255 said *"bars B and C have never been run."* **That was wrong.**

**And the first version of this correction blamed the wrong cause**, which is
recorded here because a correction carrying its own error is worth less than
nothing. It said the summary table held one row while the detail table held
four. Both tables were right: `research.bar_experiment_runs` has always held
**four** rows for 2026-07-31, one per invocation, each naming its bar.

The query was `select * from research.bar_experiment_runs order by 1 desc limit
5` piped to `head -14`. `report_markdown` is a multi-line `TEXT` column holding
an entire run report, so in psql's aligned output **one row spans dozens of
lines** and `head -14` cut the result off inside the first one. Four rows came
back; one was shown. **I read my own truncation as a finding.**

**A pipe is part of the query.** `head` truncates silently and the truncation is
indistinguishable from a short result. `SELECT *` on a table with a wide text
column is not a listing — name the columns, or ask for `count(*)` when a count
is the claim.

The three comparable runs share an item set (A∩B = 598, A∩C = 599 of ~600), one
corpus, one model:

| bar | hard scored | hard admits |
|---|---|---|
| `A-incumbent` | 454 | **2** |
| `B-evidence-contribution` | 453 | **2** |
| `C-signal-tagging` | 454 | **1** |

Every admit across all three is one of **two USASpending DOE awards**. `B`
admits exactly what `A` admits; `C` admits one of them and labels a uranium
enrichment contract `bio-mechanism`.

**The hypothesis predicts B and especially C should admit substantially more
than A. They do not.** That is the outcome the spec names as falsifying.

So the full three-bar run on 21,231 items — the one I costed at **10.6 weekly
Ollama allowances** — is probably not worth funding. The three-bar comparison
already exists. What does not exist is the same comparison under the current
model, and that is **one bar over 599 items**, roughly 10% of a week.

That replay ran as `01M2YHEGZ5KSBAADGHYK96QGAY` and **407 of its 599 rows carry
an error**, every one the same HTTP 429: *"you have reached your session usage
limit."* 192 items were actually scored.

**The budget number this project has been using is the wrong one.**
`https://ollama.com/api/usage` — an endpoint nobody here had read, returning JSON
to the firm's own key — reports `limits.session.usage = 1.0` against
`limits.weekly.usage = 0.277`. Every cost estimate in #255 and in the spec is
denominated in the weekly window, and the **session** window is what actually
stops a run. It had not reset an hour later, and the payload does not say how
long it lasts.

### The experiment that starved the production filter (#261)

A 599-item bar-experiment replay on 2026-09-20 hit Ollama Cloud's **session**
rate limit at item 192 and then **made 407 more calls it already knew would be
refused** — every one returning the same HTTP 429 — writing all 407 as error
rows. Twenty minutes later the Tech Watcher's hourly literature pass, which
draws on the same account, aborted with `scored: 0` after five consecutive 429s
of its own. **An experiment took the production filter down.**

Three faults, each independent:

**1. The budget was denominated in the wrong window.** Ollama Cloud enforces a
**session** window as well as a weekly one. Every estimate this project has made
— the 2.7x inferred from request counts, then #255's "10.6 weekly allowances"
measured against `weekly` — priced runs against the window that was not binding.
`https://ollama.com/api/usage` returns both directly to the firm's own key and
nobody here had read it. At the moment of the failure it reported
`session.usage = 1.0` against `weekly.usage = 0.277`.

`src/shrap/llm/ollama_usage.py` now reads it. `--dry-run` prints both windows
beside the call budget, because a completion count without the allowance it is
drawn from is exactly how this run got planned.

**2. There was no backpressure between a batch job and the agents.** The quota is
account-wide; the Tech Watcher, the Hypothesis Generator and any experiment all
draw on it. The CLI now refuses to start when the binding window is inside a
**10% reserve** held for the always-on agents. `--ignore-quota` spends it
deliberately; nothing spends it by accident.

**The reserve is re-checked every 50 items, not only at the start.** A start-time
check proves there was room to *begin*, which is the moment it matters least — a
407-item bar can spend the whole window mid-flight and starve production anyway,
which is the fault the guard exists to prevent. A mid-run stop is **clean**: the
items behind it are unwritten, same contract as the error wall, so the run stays
resumable rather than half-recorded.

**3. A run of failures is not a failure.** `run_bar` caught every exception and
continued, which is right for one bad item and wrong for a wall. The production
literature filter has had `MAX_CONSECUTIVE_FAILURES = 5` for months;
`run_bar` now has the same guard, and the items behind the wall are left
**unwritten** rather than recorded as errors — *never attempted* and *failed* are
different facts, and a resume needs to tell them apart.

**Resuming.** `--resume-run RUN_ID` scores only the items a run errored on and
writes them into the **same** run id, so the experiment ends as one comparable
set of rows rather than two halves under two ids. The conflict clause carries
`WHERE ... error IS NOT NULL`: a row already holding a verdict is never
overwritten. Without that guard a resume would replace measurements taken hours
earlier, possibly under a different model — a recomputation overwriting a
recorded fact, which is this project's oldest defect shape.

The usage reader fails open by design. The endpoint is undocumented, and a
courtesy check that can block the firm's work when Ollama changes a URL would be
a worse fault than the one it guards against. An unreadable meter prints
`unavailable (proceeding blind)` and the run starts.

## Security notes

- Old Alpaca paper key was rotated after appearing in chat.
- New credentials are local-only in ignored `infra/.env`.
- Do not print, commit, or paste Alpaca key/secret values.
