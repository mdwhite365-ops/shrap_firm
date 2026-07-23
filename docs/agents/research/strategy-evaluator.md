# Strategy Evaluator

**Department:** Research
**LLM tier:** `no-llm` for the stats core — deterministic. The Evaluator is intentionally a numerical
machine; LLMs are explicitly excluded from any pass/fail/demote decision
because they are calibration disasters on this kind of question. A `cloud-default`-tier
call MAY be used solely for human-readable narration of an already-decided kill/promote
verdict (kill-criteria explainer text); the narration is downstream of the decision and
cannot influence it. See `docs/infrastructure/llm-routing.md` and `docs/infrastructure/llm-registry.md`.
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** Draft
**Date:** 2026-05-30
**Author:** Mike White
**Version:** 0.1 (draft)

## Purpose

The Strategy Evaluator is the firm's gatekeeper. It takes a proposed strategy
(from the Hypothesis Generator, from Mike, or from a parameter-refresh job),
runs it through a rigorous overfitting-aware validation pipeline, and emits a
verdict: kill, hold for more data, or promote. Under the new Research thesis
(ADR-0007) it also acts as the consumer of upstream "thesis-broken" events
from Tech Watcher, Bottleneck Scout, and Infrastructure Mapper, demoting any
strategy whose anchor has been invalidated.

The Evaluator is the single piece of the firm that most determines whether
Shrap loses money slowly or quickly. It is designed to be conservative,
deterministic, and boring.

Expected base rate of strategies that survive end-to-end promotion: **≤10%**
of proposals (target kill rate ≥90%). A permissive Evaluator is a worse
failure mode than a missing one — promoting noise costs real money; killing
real edge only costs the time to rediscover it. **Kill more aggressively than
you promote** is non-negotiable.

What this agent cannot do, stated clearly:

- It cannot prove a strategy has edge. Passing every test means "we have
  failed to disprove edge under our test protocol," not "edge is real." This
  wording is required in every Evaluator report.
- It cannot verify the anchor thesis itself. If Tech Watcher says a
  world-changer is real and it isn't, the Evaluator will happily promote
  strategies built on top — until the thesis-broken event lands.
- It cannot detect look-ahead bugs in strategy implementation — that is the
  Code Reviewer's job. The Evaluator assumes strategy code is faithful to
  spec.
- It cannot rule out regime-dependent decay across sizing modifiers — regime
  is now a sizing input, not a gate, so the Evaluator stratifies but does
  not gate on it.

## Trigger

- **Schedule:** Overnight queue runner starting 19:30 ET, per-job timeout
  configurable. Sunday weekly run for stratified re-evaluation of all
  paper-stage strategies.
- **Event:** Subscribes to:
  - `research.hypothesis.proposed` → enqueues a fresh evaluation.
  - `research.strategy.refit.request` → enqueues a parameter refresh.
  - **Thesis-broken events (new):**
    - `research.world-changer.thesis-broken` from Tech Watcher.
    - `research.bottleneck.no-longer-binding` from Bottleneck Scout.
    - `research.infra.graph.node-failed` from Infrastructure Mapper.
    These do not enqueue a re-evaluation; they directly enqueue a **kill
    review** job for every strategy referencing the named anchor.
- **On-demand:** Mike can promote a job to the head of the queue.

## Cross-references

**Depends on:** Hypothesis Generator (proposals), market data pipeline (clean
OHLCV, splits, dividends, halts), Regime Classifier
(`docs/agents/intelligence/regime-classifier.md`) for regime windows used in
stratified reporting, Tech Watcher / Bottleneck Scout / Infrastructure Mapper
(thesis-broken events).
**Depended on by:** Strategy Librarian, Decision Maker, Daily Briefing Agent,
Risk Officer (uses promotion stage for sizing caps).
**Related ADRs:** ADR-0006 (envelope), ADR-0007 (Research thesis).
**Related architecture sections:** `docs/02-architecture.md` §Research
Department, §Strategy lifecycle, §Promotion stages.

## Inputs

