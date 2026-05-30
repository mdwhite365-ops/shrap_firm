# Strategy Evaluator

**Department:** Research
**LLM tier:** No LLM — deterministic. The Evaluator is intentionally a numerical
machine; LLMs are explicitly excluded from any pass/fail decision because they are
calibration disasters on this kind of question. See `docs/infrastructure/llm-routing.md`.
**Status:** Draft
**Date:** 2026-05-29
**Author:** Mike White
**Version:** 0.1 (draft)

## Purpose

The Strategy Evaluator is the firm's gatekeeper. It takes a proposed strategy (from the
Hypothesis Generator, from Mike, or from a parameter refresh job), runs it through a
rigorous overfitting-aware validation pipeline, and emits a verdict: kill, hold for more
data, or promote to the next stage. The Evaluator is the single piece of the firm that
most determines whether Shrap loses money slowly or quickly. It is therefore designed to
be conservative, deterministic, and boring.

The expected base rate of strategies that survive end-to-end promotion is **at most 10%**
of proposals (target kill rate ≥ 90%). A permissive Evaluator is a worse failure mode
than a missing one — promoting noise costs real money, killing real edge only costs the
time to rediscover it. This is documented in the firm's operating principles and is
non-negotiable.

What this agent cannot do, stated clearly:
- It cannot prove a strategy has edge. Passing every test means "we have failed to
  disprove edge under our test protocol," not "edge is real." This wording is required in
  every Evaluator report.
- It cannot rule out regime-dependent edge decay. Out-of-regime testing helps but cannot
  replace live observation.
- It cannot detect look-ahead bugs in the strategy implementation itself — that is the
  Code Reviewer's job. The Evaluator assumes the strategy code is faithful to the spec.

## Trigger

- **Schedule:** Overnight queue runner starting 19:30 ET, working through the queue with
  a configurable per-job timeout. Sunday weekly run for regime-stratified re-evaluation
  of all live-paper strategies.
- **Event:** `research.hypothesis.proposed` enqueues a new job. `research.strategy.refit.request`
  enqueues a parameter refresh.
- **On-demand:** Mike can promote a job to the head of the queue.

## Cross-references

**Depends on:** Hypothesis Generator (input), market data pipeline (clean OHLCV, splits,
dividends, halts), Regime Classifier (regime labels for stratification).
**Depended on by:** Strategy Librarian, Regime Router (consumes promoted strategies),
Decision Maker, Daily Briefing, Risk Officer (uses promotion stage for sizing caps).
**Related ADRs:** ADR-0006 (envelope).
**Related architecture sections:** `docs/02-architecture.md` §Research Department,
§Strategy lifecycle, §Promotion stages.

## Inputs

| Source | Type | Description |
|---|---|---|
| PostgreSQL: `research.strategies` | Query | Strategy spec at status=`hypothesis` or refit request |
| PostgreSQL: `market_data.*` | Query | Historical OHLCV (1m, 5m, daily), corporate actions, halts |
| PostgreSQL: `research.regime_history` | Query | Labeled regime windows for stratified testing |
| Redis: queue `research.eval.queue` | Job pull | Pending jobs |
| Repo: `docs/research/eval-protocol.md` | File read | Authoritative test protocol — versioned |

## Processing

The Evaluator runs a deterministic pipeline. Each stage is gated; failure at any stage
yields a kill verdict and the pipeline stops.

1. **Spec hygiene.** Validate the strategy spec: archetype allowed, kill conditions
   declared, tickers in universe, parameter ranges bounded. Reject if not.
