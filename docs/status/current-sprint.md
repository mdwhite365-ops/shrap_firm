# Current sprint status

**Last updated:** 2026-07-23 (afternoon)
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

## Main branch state

Merged on `main` through PR #71. Highlights since the spine-close status:
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
Filing Processor backfill CLI (#71). Full list in `recent-changes.md`.

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
- **Blocked on Mike:** DQ-004 lock-in and the 6-of-50 profile-coverage
  ruling (Universe Curator spec, open questions) gate the Curator's first
  implementation card (`research.universe_tiers` +
  `research.universe_staging` stores, the four transition events, the
  Mike approval CLI, and the launch-list load) — which in turn is what
  allows flipping `PRE_TRADE_CHECKER_TIER3_ENFORCEMENT`.

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