| Source | Type | Description |
|---|---|---|
| PostgreSQL: `research.strategies` | Query | Strategy spec at status=`hypothesis` or refit request |
| PostgreSQL: `market_data.*` | Query | Historical OHLCV (1m, 5m, daily), corporate actions, halts |
| PostgreSQL: `intelligence.regime_history` | Query | Labeled regime windows for stratified reporting |
| PostgreSQL: `research.world_changers` | Query | Current status of each world-changer anchor |
| PostgreSQL: `research.bottlenecks` | Query | Current binding status of each bottleneck anchor (deferred — Bottleneck Scout unbuilt, table has no rows; see Sprint scope 2026-07-23) |
| Redis: queue `research.eval.queue` | Job pull | Pending evaluation, refit, and kill-review jobs |
| Redis: `research.world-changer.thesis-broken` | Event | Triggers kill-review enqueue |
| Redis: `research.bottleneck.no-longer-binding` | Event | Triggers kill-review enqueue (deferred — emitter unbuilt; see Sprint scope 2026-07-23) |
| Redis: `research.infra.graph.node-failed` | Event | Triggers kill-review enqueue (deferred — emitter unbuilt; see Sprint scope 2026-07-23) |
| Repo: `docs/research/eval-protocol.md` | File read | Authoritative test protocol — versioned |

## Processing

The Evaluator runs three pipelines depending on job type: **evaluation**,
**refit**, and **kill review**. Each stage is gated; failure at any stage
yields a kill verdict and the pipeline stops.

### Evaluation pipeline (hypothesis → paper)

1. **Spec hygiene.** Validate the strategy spec: archetype is
   `infra-graph-play` or `bottleneck-rotation`, kill criteria declared and
   include the required upstream-event clauses (see Hypothesis Generator
   spec), tickers are in the current active universe, parameter ranges
   bounded, regime field is a sizing modifier and not an entry/exit gate.
   Reject if not.
2. **Anchor freshness.** Confirm the anchor is still live: world-changer in
   `promoted` status, or bottleneck in `validated-binding` status. If the
   anchor is `at-risk` or already broken, kill immediately with reason
   `anchor-not-live`.
