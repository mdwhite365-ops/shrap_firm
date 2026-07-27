# Current sprint status

**Last updated:** 2026-07-27 (evening — session close, pivot to strategy loop)
**Phase:** Month 3 / Framework #1 funnel live
**Operating mode:** Paper only. No real-money execution.

## Current focus

**All three loops are physically running, and the funnel has its first
tracked candidate.** After the 2026-07-18 reorder ruling (DQ-007, PR #52 —
"widen the web before deepening the funnel," motivated by the Valar
Atomics case), the same day shipped: gov-sources ingest (PR #53 —
USASpending awards + DOE newsroom), the promotion workflow (PR #54 —
promote/kill CLI + Mike-seed path), a Dell rebuild deploying both, and
the firm's first Mike-seeded world-changer:
`Mass-manufactured fission cost-curve crossing`
(`01KXVVPXDMB4HS1QNRPQWRP1RX`, archetype cost-curve, falsifier horizon
2027-12, three observable kill criteria). The 2026-07-19 session landed
eight PRs (#56–63): the RKLB/Iridium handoff items (ADR-0012 tiered
universe, Market Phase Scheduler), the regulator leg (Federal Register
API — nrc.gov RSS proved Akamai bot-blocked, verified before building),
the KI-007 auditability fix, the source-class independence taxonomy
(spec, then same-day enforcement in the triangulation rule), and the
News Analyzer spec.

