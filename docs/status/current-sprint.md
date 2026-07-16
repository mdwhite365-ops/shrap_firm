# Current sprint status

**Last updated:** 2026-07-15 (late evening)
**Phase:** Month 3 / spine closed → Research middle loop open
**Operating mode:** Paper only. No real-money execution.

## Current focus

**The middle loop is open.** The paper spine closed 9/9 on 2026-07-15; the
same evening, Mike armed the strategy fixture and the firm submitted its
first fully autonomous order (SPY buy x1, ~5s signal-to-broker, no human in
the loop — fill pending the 2026-07-16 open). The strategy registry — the
middle loop's system of record — merged as PR #38. Next build work is the
Strategy Librarian service.

## Main branch state

Merged on `main` through PR #38. Since the spine-close status (PR #36):

1. PR #37: all stream consumers moved to Redis consumer groups with
   acknowledged, persisted offsets (`src/shrap/events/groups.py`). KI-006
   resolved; restart replay is gone at the root.
2. PR #38: strategy registry schema + lifecycle state machine
   (`src/shrap/research/strategy_registry.py`): `research.strategies` +
   append-only `research.strategy_transitions`, enforced promotion path
   `hypothesis → paper → small-size-paper → live-paper` with kill-review
   states, `real` unrepresentable by design. Draft Strategy Librarian spec
   in `docs/agents/research/strategy-librarian.md`.

## Spine verification record

- **2026-07-08:** first live fill observed (AAPL x1 @ 313.33) — 8/9, the
  reconciliation check flagged a June-era order predating persistence.
- **2026-07-15:** 9/9 PASS — fill AAPL x1 @ 326.28, `reconciliation:
  clean=True discrepancies=0`. Spine closed.
- **2026-07-15 23:32 UTC:** first autonomous signal → order. Fixture fired
  on `late-cycle-melt-up`, chain ran signal → intent → approval → Alpaca
  submission unattended. Order `6315af3f` pending (market closed).

## Open work

- **Verify the first autonomous fill (2026-07-16 after 09:30 ET):** confirm
  `execution.order.filled`, persisted fill row, clean reconciliation. Then
  **disarm the fixture** (`STRATEGY_FIXTURE_ENABLED=false` + recreate) — its
  proof job is done; the next armed path should be a registry-promoted
  strategy.
- **Dell redeploy:** running containers predate PR #36. After the fill
  lands: `git pull` + full `docker compose up -d --build` to pick up
  consumer groups.
- **Strategy Evaluator minimal harness card:** blocked on Mike's scoping
  ruling — backtest engine (spec says VectorBT PRO, which is paid and not
  yet licensed/installed) and anchor-freshness handling (Framework #1
  tables `research.world_changers` / `research.bottlenecks` don't exist
  yet). Whichever way Mike rules, the Evaluator spec gets amended in the
  same card (drift rule).
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

Strategy Evaluator minimal backtest harness (after Mike's engine/anchor
scoping ruling), then the Hypothesis Generator seed path.
