# Strategy Evaluator — Test Protocol

**Version:** 0.1 (draft)
**Date:** 2026-07-26
**Owner:** Mike White
**Status:** Draft — versioned; `PROTOCOL_VERSION = "0.1"`
**Serves:** `docs/agents/research/strategy-evaluator.md` (the spec references this
file as the authoritative test protocol).

## What this document is

The Strategy Evaluator spec points here for the exact numbers and rules the
Evaluator applies. The spec is the *contract*; this is the *protocol*. Every
persisted evaluation is stamped with the `PROTOCOL_VERSION` above so a result
is reproducible against the protocol that produced it. When the protocol
changes in a way that makes prior evaluations non-comparable, the version bumps
and old evaluations keep their old stamp.

This v0.1 captures exactly what the Evaluator's **first card** implements. It is
deliberately less than the full spec: overfitting statistics (PBO, DSR, CPCV),
regime-stratified reporting, the kill-review pipeline, and the overnight queue
runner are all **deferred** (see §8). What is here is the deterministic core —
walk-forward, realistic costs, a friction stress test, the trade-count gate, and
a verdict mapping that is a pure function of the metrics.

The Evaluator cannot prove edge. **Passing every test means "we have failed to
disprove edge under our test protocol," not "edge is real."** That wording is
required in every evaluation card and is not decoration: the whole design is
built to kill more aggressively than it promotes, because promoting noise costs
real money and killing real edge only costs the time to find it again.

## 1. Inputs and eligibility (spec Processing step 1–2)

A strategy is read from `research.strategies` at status `hypothesis`. Before any
backtest runs, it must clear spec hygiene:

- **Archetype must have a declared evaluation policy.** Policies live in
  `ARCHETYPE_POLICIES` (`pipeline.py`) — one table, one place, per archetype:

  | Archetype | Evaluable | Anchor gate |
  |---|---|---|
  | `infra-graph-play` (Framework #1) | yes | **required** |
  | `technical-catalyst` (Framework #3) | yes | not applicable |
  | `bottleneck-rotation` | **refused** — `research.bottlenecks` has no rows until Bottleneck Scout exists (resequencing ruling, 2026-07-23) | required |

  An archetype absent from the table is **refused**, fail-closed: gates we have
  not decided on are not gates we get to guess at.
- **Tickers must be Tier-3 eligible** — present in `research.universe_tiers` at
  tier `active` (read-only; the Universe Curator owns that table).
- **Parameters must be bounded** — every numeric parameter must be finite and
  carry a declared `[lo, hi]` bound in `spec.param_bounds`, and its value must
  lie inside it.
- **Kill criteria must be declared** (non-empty).
- **Regime must be a sizing modifier, not a gate** — a `spec.regime_gate` is
  refused.

**Refusal is not a kill.** A malformed or not-yet-evaluable spec has not been
evaluated, so it does not earn a terminal verdict: the strategy stays at
`hypothesis` and the CLI exits non-zero with the reason. This is distinct from a
*kill verdict*, which is reserved for strategies we did evaluate and found dead
(dead anchor, too few trades, no edge, edge that dies under friction). Both are
fail-closed; neither promotes anything.

**Anchor freshness (spec step 2) — only where the archetype requires it.** For
an anchor-bearing archetype the strategy's `anchor` must reference a
world-changer that is currently `promoted` in `research.world_changers`. The
anchor JSONB references it by `world_changer_id` (or `candidate_id`). If the
anchor is missing, unresolved, or not `promoted`, the verdict is `kill` with
reason `anchor-not-live`, and the backtest does not run. This card wires the
anchor-freshness gate to `research.world_changers` **only**; the bottleneck leg
is deferred with Bottleneck Scout.

For an archetype whose policy sets `requires_anchor=False`, `world_changers` is
**not queried at all** and any anchor the record happens to carry is ignored.
The policy decides, never the payload — otherwise the exemption would depend on
whoever wrote the row remembering to clear a field.

**Two different meanings of `anchor_fresh=False`.** Once an archetype can be
anchor-less the flag is ambiguous on its own, so `research.evaluations` carries
`anchor_required` alongside it (added by `ALTER`, `DEFAULT TRUE` — correct for
every row written before this change, when `infra-graph-play` was the only
evaluable archetype). The dead-anchor set is
`anchor_required AND NOT anchor_fresh`. Evaluation cards and the CLI summary
render three states — `live`, `not-live`, `not-required` — and never a bare
boolean, because a card reading "anchor: not live" for a strategy that never
claimed a thesis reports a falsification that did not happen.

**Why this is not a loosening.** Removing a gate to let more strategies through
is how a firm promotes noise. This removes a gate *from the archetype it was
never about*: a `technical-catalyst` strategy's thesis is price and flow
structure, so a world-changer anchor is not a weaker falsifier for it — it is
not a falsifier at all, and requiring one produced anchors invented to satisfy
the gate. Every other gate (Tier-3 membership, bounded params, declared kill
criteria, regime-as-modifier, the trade-count floor, the Sharpe floor, the
friction stress) applies unchanged to both archetypes.

## 2. Dataset (spec step 3)

Daily bars are read from `market_data.daily_bars` (IEX feed, `adjustment=all` —
splits and dividends, the correct basis for total-return backtesting) over the
configured window (default 5 years). Multiple tickers are aligned on the
**intersection** of their session dates — no forward-fill, no fabricated bars.
A dataset too short to form the configured folds yields `hold-for-data`
(reason `insufficient-data`), never a kill: lack of data is not evidence of no
edge.

> **Recorded project fact.** IEX volumes are a fraction of the SIP consolidated
> tape, so volume — and any ADV-scaled slippage derived from it — reads
> differently than a live desk on SIP would see. See
> `docs/infrastructure/market-data.md`.

## 3. Walk-forward (spec step 5)

Expanding-window walk-forward, **6 folds** (default; ≥6 required), train on
prior / test on next, no peeking. The out-of-sample range (after warmup and lag
headroom) is partitioned into 6 contiguous reporting folds; fold *i*'s "train"
is the expanding history before its first period.

**No-peek is structural, not conventional.** The holding period `[close[p],
close[p+1]]` uses a target decided at bar `p − execution_lag` from a window that
exposes only bars `0..p−execution_lag`. A strategy cannot see the bar whose
return it is about to earn.

**Fixed parameters this card.** In-sample grid fitting (spec step 4) is
deferred, so a fixed-parameter strategy's expanding walk-forward reduces to one
continuous out-of-sample backtest partitioned into the reporting folds. The
fold geometry is already the shape per-fold refitting will use when that lands.

**Metrics** (per fold and aggregate): total return, annualized Sharpe (sample
std, ddof=1, ×√252), max drawdown (worst peak-to-trough, a non-negative
fraction), and trade count (a trade = any rebalance leg that changes a ticker's
target weight).

## 4. Transaction costs (spec step 3)

Costs are charged per rebalance as a fraction of the normalized book:

| Component | v0.1 default | Notes |
|---|---|---|
| Commission | 0.5 bps | flat, on traded notional |
| Half-spread | 2.0 bps | crossing half the quoted spread |
| Slippage | 10.0 bps per 100% ADV | scales linearly with participation = traded notional / trailing-20-day avg dollar volume; capped at 100% |
| Borrow (shorts) | 3.0% annual, accrued daily | flat rate — no clean retail borrow feed exists (deferred); the short side only |
| Backtest capital | $100,000 | sets participation, hence slippage |

A non-positive ADV (illiquid / missing) is treated as full participation — the
maximum slippage penalty — which fails closed.

## 5. Realistic-friction stress test (spec step 9)

The whole walk-forward is re-run with **+50% on every cost component and +1 day
of execution lag**. The stressed run must keep a **positive** aggregate Sharpe.
An edge that only exists at modeled costs and instantaneous execution is treated
as fragile and does not promote.

## 6. Trade-count gate (spec step 6)

Fewer than **150 trades** across the full walk-forward → `kill`, regardless of
headline metrics. No exceptions, and **the gate stays universal** — it is the
one Framework #1 construct this card deliberately does *not* make
archetype-conditional.

That is a change of reasoning, not of code. The gate was previously logged as
"too strict for structural strategies, pending a Mike-owned floor per
archetype." The first three real evaluations (2026-07-27/28) argued the
opposite. Fold 5 of the seed strategy produced an annualized Sharpe of **1.712
from a single trade**, and three parameter pairs on the same rule, ticker and
window produced trade counts of 20 / 43 / 145 against Sharpes of 0.415 /
**−0.157** / 0.745 — monotonic in count, sign-changing in Sharpe. At these
counts the statistic is not a small measurement, it is noise, and no threshold
makes a Sharpe-based walk-forward able to judge one to five decisions per fold.

So a lower floor for structural strategies would not measure them more
leniently; it would report noise with more confidence. The open question is
therefore **not** "what floor for `infra-graph-play`" but "what protocol
evaluates a multi-year thesis at all" — an event-study or realized-vs-thesis
comparison rather than a Sharpe walk-forward. That is a separate card, and
until it exists `infra-graph-play` strategies will keep dying here, correctly.

For `technical-catalyst` — the archetype the vision assigns most of the firm's
trading, "fast loops, many trades" — 150 is the floor it was calibrated for and
needs no exemption.

## 6b. Benchmark-relative evaluation — skill, not exposure

**Defect found and closed 2026-07-28.** The account below is kept because the
measurement is the justification for the gate, and a gate whose reason is
forgotten is a gate someone later removes as redundant.

The promote gate is an **absolute** Sharpe floor. Nothing in the protocol
compares a strategy to the alternative of simply being invested. Measured
through this engine on synthetic random-walk data, with a naive equal-weight
buy-and-hold portfolio that has **no timing rule at all**:

| Drift | 1 name | 10 names | 50 names |
|---|---|---|---|
| **zero** (pure noise) | 0.450 | 0.330 | 0.358 |
| **~7.5%/yr** (roughly US equities) | **1.026** | **1.098** | **1.158** |

With realistic drift, doing nothing clears the 1.0 floor at every breadth. At
zero drift the same portfolios score 0.33-0.45, which identifies the term doing
the work: **market drift, not skill, and not diversification.**

Corroborating, from the same runs: on 50 independent series a cross-sectional
5/20 timing rule scored 2.28 against buy-and-hold's 3.22. The rule **destroyed
value** relative to holding the basket — and would have promoted, because 2.28
clears 1.0.

**Why breadth makes this urgent rather than merely true.** A single-name timing
rule is at least measured against being flat: it is out of the market much of
the time, so its Sharpe is not simply the instrument's. A cross-sectional rule is
reliably invested in *something*, so it inherits close to full market exposure
and the floor stops discriminating at all. Building breadth without a benchmark
would produce a machine that promotes market beta and files it as edge.

**The fix.** Benchmark-relative evaluation: measure active return against
equal-weight buy-and-hold of the strategy's own declared universe over the same
window, and gate on that (an information ratio) rather than on absolute Sharpe.
"Beat being invested" is the question the firm actually wants answered. The
engine already supports this — computing it needs one extra `walk_forward` pass
over the same panel with a constant-weight rule.

### What shipped

Every evaluation now runs a **second backtest over the identical panel, periods
and cost model**, using equal-weight buy-and-hold (`benchmark.py`). The
per-period difference is the strategy's active return; its risk-adjusted form is
the **information ratio** (active return over tracking error), reported in
`research.evaluations.active_metrics` and on every evaluation card.

Two verdict outcomes come from it, and the asymmetry is deliberate:

| Condition | Verdict | Reason |
|---|---|---|
| information ratio ≤ 0 | **kill** | `no-active-edge` |
| 0 < information ratio < floor | hold | `below-information-ratio-floor` |

Losing to buy-and-hold **kills**. A strategy that traded all year to finish
behind the basket it trades has been measured and found actively harmful, and
more data cannot redeem the decisions it already made. Beating the benchmark
insufficiently only **holds** — that is a power problem, not a verdict.

The benchmark is **fully invested and pays costs**. It does not inherit the
strategy's gross exposure: a strategy that sits in cash is making a decision,
and matching its exposure would hide the choice being evaluated. It generalises
at N=1, where it becomes buy-and-hold that one name and the question is "did
the timing beat simply owning it?"

**`DEFAULT_INFORMATION_RATIO_FLOOR = 0.5` is decision-carrying and Mike's.** An
information ratio of 0.5 sustained out of sample is a genuinely good active
manager; 1.0 is exceptional and rare. Setting it equal to the Sharpe floor would
mean the firm essentially never promotes — defensible, but it should be chosen
rather than inherited.

Cross-sectional rules, shipped refused in PR #110, are **enabled** by this:
`DEFERRED_RULES` is now empty. It is kept as a mechanism rather than deleted,
because "written and tested but not yet safe to evaluate" will recur.

### What it caught immediately

The pipeline's own promote fixture. Its synthetic prices rose during the long
phase and were **flat** otherwise, so buy-and-hold captured every rise and gave
nothing back — the timing rule added only its own costs and was correctly killed
as `no-active-edge`. It had only ever "promoted" because absolute Sharpe cannot
tell being invested apart from being skilful. The fixture now falls during the
off phase, so avoiding it is worth something.

**What this says about the verdicts already on record.** The three killed
strategies all died on trade count, before Sharpe was ever the binding
constraint, so none of their verdicts changes. But their reported Sharpes were
never measures of skill, and should not be cited as if they were.

## 7. Verdict mapping (spec step 6, 10)

A pure function of the metrics — no human tuning — applied in strict priority:

1. anchor required and not live → `kill` (`anchor-not-live`)
2. trades < 150 → `kill` (`insufficient-trades`)
3. aggregate Sharpe ≤ 0 → `kill` (`no-edge`)
4. stressed Sharpe ≤ 0 → `kill` (`fails-friction-stress`)
5. aggregate Sharpe < the promote floor → `hold-for-data` (`below-sharpe-floor`)
6. otherwise → `promote` (`promote-criteria-met`)

**Promote** therefore requires all of: a fresh anchor *where the archetype
requires one*, ≥150 trades, positive Sharpe surviving the friction stress, and
Sharpe ≥ the promote floor.

On `promote` the strategy transitions `hypothesis → paper` through the strategy
registry (the append-only transition row records the reasoning). On `kill`,
`hypothesis → killed`. On `hold-for-data`, no transition. Every run writes an
append-only row to `research.evaluations` (full metrics blob, config, protocol
version), publishes `research.strategy.verdict` (and `research.strategy.killed`
on a kill), and writes a Markdown evaluation card under
`docs/strategies/evaluations/<strategy_id>/<ts>.md`.

## 8. Deferred (later cards)

Explicitly **not** in this protocol version:

- Probability of Backtest Overfitting (PBO), Deflated Sharpe Ratio (DSR),
  Minimum Backtest Length (MinBTL), and Combinatorial Purged Cross-Validation
  (CPCV) with embargo.
- Regime-stratified reporting and regime-conditional sizing modifiers.
- In-sample parameter grid fitting and the refit pipeline.
- The kill-review pipeline and the `research.strategy.halt` /
  `research.strategy.demoted` events; the `research.bottleneck.*` and
  `research.infra.graph.node-failed` thesis-broken triggers.
- The overnight queue runner, Sunday re-evaluation, and decay detection.
- The strategy-authoring DSL / plugin registry (the record → signal-code
  binding; see §9).
- A real borrow-cost data feed (a flat configurable rate is used).

## 9. Decisions pending Mike

Two choices in this card are Mike-owned and shipped as documented defaults, not
silent decisions:

- **The `StrategySignal` seam (architectural).** The engine is driven by one
  interface: given a no-peek window of daily bars, a strategy emits a target
  weight per ticker; the engine turns weight changes into trades, costs, and
  PnL. This is the interface every future strategy implements. Until the
  strategy-authoring system exists, an `infra-graph-play` record is evaluated by
  instantiating a **reference** moving-average trend rule from its params (a
  labelled placeholder, not a proposed edge); the record → code binding is an
  injectable factory that the authoring card will replace. **Merging this card
  accepts the seam.**
- **The Sharpe promote floor (calibration).** Default **1.0** annualized
  out-of-sample Sharpe. Chosen conservatively — a net annualized Sharpe of 1.0
  after realistic costs is a modest but economically meaningful bar, and it is a
  round number that is easy to move. The spec lists Sharpe/DSR thresholds per
  stage as "Blocks: first promotion. Owner: Mike." This default is
  calibration-pending in exactly the way the Regime Classifier's v0.1 sizing
  bands are — it holds the line until Mike rules, and it is overridable per run
  via `--sharpe-floor`.

---

*End of protocol v0.1.*
