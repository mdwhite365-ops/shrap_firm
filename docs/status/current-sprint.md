# Current sprint status

**Last updated:** 2026-07-19 (late night)
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
News Analyzer spec. The funnel now triangulates on originating
institutions, not feed names, and every pre-synthesis decision leaves a
queryable trace. Next: News Analyzer service card (spec + Alpaca vendor
accepted by merge), then the Filing Processor spec.

## Main branch state

Merged on `main` through PR #63. Highlights since the spine-close status:
consumer groups (#37), strategy registry + state machine (#38), Strategy
Librarian service (#40), Evaluator ruling — Framework #1 first, in-house
walk-forward engine (#41), LLM tier client (#42), registry seed correction +
Ollama runtime bump (#43), GPU swap + drift commit (#44–45), Tech Watcher
ingest + synthesis + filter prompt v2 (#47–49), reorder ruling + gov
sources + promotion workflow (#52–54), Market Phase Scheduler (#56),
ADR-0012 tiered universe (#57), Federal Register regulator leg (#59),
KI-007 audit trails (#60), source-class taxonomy spec + enforcement
(#61, #63), News Analyzer spec (#62). Full list in `recent-changes.md`.

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

- **Monday 2026-07-20 open:** the after-hours smoke order (2026-07-17
  16:59 ET) fills; confirm `execution.order.filled` + clean
  reconciliation to certify the rebuilt stack end to end.
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
- **Dell rebuild pending (one session covers everything merged today):**
  `tech-watcher` rebuild picks up the Federal Register source (#59), the
  KI-007 audit tables (#60, created by `ensure_schema` on boot), and the
  taxonomy triangulation rule (#63); `market-phase` (#56) comes up new.
  Then: confirm the market-phase startup event, watch the first real
  transitions Monday, and certify across the next full weekend
  (`closed-day` Sat/Sun, `pre-open` Monday 04:00 ET). Note the taxonomy
  rule makes promotion strictly harder — if the funnel goes quiet, the
  cluster log shows what it is holding and why.
- **Market-phase consumers** (regime sync skip, overnight research
  conductor, briefing) come in later cards; the News Analyzer spec (#62)
  is the first committed consumer.
- **ADR-0012 follow-ups (accepted 2026-07-19, unscheduled):** restructure
  `docs/universe/README.md` around the three tiers (the 50-name list
  becomes the Tier 3 launch proposal, still awaiting DQ-004 lock-in);
  update the Universe Curator spec from derived-only consumer to Tier 2/3
  owner + transition-event publisher; Pre-Trade Checker Tier 3 membership
  check card (needs a Tier 3 data source decision).

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