3. **Build the dataset.** Pull the configured backtest window (default: 5
   years daily, 2 years intraday for the strategy's tickers). Apply realistic
   transaction-cost model: commissions, half-spread, slippage scaled by ADV
   participation, regime-conditional slippage uplift in high-VIX regimes.
   Borrow-cost model for shorts. Point-in-time universe membership — no
   survivor bias.
4. **In-sample exploratory fit.** Optional. If the spec includes a tunable
   parameter range, run a coarse grid on the first 60% of the window. Record
   the number of configurations tried — feeds Deflated Sharpe and PBO.
5. **Walk-forward validation.** Expanding-window walk-forward, ≥6 folds.
   Train on prior fold, test on next. No peeking. **Combinatorial purged
   cross-validation (CPCV)** with embargo equal to the strategy's max holding
   period.
6. **Trade-count gate.** A strategy must produce **at least 150 trades**
   across the full walk-forward (target 200) to be eligible for promotion.
   Fewer = kill, regardless of headline metrics. No exceptions during the
   sprint. Long-horizon infra-graph plays that cannot meet this gate are
   killed — there is no special case.
7. **Overfitting controls.**
   - **PBO (Bailey–López de Prado):** ≤ 0.5 for paper, ≤ 0.4 for small-size,
     ≤ 0.3 for live-paper.
   - **Deflated Sharpe Ratio (DSR):** deflated for configurations tried.
     p-value < 0.05 required.
   - **Minimum Backtest Length (MinBTL):** verify backtest window is long
     enough at observed Sharpe to be statistically distinguishable from
     noise.
8. **Regime-stratified report.** Performance per regime label. A strategy
   that performs only in one regime is fine — its `regime_sizing_modifier`
   gets tightened toward that regime. A strategy whose performance is
   indistinguishable from zero in any regime gets that regime's modifier set
   to 0.0. Regime is never used to outright kill at this stage unless every
   regime returns zero, in which case kill for absence of edge.
9. **Realistic-friction stress test.** Re-run with +50% transaction costs
   and +1 day of execution lag. Sharpe must remain positive.
10. **Verdict.** Map results to one of: `kill`, `hold-for-data`, `promote`.
    Verdict is a pure function of the metrics; no human-in-the-loop tuning.
11. **Persist, publish, generate report.** Write full result set to Postgres,
    publish a verdict event, write a Markdown evaluation card to the repo
    (auto branch, never auto-merged). Implementation Agent may **not**
    modify trading or risk policy via this card without Mike's explicit
    approval.

### Refit pipeline

Same as evaluation but operates on an already-promoted strategy with bounded
parameter perturbations. Output verdicts: `accept-refit`, `reject-refit-keep-prior`,
`kill`.

### Kill-review pipeline (NEW)

Triggered by upstream thesis-broken events. Per affected strategy:

1. **Load the trigger event.** Identify the broken anchor: world-changer ID,
   bottleneck ID, or graph node ID.
2. **Match.** Query `research.strategies` for every strategy whose `anchor`
   references the broken entity.
3. **Demote.** Each match is immediately demoted to `kill-review` status.
   Paper / small-size / live-paper executions are halted via a
   `research.strategy.halt` event consumed by the paper-trading runner. No
   further fills occur.
4. **Diagnose.** Pull the last 30 days of live or paper performance. Compute:
   did performance already deteriorate before the upstream event? (If yes,
   this is supporting evidence that the anchor was truly load-bearing. If
   no, evaluator notes that the kill was anchor-driven, not performance-
   driven.)
5. **Verdict.** Three outcomes:
   - `kill-confirmed` (default): anchor is gone, strategy is dead. Status →
     `killed`. Strategy Librarian and Risk Officer notified.
   - `kill-deferred-pending-mike` (rare): strategy is showing strong
     independent performance not obviously dependent on the broken anchor.
     Status → `kill-review-mike`, halt remains in place, Mike must
     explicitly re-classify the anchor or accept the kill.
   - `false-alarm` (rarer): the upstream event is later retracted by its
     source agent (see that agent's retraction event). Status returns to
     prior. Halt lifted. A retraction must be event-sourced; the Evaluator
     never decides on its own that an upstream event was wrong.

**Bias:** kill-confirmed is the default. Kill-deferred requires a strong,
documented reason. This preserves "kill more aggressively than you promote"
at the demotion layer too.

### Promotion stages

A strategy moves through these stages, each requiring a separate Evaluator
pass plus the listed external gate. **No real-money execution occurs at any
stage during the sprint.** Real-money is a post-sprint decision and requires
Mike's explicit ADR.

| Stage | What runs | Capital | External gate to advance |
|---|---|---|---|
| `hypothesis` | Evaluator backtest only | None | Pass walk-forward + PBO + DSR + trade-count + anchor-fresh |
| `paper` | NautilusTrader paper at notional size | None | 30 calendar days + 30+ live trades + live metrics within 1σ of backtest + anchor still live |
| `small-size-paper` | NautilusTrader paper at 1/4 sizing | None | +30 days + Risk Officer sign-off + no thesis-broken event triggered |
| `live-paper` | NautilusTrader paper at full sizing | None | +60 days + Mike's explicit promotion ADR. Final sprint stage. |
| `real` (POST-SPRINT) | Real broker | Real | Requires post-sprint review, separate ADR, Mike's signed approval. Not in sprint scope. |

At every stage, a thesis-broken event from any upstream agent demotes the
strategy directly to `kill-review`, bypassing the normal stage transitions.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis stream: `research.strategy.verdict` | Event | `{strategy_id, verdict, from_stage, to_stage, metrics_ref, trigger}` |
| Redis stream: `research.strategy.killed` | Event | Emitted on kill verdict, including kill-confirmed from kill-review. Consumed by Strategy Librarian and Risk Officer |
| Redis stream: `research.strategy.halt` | Event | Halts paper execution on a demoted strategy |
| Redis stream: `research.strategy.demoted` | Event | Emitted on kill-review entry, includes `trigger_event_id` |
| PostgreSQL: `research.evaluations` | Append-only insert | Full metrics blob per evaluation run |
| PostgreSQL: `research.strategies` | Update | Status transition with timestamp, evaluation_id, and trigger reference |
| Repo: `docs/strategies/evaluations/<strategy_id>/<ts>.md` | File write | Markdown evaluation card on auto branch, never auto-merged |

Every event carries the ADR-0006 envelope.

## LangGraph structure

Not used. The Evaluator is a deterministic pipeline; LangGraph would add
overhead without benefit. Implemented as a queue-consuming Python worker
with three job-type branches (evaluation, refit, kill-review).

## State

| What | Store | Notes |
|---|---|---|
| Evaluation history | PostgreSQL `research.evaluations` | Append-only; never overwritten |
| Job queue | Redis list `research.eval.queue` | At-least-once; idempotent on `(strategy_id, spec_hash, job_type)` |
| Cached datasets per ticker window | Local disk | Invalidated on market_data updates |
| Upstream-event log | PostgreSQL `research.upstream_events` | Append-only, retraction-aware |

## Failure behavior

1. **Containment.** A bug that *fails open* (promotes a bad strategy) is the
   firm's worst failure mode short of trading real money on it. Containment:
   (a) promotion stages require live paper observation before sizing up,
   (b) Risk Officer independent veto, (c) no-real-money invariant for the
   entire sprint, (d) thesis-broken events provide an out-of-band kill
   mechanism that does not depend on metrics alone. A bug that *fails closed*
   (kills good strategies, or over-reacts to a thesis-broken event) wastes
   proposals but does not move money. We prefer fail-closed.
2. **Replay safety.** Evaluation and refit pipelines are pure functions of
   strategy spec, historical data, and protocol version — fully replayable.
   Kill-review depends on the upstream event log; with the event log
   persisted, kill-review is also replayable. Protocol version is recorded
   with every evaluation.
3. **Degraded operation.** The firm can run without the Evaluator for the
   duration of any currently-promoted strategies' performance windows. No
   new promotions occur; existing paper strategies continue under Risk
   Officer caps. If the Evaluator is down >24h, thesis-broken events queue
   up and kills are delayed — Mike should manually halt affected strategies
   if any upstream agent emits a thesis-broken event during the outage.
   Mike should halt the Hypothesis Generator's batch if the Evaluator is
   down >48h to prevent queue buildup.

## Sprint scope

**Sequencing and engine (Mike's ruling, 2026-07-15):**

1. The Evaluator is **not implemented until the Framework #1 agents exist**
   (Tech Watcher → Infrastructure Mapper → Bottleneck Scout). The
   anchor-freshness gate (Processing step 2) is load-bearing under ADR-0007;
   evaluating unanchored strategies was rejected in favor of building the
   funnel top-down so anchors are real from the first evaluation.

   > **Superseded 2026-07-23 (Mike's ruling) — see "Resequencing" below.**
   > Item 1's sequencing constraint no longer holds. Item 2 (backtest
   > engine) is unaffected and remains in force as written.

2. **Backtest engine: in-house walk-forward** (numpy/pandas expanding-window
   over `market_data.*`), deterministic, no new dependencies. VectorBT PRO
   is re-scoped as a gated upgrade — the gate is evaluation volume or
   strategy complexity the in-house harness cannot handle honestly, and the
   purchase is Mike's decision at that gate. Architecture-doc references to
   VectorBT PRO as the Evaluator engine are updated when the Evaluator
   implementation card lands.

**Resequencing (Mike's ruling, 2026-07-23) — supersedes item 1 above:**

Proceed to the Evaluator now, ahead of Infrastructure Mapper and Bottleneck
Scout. Recorded honestly, the motivation is need, not impatience: the
paper-trading spine is certified end-to-end but has no strategy source —
the Strategy Fixture is disarmed — so the firm cannot demonstrate whether
the funnel-to-paper path works or doesn't until a real strategy flows
through evaluation to paper trading. The 2026-07-15 ruling's substance is
also only partly overridden, not discarded: the funnel now exists and has
produced the firm's first promoted world-changer anchor (mass-manufactured
fission cost-curve crossing, promoted 2026-07-18), so anchor-freshness
(Processing step 2) is checkable against real state for
world-changer-anchored strategies — which is exactly what item 1 was
protecting. It is not yet checkable for the bottleneck leg, because
Bottleneck Scout is unbuilt and `research.bottlenecks` has no rows to
check against. The adjusted first-card scope holds that line rather than
faking it:

- **Anchor-freshness gate wires to `research.world_changers` ONLY.** The
  `research.bottlenecks` leg is deferred until the Bottleneck Scout
  exists. Consequently the `bottleneck-rotation` archetype is **not
  evaluable yet** — spec hygiene (Processing step 1) rejects it with an
  explicit reason until that table exists. This is fail-closed, consistent
  with the spec's bias.
- **`research.infra.graph.node-failed` and
  `research.bottleneck.no-longer-binding` kill-review triggers are
  likewise deferred** — their emitters (Infrastructure Mapper, Bottleneck
  Scout) don't exist yet. `research.world-changer.thesis-broken` is in
  scope.
- **First strategy proposals may be Mike-seeded.** The spec already lists
  Mike as a proposal source (see Trigger); the Hypothesis Generator is not
  a prerequisite for the first card.
- **Data prerequisite:** a `market_data.daily_bars` historical store +
  backfill card is in flight (branch `phase1/market-data-daily-store`) —
  the Evaluator first card builds on it.

Phase scope (relative to Framework #1 completion, not calendar months):

- First card: Walk-forward + trade-count gate + realistic costs + verdict
  pipeline + promotion to paper. Anchor-freshness check wired to
  `research.world_changers` and `research.bottlenecks` tables (which exist
  by then).

  > **Superseded 2026-07-23:** per the resequencing ruling above, the
  > first card wires `research.world_changers` only. The
  > `research.bottlenecks` leg, and any archetype or trigger that depends
  > on it, is deferred until Bottleneck Scout exists.

- Then: PBO, DSR, CPCV, regime-stratified reports. Kill-review pipeline
  consuming all three thesis-broken event types. Halt event wired to paper
  runner.
- Then: Sunday re-evaluation of all paper-stage strategies. Decay
  detection. False-alarm retraction handling.

## Deferred

- Monte Carlo bootstrap of return distributions (nice-to-have).
- Alternative cost models for crypto.
- Multi-strategy portfolio-level evaluation — Bayesian Updater owns that.
- Any LLM involvement in verdict.
- Auto-resurrection of `kill-confirmed` strategies on anchor recovery —
  resurrection requires a new proposal through the Hypothesis Generator.

## Open questions

- **Sharpe and DSR thresholds per stage:** Need calibration against the
  existing 47%-win-rate baseline. Blocks: first promotion. Owner: Mike.
- **Borrow-cost data source:** No clean retail feed identified. Blocks:
  short legs of bottleneck-rotation strategies passing realistic-friction
  test. Owner: Mike (may decide to disallow shorts in sprint).
- **Should `kill-deferred-pending-mike` ever exist, or should anchor-driven
  kills always be auto-confirmed?** Currently allowed but biased against.
  Blocks: handling of strategies that turn out to have orthogonal edge.
  Owner: Mike, after first thesis-broken event in live operation.
- **Trade-count gate vs long-horizon infra-graph plays:** 150-trade floor
  kills most multi-quarter horizon strategies. This is intentional for the
  sprint but is a known tension with the new thesis. Blocks: ever promoting
  a slow-burn infra-graph play. Owner: Mike + Hypothesis Generator owner,
  post-sprint.
- **Idempotency of thesis-broken events on re-delivery:** Current design
  keys on `(strategy_id, trigger_event_id)`. If an upstream agent re-emits
  with a new event ID for the same underlying break, we will demote twice.
  Acceptable for sprint; revisit before real-money.
