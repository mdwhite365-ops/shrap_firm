# Current sprint status

**Last updated:** 2026-07-17
**Phase:** Month 3 / middle loop open → Framework #1 funnel
**Operating mode:** Paper only. No real-money execution.

## Current focus

**First autonomous trade complete; substrate upgraded; Framework #1 next.**
The fixture-originated SPY order filled at the 2026-07-16 open — signal to
fill, no human in the loop — and the fixture is now disarmed. The registry +
librarian arc (PRs #38/#40) is merged and deployed. The LLM substrate is
live: tier client (PR #42), corrected registry seed `qwen3.5:9b-q4_K_M`
(PR #43), RTX 2070 Super serving it on GPU (PRs #44–45). Next build work is
the Tech Watcher seed — the funnel's first agent and the firm's first
LLM-calling agent, local-only per Mike's ruling.

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
- **Tech Watcher seed card (next):** Framework #1 opener, first
  LLM-calling agent. Local-only on the tier client per Mike's ruling —
  no cloud billing required. Carried design note: qwen3.5 thinks by
  default; the tier client needs a `think: false` toggle for bulk
  classification calls.
- **Fixture disarm verification:** `docker logs shrap_strategy_fixture`
  should show `"enabled": false` post-rebuild (belt-and-suspenders; the
  .env flip + rebuild happened in the 2026-07-17 session).
- **Retry-backoff for systemic errors:** scoped into KI-006's mitigation but
  not shipped in PR #37; fold into a consumer hygiene card (candidate
  companion: market-closed re-poll backoff — the pending SPY order polls
  Alpaca every ~10s all night).
- **Regime threshold watch:** v0.1 calibration is single-day evidence. A
  historical feature backfill would earn the thresholds.

## Local credentials policy

Alpaca paper credentials live only in local ignored `infra/.env`.

- Do not print values.
- Do not commit values.
- Check only presence/length.
- If a key appears in chat or a log, rotate it.

## Next recommended card

Tech Watcher seed (Framework #1 opener, first LLM-calling agent), then
Infrastructure Mapper and Bottleneck Scout seeds, then Hypothesis
Generator, then the Evaluator.