2. **Build the dataset.** Pull the configured backtest window (default: 5 years of daily,
   2 years of intraday for the strategy's tickers). Apply realistic transaction-cost
   model: commissions, half-spread, slippage scaled by ADV participation, plus a
   regime-conditional slippage uplift in high-VIX regimes. Borrow-cost model applied for
   shorts. Use point-in-time universe membership — no survivor bias.
3. **In-sample exploratory fit.** Optional. If the spec includes a tunable parameter
   range, run a coarse grid on the first 60% of the window. Record the *number of
   configurations tried* — this feeds the Deflated Sharpe and PBO calculations.
4. **Walk-forward validation.** Standard expanding-window walk-forward with at least 6
   folds. Train on prior fold, test on next. No peeking, ever. Combinatorial purged
   cross-validation (CPCV) with embargo equal to the strategy's max holding period.
5. **Trade-count gate.** A strategy must produce **at least 150 trades** across the
   full walk-forward (target 200) to be eligible for promotion. Fewer trades = kill,
   regardless of headline metrics. No exceptions during the sprint.
6. **Overfitting controls.**
   - **Probability of Backtest Overfitting (PBO):** Compute via Bailey-López de Prado.
     Threshold: PBO ≤ 0.5 for paper, ≤ 0.4 for small-size, ≤ 0.3 for live-paper.
   - **Deflated Sharpe Ratio (DSR):** Compute deflating for the number of configurations
     tried in step 3. DSR p-value < 0.05 required.
   - **Minimum Backtest Length (MinBTL):** Verify backtest window is long enough at the
     observed Sharpe to be statistically distinguishable from noise.
7. **Regime-stratified report.** Performance per regime label. A strategy that performs
   only in one regime is fine — it gets `regime_fit` set narrowly. A strategy whose
   performance is indistinguishable from zero in any regime gets that regime added to
   `regime_kill`.
8. **Realistic-friction stress test.** Re-run with +50% transaction costs and +1 day of
   execution lag. Sharpe must remain positive.
9. **Verdict.** Map results to one of: `kill`, `hold-for-data` (passes hygiene but
   under-traded), `promote` (passes all gates for the next stage). The verdict is a pure
   function of the metrics; no human-in-the-loop tuning at this step.
10. **Persist, publish, generate report.** Write the full result set to Postgres, publish
    a verdict event, and write a Markdown evaluation card to the repo (auto branch, never
    auto-merged into main).

### Promotion stages

A strategy moves through these stages, each requiring a separate Evaluator pass plus the
listed external gate. **No real-money execution occurs at any stage during the sprint.**
Real-money is a post-sprint decision and requires Mike's explicit ADR.

| Stage | What runs | Capital | External gate to advance |
|---|---|---|---|
| `hypothesis` | Evaluator backtest only | None | Pass walk-forward + PBO + DSR + trade-count |
| `paper` | NautilusTrader paper at notional size | None | 30 calendar days minimum + 30+ live trades + live metrics within 1 sigma of backtest |
| `small-size-paper` | NautilusTrader paper at 1/4 sizing | None | Additional 30 days + Risk Officer sign-off + no regime_kill triggered |
| `live-paper` | NautilusTrader paper at full sizing | None | 60 days + Mike's explicit promotion ADR. This is the final sprint stage. |
| `real` (POST-SPRINT) | Real broker | Real | Requires post-sprint review, separate ADR, Mike's signed approval. Not in sprint scope. |

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis stream: `research.strategy.verdict` | Event | `{strategy_id, verdict, from_stage, to_stage, metrics_ref}` |
| Redis stream: `research.strategy.killed` | Event | Emitted on kill verdict; consumed by Strategy Librarian and Regime Router |
| PostgreSQL: `research.evaluations` | Append-only insert | Full metrics blob per evaluation run |
| PostgreSQL: `research.strategies` | Update | Status transition with timestamp and evaluation_id |
| Repo: `docs/strategies/evaluations/<strategy_id>/<ts>.md` | File write | Markdown evaluation card (auto branch) |

## LangGraph structure

Not used. The Evaluator is a deterministic pipeline; LangGraph would add overhead
without benefit. Implemented as a queue-consuming Python worker.

## State

| What | Store | Notes |
|---|---|---|
| Evaluation history | PostgreSQL `research.evaluations` | Append-only; never overwritten |
| Job queue | Redis list `research.eval.queue` | At-least-once; idempotent on strategy_id+spec_hash |
| Cached datasets per ticker window | Local disk | Invalidated on market_data updates |

## Failure behavior

1. **Containment.** A bug in the Evaluator that *fails open* (promotes a bad strategy)
   is the firm's worst failure mode short of trading real money on it. Containment is
   provided by (a) the promotion stages requiring live paper observation before sizing
   up, (b) the Risk Officer's independent veto, (c) the no-real-money invariant for the
   entire sprint. A bug that *fails closed* (kills good strategies) wastes proposals but
   does not move money.
2. **Replay safety.** Fully safe. Evaluation is a pure function of the strategy spec, the
   historical data, and the protocol version. Replays are deterministic. The protocol
   version is recorded with every evaluation so historical results remain interpretable
   if the protocol changes.
3. **Degraded operation.** The firm can run without the Evaluator for the duration of any
   currently-promoted strategies' performance windows. No new promotions occur, and
   existing live-paper strategies continue under the Risk Officer's caps. Mike should
   halt the Hypothesis Generator's batch if the Evaluator is down >48h to prevent queue
   buildup.

## Sprint scope

- Month 2: Walk-forward + trade-count gate + realistic costs + verdict pipeline +
  promotion to paper.
- Month 3: PBO, DSR, CPCV, regime-stratified reports.
- Month 4: Sunday re-evaluation of all live-paper strategies. Decay detection.

## Deferred

- Monte Carlo bootstrap of return distributions (nice-to-have, not required for verdict).
- Alternative cost models for crypto.
- Multi-strategy portfolio-level evaluation — the Bayesian Updater owns that.
- Any LLM involvement in verdict.

## Open questions

- **Sharpe and DSR thresholds per stage:** Need calibration against the existing
  47%-win-rate baseline. Blocks: first promotion. Owner: Mike.
- **Borrow-cost data source:** No clean retail feed identified. Blocks: short strategies
  passing realistic-friction test. Owner: Mike (may decide to disallow shorts in sprint).
- **Should the Evaluator have authority to *demote* a live-paper strategy without Mike's
  approval?** Currently yes if regime_kill triggers. Blocks: trust progression. Owner:
  Mike.