**The 2026-07-22/23 session landed seven more PRs (#65–71): the
Intelligence Department's Month 2 seeds are both live, and the ADR-0012
follow-ups from 2026-07-19 are closed out.** News Analyzer service (PR
#65) publishes materiality-scored signals on `intelligence.signal` —
local scoring (`local-classification`) with cloud escalation
(`cloud-default`) for material items, market-phase-driven cadence,
append-only verdict history (KI-007) — running today on a placeholder
nine-symbol set (the Regime Classifier's default) pending Tier 3 state.
Filing Processor spec and service (PR #66, #68) do the same for Tier 3
8-Ks: full-text fetch from EDGAR, per-item-code materiality scoring,
`signal_type: "filing"`, a placeholder AAPL/NVDA/TSLA/LMT roster keyed
by CIK, and a new shared `src/shrap/intelligence/market_phase.py`
module that the News Analyzer now imports too — its container needs
recreating at the next deploy. The universe README was restructured
around the ADR-0012 tiers (PR #67 — the 50-name list is now framed as
the Tier 3 launch proposal; DQ-004 lock-in still open), and the
Universe Curator spec was rewritten from derived-only consumer to Tier
2/3 owner + transition-event publisher (PR #69 — accepted by merge:
`research.universe_tiers` as the Tier 3 store, events-as-history via
the Audit Logger, no auto-add path, eviction lands back in Discovery;
open question on record: only 6 of the 50 launch names have behavioral
profiles, grandfather-or-gate ruling pending). The Pre-Trade Checker
gained its Tier 3 membership check (PR #70) — flag-gated on
`PRE_TRADE_CHECKER_TIER3_ENFORCEMENT` (default false), fail-closed
(`TIER3_STATE_UNAVAILABLE` on any query failure, never cached), the
tier literal `'active'` pinned for the Curator's first implementation
card to match, gated ahead of the rate guardrails, and the checker
gained an asyncpg pool + DSN. **Do not flip the Tier 3 enforcement flag**
until the Curator's launch-list load populates `research.universe_tiers`
— flipping now vetoes every order, including the smoke. The Filing
Processor backfill CLI (PR #71) followed:
`shrap-filing-processor-backfill`, docker-exec pattern on the
`shrap-tech-watcher-promote` precedent, `--rescore` appends new
verdict-history rows rather than overwriting (KI-007). Process note: all
seven cards were built by delegated Opus/Sonnet subagents, with the
orchestrator reviewing, gating, and opening PRs — Mike's 2026-07-22 cost
policy. Next: the Dell deploy session for #65/#68/#70, then the
Universe Curator service card once DQ-004 and the profile-coverage
ruling land.

**The 2026-07-26/27 session closed the Infrastructure Mapper's Month-2
scope (#81–85) and deployed it to the Dell.** Five cards: the graph schema
+ store (#81), the `shrap-infra-mapper` CLI + first hand-seeded graph
(#82), the deterministic staleness pass (#83), a repair for seed evidence
rows stamped with load time (#84), and the thesis observation log (#85).
Three of the five produced findings rather than only code, and the findings
are the point:

1. **The universe gap (#82).** Mapping the promoted fission thesis onto the
   closed layer-role taxonomy, the critical-path layers — `raw-inputs`
   (uranium), `power-gen` (nuclear/SMR), `power-delivery` — have **no Tier-3
   representation**. Rather than force a wrong-layer ticker in (the
   Cisco-1999 trap the Mapper exists to prevent), the seed graph is
   deliberately small: four hyperscalers on the `end-user` demand side, at
   `low` confidence / `downstream-beneficiary`. The graph's own output says
   the fission thesis is only weakly expressible in today's universe.
2. **The false-fresh clock (#83/#84).** The staleness pass runs on evidence
   `observed_at`, which exposed that #82's loader stamped *load* time — so
   2024 procurement announcements looked days old and the first pass would
   have reported a fully fresh graph. General property worth carrying to
   every append-only store: **a max-based clock can absorb a too-old error
   but never a too-new one**, because the spurious row keeps winning the
   max. Repair required an in-place update, the one documented exception on
   `graph_node_evidence`.
3. **No home for thesis evidence (#85).** The funnel had two evidence stores
   and neither could hold an observation about a *thesis*:
   `graph_node_evidence` is per-ticker-per-layer, `world_changer_evidence`
   is proposal-time provenance keyed on `item_id` with no observation date.
   `research.world_changer_observations` is the ongoing log; every row
   declares whether it bears on a declared kill criterion, and the summary
   reports that count first, because a thesis accumulating supportive
   observations that touch none of its falsifiers is collecting a story, not
   being validated.

**Live on the Dell 2026-07-27.** Restamp corrected 4 of 4 evidence rows
(603–938 days older; AMZN's year-only ref floored conservatively to
2024-01-01). The staleness pass then flagged all four nodes
`active → stale-evidence`, and a second run reported `flagged stale: 0,
unchanged: 4` with no writes — idempotence confirmed, so the pass is safe to
schedule. **The graph now reads `(4 nodes, 0 active)`:** when the weekly
aggregation card lands it unions the *active* ticker set, so this graph
currently proposes **zero** universe names. That is the correct answer, not
a bug — the one promoted thesis has no tradeable expression whose evidence
has been confirmed within two years.

## NEXT SESSION STARTS HERE — pivot to the strategy loop

**Mike's ruling, 2026-07-27 (end of session).** Framework #1 work pauses; the
next card is the **Strategy Evaluator's first verdict**. The reasoning, and it
is grounded in the vision rather than a preference:

`docs/00-vision.md` §7 says most of Shrap's strategies trade on "technical and
short-term-catalyst signals — fast loops, many trades," while the structural
department runs "on a much slower clock" and feeds "biases and sizing
modifiers — **not entry triggers**." ADR-0007 has the funnel producing the
*universe* ("the graph IS the trading universe"), not signals. So Framework #1
answers *which names*, never *when to trade them* — it was never the day/swing
edge and cannot become it.

Two facts make the pause urgent rather than optional:

1. **The funnel currently feeds nothing.** DQ-004 locked the 50-name universe
   as a hand-chosen list and the Universe Curator's implementation card has not
   shipped, so even a perfect funnel would not change what the firm trades
   today. The Mapper's one graph proposes zero names (all four nodes stale).
2. **The firm has never evaluated a single strategy.** The Evaluator is built
   (#41, in-house walk-forward) and has never produced a verdict. The Librarian
   is deployed and idles waiting for one. The only "strategy" in existence is
   the disarmed fixture. The inner loop executes flawlessly and has nothing
   real to execute.

**The chain to unblock:** market-data backfill → `shrap-strategy-evaluate` on
the seeded strategy → first verdict → Librarian lifecycle transition. That is
the first real test of whether the firm can find edge at all, which is a more
load-bearing question than the Tech Watcher's filter calibration.

**The command block below replaces an earlier draft that would not have run.**
Read against the code on 2026-07-27, that draft had four defects: it named a
`strategy-seed` compose service that does not exist, backfilled
`AAPL,MSFT,NVDA,SPY` when the seeded strategy trades **XLE and nothing else**,
and skipped both of the pipeline's hard prerequisites. Corrected and verified
chain:

**Two gates the Evaluator checks before it will backtest anything**
(`src/shrap/research/strategy_evaluator/pipeline.py`):

1. **Tier-3 membership** (`_check_tickers_tradeable`, line 488). Every ticker
   must be `active` in `research.universe_tiers` or it raises
   `SpecHygieneError` and nothing runs. That table is **empty** — the
   launch-list load has never been run on the Dell. This is no longer blocked:
   DQ-004 was resolved 2026-07-23 and the 44 unprofiled names were
   grandfathered in the same ruling, so `load-launch-list` is just an
   un-run command now, not a pending decision.
2. **Anchor freshness** (line 293). The strategy's world-changer must be
   `promoted` in `research.world_changers`. **It is `proposed`** — the same
   open item already logged below ("The Mapper's anchor thesis was never
   promoted"). `first_strategy.py:52` claims "promoted 2026-07-18"; the
   database disagrees, and the database is right.

**The trap, and the reason `--dry-run` is not optional.** A dead anchor does
*not* refuse. `map_verdict` returns `KILL / anchor-not-live` with
`engine_ran=False` and zero trades (`verdict.py:61`), and a non-dry-run
`commit()` **transitions the seed to `killed` on the registry**
(`pipeline.py:376`). Because `shrap-strategy-seed load-first` is idempotent on
`spec_hash`, it will then refuse to recreate the row — the firm's only
strategy would be stuck killed, needing a manual registry transition to
recover. That kill would also *look* like the milestone landing: a first
verdict, persisted, with an evaluation card and published events. It would
test nothing — the backtest never ran.

## THE FIRST-VERDICT RUNBOOK — one session, six steps

**Why this is the whole priority.** The sprint's *minimum* success criteria
(`docs/00-vision.md:40`) are three items. Two are met: Mike's daily time is
under two hours, and the audit trail is strong. The third — "the development
department, research department, and trading floor are all functional" — has
one gap. The Trading Floor is proven (9/9, autonomous signal→fill). Development
is functional by substitution and ADR-0014 makes that honest. **Research has
never completed a single loop.** This runbook closes it.

Every argument below was verified against the code on 2026-07-27, including
one that a previous draft got wrong: `candidate_id` on the promote CLI is
**positional**, not `--candidate-id` (`promotion.py:314`).

Run top to bottom in one sitting. Paste output back after each step.

```bash
cd /mnt/Archive/shrap/shrap_firm/infra

# ── STEP 1 ── Promote the anchor. Mike's decision, authorized 2026-07-27.
# candidate_id is POSITIONAL. `research.world-changer-promoted` has zero
# consumers, so nothing cascades — this only flips state and writes the event.
sudo docker compose exec tech-watcher \
  shrap-tech-watcher-promote promote 01KXVVPXDMB4HS1QNRPQWRP1RX \
  --note "Promoted 2026-07-27 to unblock the Evaluator anchor gate for the firm's first verdict. Thesis and falsifiers unchanged. Open: archetype may be keystone-completion rather than cost-curve (ADR-0013 s7)."
# EXPECT: a promoted decision line naming the candidate.

sudo docker compose exec tech-watcher shrap-tech-watcher-review
# EXPECT: "Proposed: 0 · Promoted: 1"; decided_at and decision_note no longer NULL.

# ── STEP 2 ── Populate research.universe_tiers. Idempotent; re-run = no-op.
# Does NOT flip PRE_TRADE_CHECKER_TIER3_ENFORCEMENT, which stays false.
sudo docker compose exec universe-curator shrap-universe-promote load-launch-list
# EXPECT: "launch-list loaded: 50 promoted (...)" — or fewer if partially loaded.

sudo docker compose exec universe-curator shrap-universe-promote list
# EXPECT: XLE present with tier `active`. If XLE is missing, STOP — step 5 will refuse.

# ── STEP 3 ── Seed the strategy. The seed CLI ships inside the evaluator
# image; there is no strategy-seed service. Idempotent on spec_hash.
sudo docker compose --profile tools run --rm strategy-evaluator \
  shrap-strategy-seed load-first
# EXPECT: "loaded: 01KYGTRTTQA9X2B2E16N4SBPTG ... at status=hypothesis"
#         or "already present: ... status=hypothesis — skipped".
# IF it says status=killed: stop. The registry needs a manual transition
# back to hypothesis; load-first will not recreate it.

# ── STEP 4 ── Backfill XLE. The ONLY ticker the seed trades. The engine reads
# a 5-year window; start earlier so MA(100) has warmup inside fold one.
sudo docker compose --profile tools run --rm market-data \
  shrap-market-data-backfill --tickers XLE --since 2020-01-01 --dry-run
# EXPECT: "tickers=1 rows_fetched=~1600 rows_upserted=0 dry_run=True"
# If rows_fetched is 0, the Alpaca data credentials are the problem, not the code.

sudo docker compose --profile tools run --rm market-data \
  shrap-market-data-backfill --tickers XLE --since 2020-01-01
# EXPECT: rows_upserted matching rows_fetched, dry_run=False.

# ── STEP 5 ── Evaluate. DRY RUN FIRST, ALWAYS.
sudo docker compose --profile tools run --rm strategy-evaluator \
  shrap-strategy-evaluate --strategy-id 01KYGTRTTQA9X2B2E16N4SBPTG --dry-run
# READ THE ANCHOR LINE BEFORE ANYTHING ELSE.
#   "Anchor: not live"  -> step 1 did not take. STOP. Do not drop --dry-run.
#   "Anchor: live" + trades > 0 -> the engine ran. This is a real verdict.
# Expected verdict: KILL / insufficient-trades. That is SUCCESS, see below.

# ── STEP 6 ── Commit the verdict, and watch the Librarian react.
sudo docker compose --profile tools run --rm strategy-evaluator \
  shrap-strategy-evaluate --strategy-id 01KYGTRTTQA9X2B2E16N4SBPTG
# EXPECT: evaluation_id=... transitioned=True to_stage=killed card=... streams=...

sudo docker compose logs --tail 50 strategy-librarian
# EXPECT: the Librarian consuming research.strategy.verdict and transitioning
# the registry. This is the loop closing — it has idled since PR #40 waiting
# for exactly this event.
```

**How to read the result, and why a kill is the win.** The seed is a plain
MA(20/100) crossover on one ETF; it flips position a handful of times over five
years and cannot clear the 150-trade gate. Its own write-up
(`docs/strategies/fission-costcurve-seed-v1.md`) predicts this.

The distinction that matters is *which* kill:

- `KILL / anchor-not-live` with `engine_ran=False` — **fake.** Nothing was
  measured. The backtest never ran.
- `KILL / insufficient-trades` with `engine_ran=True` — **real.** The engine
  ran a full walk-forward, measured the strategy, and judged it. The Research
  Department completed a loop for the first time.

The second outcome is the milestone. Record it in `recent-changes.md` as such.

**The one calibration question this raises, and it is Mike's.** `DEFAULT_MIN_TRADES
= 150` (`engine.py:51`) is calibrated for the vision's fast layer — "fast loops,
many trades." A multi-year structural thesis *cannot* produce 150 trades, so for
Framework #1 the gate is not measuring overfitting risk; it is excluding the
archetype categorically (KI-013). Setting an archetype-appropriate floor is
legitimate. Setting it to whatever number lets this seed through is the
"promoting noise" failure the vision warns about. Those look similar and are
not. The ruling wants a rationale, not a number.

**After the loop closes.** The remaining sprint weeks go to making it *repeat*
without Mike — the Evaluator has no trigger and is tools-profile/manual-only,
so nothing in Research is automatic regardless of how many lenses exist. That
is the difference between "we did it once by hand" and "the Research Department
is functional." Everything in ADR-0013 beyond merging it is Phase 2.

**Open PRs at session end (none stacked, any merge order):**

- **#91** — USASpending new-awards fix (below). Not on the critical path;
  merge whenever.
- Everything else from this session is merged through #90.

**Carried over, deliberately not done:** KI-008 auto-attach, KI-009's
taxonomy question (should `cost-curve` admit leading indicators, or is
industrial scale-up a separate archetype — Mike's artifact), KI-010's
freshness/zero-new-rows alerting, the anchor-thesis promotion call, and
logging the Valar criticality item as a thesis observation against kill
criterion 3.

## Main branch state

Merged on `main` through PR #90 (#91 open). **PRs #72–80 were never recorded
in either status doc** — that gap is why the pivot's command chain was drafted
against a repo state that had already moved: the Evaluator, the Universe
Curator service, the market-data store, the strategy seed, and the paper
strategy runner all shipped in that window. Backfilled into
`recent-changes.md`. Highlights since the spine-close status:
consumer groups (#37), strategy registry + state machine (#38), Strategy
Librarian service (#40), Evaluator ruling — Framework #1 first, in-house
walk-forward engine (#41), LLM tier client (#42), registry seed correction +
Ollama runtime bump (#43), GPU swap + drift commit (#44–45), Tech Watcher
ingest + synthesis + filter prompt v2 (#47–49), reorder ruling + gov
sources + promotion workflow (#52–54), Market Phase Scheduler (#56),
ADR-0012 tiered universe (#57), Federal Register regulator leg (#59),
KI-007 audit trails (#60), source-class taxonomy spec + enforcement
(#61, #63), News Analyzer spec + service (#62, #65), Filing Processor spec
+ service (#66, #68), universe README tier restructure (#67), Universe
Curator spec rewrite (#69), Pre-Trade Tier 3 membership check (#70),
Filing Processor backfill CLI (#71), Infra Mapper schema (#81), seed graph +
CLI (#82), staleness pass (#83), seed-evidence repair (#84), thesis
observation log (#85). Full list in `recent-changes.md`.

## Spine verification record

- **2026-07-08:** first live fill observed (AAPL x1 @ 313.33) — 8/9, the
  reconciliation check flagged a June-era order predating persistence.
- **2026-07-15:** 9/9 PASS — fill AAPL x1 @ 326.28, `reconciliation:
  clean=True discrepancies=0`. Spine closed.
- **2026-07-15 23:32 UTC:** first autonomous signal → order. Fixture fired
  on `late-cycle-melt-up`, chain ran signal → intent → approval → Alpaca
  submission unattended. Order `6315af3f` pending (market closed).
- **2026-07-16 open:** order `6315af3f` filled — first fully autonomous
  trade, signal through fill. Fixture disarmed after.
- **2026-07-17:** post-upgrade smoke (consumer groups + librarian +
  ollama 0.32.0 + RTX 2070 Super): submission/persistence/audit passed;
  after-hours order queued, fill close-out at the 2026-07-20 open.

## Open work

- **2026-07-20 smoke fill: confirmed.** The after-hours smoke order
  (2026-07-17 16:59 ET) filled at the open — SPY x1 @ 747.85, order
  `6573fb37`, 13:33:07Z, full correlation chain intact, three minutes
  after market-phase published `open`. Residual: the nightly
  reconciliation verdict for that session was never captured in these
  docs — pull `operations.reconciliation-completed` once and record
  `clean=True` to formally close the rebuilt-stack certification.
- **v2 re-filter ran 2026-07-18: 0/246 kept.** The five v1 false positives
  are gone, but the v1 borderline-real item was also rejected and cannot be
  identified for a false-negative audit (KI-007 — fixed in PR #60; that
  batch's verdicts are gone for good, but every batch after the next
  rebuild keeps its history). The Qwen-quality verdict (DQ-006) now rests
  on spot-checking v2 rejection reasons and on the next live batches'
  behavior.
- **Fixture disarm verification:** `docker logs shrap_strategy_fixture`
  should show `"enabled": false` post-rebuild (belt-and-suspenders; the
  .env flip + rebuild happened in the 2026-07-17 session).
- **Retry-backoff for systemic errors:** scoped into KI-006's mitigation but
  not shipped in PR #37; fold into a consumer hygiene card (candidate
  companion: market-closed re-poll backoff — the pending SPY order polls
  Alpaca every ~10s all night).
- **Regime threshold watch:** v0.1 calibration is single-day evidence. A
  historical feature backfill would earn the thresholds.
- **#56–63 Dell rebuild: done 2026-07-19/20** (initial `up -d --build`
  left tech-watcher on the old image — `--force-recreate` required and
  now standard). Verified live 2026-07-20: market-phase published the
  real `open` 279 ms after the 13:30:00Z boundary, and the rebuilt
  tech-watcher fetched the Federal Register (200 OK) with
  `fed_register_agencies` loaded. The taxonomy rule makes promotion
  strictly harder — if the funnel goes quiet, the cluster log shows
  what it is holding and why.
- **Market-phase consumers** (regime sync skip, overnight research
  conductor, briefing) come in later cards; the News Analyzer service (#65)
  and Filing Processor service (#68) are the first deployed consumers.
- **Dell deploy pending (one session, #65–71):** force-recreate
  `filing-processor` (new service), `pre-trade-checker` (picks up the
  asyncpg pool), and `news-analyzer` (picks up the shared `market_phase`
  import) — the Tier 3 enforcement flag stays off regardless.
- **Market-phase weekend certification due 2026-07-25/26:** the service
  deployed 2026-07-19 has already shown it survives a restart; the
  `closed-day` Sat/Sun + `pre-open` Monday cycle is the remaining
  certification step.
- **THE FUNNEL IS BLOCKED (KI-009) — highest priority.** The 2026-07-27
  diagnostic found that all 8 clusters ever logged are `["arxiv"]`
  single-source. Triangulation requires ≥2 origins and ≥1 hard leg; arXiv is
  one origin and is not hard, so the funnel **cannot promote anything, ever**,
  until hard-source items survive the filter. They currently do not: 1656
  EDGAR, 117 USASpending, 113 Federal Register and 16 DOE items are ingested
  and none reach a cluster. The named false negative is in DQ-006 — a DOE
  announcement of a *fourth* reactor criticality rejected for lacking
  "independent replication." Fix is filter prompt v4, source-class aware.
  **This supersedes the earlier read that ingest health was the bottleneck**;
  a healthy USASpending leg would have its items rejected by the same filter.
- **Ingest legs die silently (KI-010).** USASpending has ingested nothing
  since 2026-07-09 (18 days) while other legs are current, and nothing
  alerted — it surfaced only from a manual per-leg count during an unrelated
  diagnostic. DOE newsroom also shows ~13 days of ingest latency. Real, but
  second to KI-009.
- **The Mapper's anchor thesis was never promoted.** `research.world_changers`
  holds one row, status `proposed`, `decided_at` and `decision_note` NULL —
  the review page reads "Proposed: 1 · Promoted: 0". The seed graph (#82) was
  built on it anyway, the Mapper's `research.world-changer-promoted` trigger
  never fired, and every seed node carries the kill criterion "world-changer
  anchor no longer 'promoted' in research.world_changers" — which, read
  literally, is *already satisfied*. Docs (including the Infra Mapper spec
  note and `first_graph.py`) call it "the promoted world-changer"; the
  database disagrees. **Mike's call:** promote it (making the docs true), or
  treat the graph as premature. Either way the anchor kill criterion is a poor
  proxy and should reference the thesis's three real criteria — which are
  good ones (unsubsidized PPA, $/kW across cohorts, licensing throughput).
- **Thesis observations are manual-only** (KI-008). `shrap-world-changer-observe`
  is Mike's keyboard. Nothing attaches observations to a thesis automatically,
  which inverts principles 6 and 10. **Deferred behind KI-009** — attaching
  pipeline hits is worthless while the filter rejects the hits worth
  attaching.
- **Graphed-but-fully-stale is unmonitored.** The spec has the Health
  Monitor surfacing "promoted but ungraphed" world-changers; a graph whose
  nodes are all `stale-evidence` (i.e. proposing zero names) raises nothing.
  Harmless while nothing consumes graph state; expensive once the weekly
  aggregation lands.
- ~~**Blocked on Mike:** DQ-004 lock-in and the 6-of-50 profile-coverage
  ruling.~~ **Both resolved 2026-07-23** (DQ-004 in `decision-queue.md`; the 44
  unprofiled names grandfathered by the same ruling, profile prerequisite
  applying only to future Tier 2 promotions). The Curator's implementation
  card **shipped** in PR #75/#76. What remains is purely operational: the
  launch-list load has never been run on the Dell, so
  `research.universe_tiers` is empty — which is what still blocks flipping
  `PRE_TRADE_CHECKER_TIER3_ENFORCEMENT`, and what step 0 above fixes.

## Local credentials policy

Alpaca paper credentials live only in local ignored `infra/.env`.

- Do not print values.
- Do not commit values.
- Check only presence/length.
- If a key appears in chat or a log, rotate it.

## Card order (Mike's ruling, 2026-07-18)

Motivating case: Valar Atomics' Ward 250 reached criticality 2026-06-18
under the DOE Reactor Pilot Program — a textbook cost-curve-crossing
signal that the funnel could not see. The confirming paper trail (DOE
award, program announcements) lives in sources the Tech Watcher spec
already lists but the deployed slice doesn't ingest. Ruling: widen the
web before deepening the funnel.

1. **Gov-sources ingest** — USASpending awards + DOE newsroom as new
   Tech Watcher source classes (SAM.gov deferred until an API key
   exists). Follows the PR #47 ingest pattern.
2. **Promotion workflow** — Mike's promote/kill action, plus a
   Mike-seeded candidate path (first seed: mass-manufactured fission
   cost-curve crossing).
3. **Intelligence Department Month 2 seeds** — News Analyzer spec +
   service, then Filing Processor spec + service, both publishing
   `intelligence.signal` (which the Tech Watcher already consumes as
   an event trigger).
4. Then the prior queue: Infrastructure Mapper, Bottleneck Scout,
   Hypothesis Generator, Evaluator.

**Progress against that order (2026-07-27):** items 1–3 shipped
(#53/#54, #65–71). Item 4's Infrastructure Mapper Month-2 scope is
complete and deployed (#81–85). Remaining in the funnel: Bottleneck Scout
(the component the entire Cisco-1999 defense rests on), Hypothesis
Generator (closes the autonomy loop), Evaluator. The Valar diagnostic and
the observation auto-attach card (KI-008) now sit ahead of Bottleneck
Scout on the same "widen the web before deepening the funnel" logic that
motivated the original reorder — the Scout consumes graph and thesis
state, and both are currently fed by hand.
