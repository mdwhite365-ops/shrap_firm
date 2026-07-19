# Current sprint status

**Last updated:** 2026-07-17 (night)
**Phase:** Month 3 / Framework #1 funnel live
**Operating mode:** Paper only. No real-money execution.

## Current focus

**All three loops are physically running.** The inner loop trades
autonomously (first fixture-originated fill 2026-07-16). The middle loop
has its registry + librarian waiting on an Evaluator. And as of tonight the
research funnel is live end to end: the Tech Watcher ingests EDGAR + arXiv
hourly, filters on local Qwen (2070 Super), and declined to propose on its
first batch because the two-source triangulation rule held — exactly the
honest behavior it was built for. First calibration finding (filter
over-flagging) was diagnosed as a prompt gap, fixed same night (PR #49).
Next: the promotion workflow, then the Infrastructure Mapper.

## Main branch state

Merged on `main` through PR #45. Highlights since the spine-close status:
consumer groups (#37), strategy registry + state machine (#38), Strategy
Librarian service (#40), Evaluator ruling — Framework #1 first, in-house
walk-forward engine (#41), LLM tier client (#42), registry seed correction +
Ollama runtime bump (#43), GPU swap + drift commit (#44–45). Full list in
`recent-changes.md`.

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
- **Re-filter under prompt v2** (after PR #49 deploys): reset
  `filter_result` for unsynthesized items, compare against the v1
  baseline (6 flagged / ~1 real). Residual error rate is the honest
  Qwen-quality datapoint for the cloud-tier decision.
- **Promotion workflow card (next):** Mike's promote/kill action on
  review-page candidates → status update + `research.world-changer-promoted`
  event. The Infrastructure Mapper has no input until this exists.
- **Fixture disarm verification:** `docker logs shrap_strategy_fixture`
  should show `"enabled": false` post-rebuild (belt-and-suspenders; the
  .env flip + rebuild happened in the 2026-07-17 session).
- **Retry-backoff for systemic errors:** scoped into KI-006's mitigation but
  not shipped in PR #37; fold into a consumer hygiene card (candidate
  companion: market-closed re-poll backoff — the pending SPY order polls
  Alpaca every ~10s all night).
- **Regime threshold watch:** v0.1 calibration is single-day evidence. A
  historical feature backfill would earn the thresholds.
- **Market-Phase Scheduler (once merged):** deploy `market-phase` on the
  Dell, confirm the startup event and the first real transitions on
  `operations.market-phase`, then certify across a full weekend (deploy
  before Friday close, verify `closed-day` Saturday/Sunday and `pre-open`
  Monday 04:00 ET). Consumers (regime sync skip, overnight research
  conductor, briefing) come in later cards.

## Local credentials policy

Alpaca paper credentials live only in local ignored `infra/.env`.

- Do not print values.
- Do not commit values.
- Check only presence/length.
- If a key appears in chat or a log, rotate it.

## Next recommended card

World-changer promotion workflow (small — unlocks the Mapper), then
Infrastructure Mapper and Bottleneck Scout seeds, then Hypothesis
Generator, then the Evaluator.
