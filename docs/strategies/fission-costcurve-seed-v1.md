# Fission cost-curve — pipeline seed v1

**Strategy ID:** `01KYGTRTTQA9X2B2E16N4SBPTG`
**Document version:** 0.1
**Date:** 2026-07-26
**Owner:** Mike White
**Status:** Seeded at `hypothesis` — expected to be killed
**Source:** `mike-seed`
**Code constant:** `src/shrap/research/strategy_seed/first_strategy.py`

## What this is

The firm's first seeded strategy. It exists so the research funnel can run end
to end: the Strategy Evaluator (PR #78) can only evaluate a strategy that is
already at status `hypothesis`, and until this card there was no way to create
one — the Hypothesis Generator is deferred and the registry had no CLI. This
seed, loaded by `shrap-strategy-seed load-first`, is the Mike-seed path that
closes that gap.

It is a **pipeline exerciser, not an edge.** The rule is a plain moving-average
crossover (fast 20 / slow 100) on the daily closes of a single liquid ETF, long
or flat. That is the Evaluator's reference trend rule — the same boring rule the
Evaluator instantiates for any `infra-graph-play` record until the
strategy-authoring layer exists. Nothing here is tuned, and nothing here is
claimed to work.

## The fission anchor, and the XLE placeholder caveat

The strategy is anchored on the promoted mass-manufactured-fission world-changer
(`01KXVVPXDMB4HS1QNRPQWRP1RX`, "Mass-manufactured fission cost-curve crossing",
promoted 2026-07-18). The Evaluator checks that this anchor is still `promoted`
in `research.world_changers` before it will run a backtest; that wiring is the
point of anchoring the seed here.

**XLE is a placeholder, not the thesis.** The energy-sector ETF is used because
it is a locked Tier 3 name with deep, clean history — exactly what a walk-forward
needs. It is not a real expression of "fission drives energy $/kWh down a
learning curve." A genuine fission expression would reach for reactor
manufacturers, SMR supply chains, utilities signing unsubsidized nuclear PPAs,
and the enrichment/fuel bottleneck — names that mostly are not in the launch
universe yet. Building that expression is research work. This card does not
pretend to do it.

## Expectation: this will be killed

A daily MA crossover on a single trending ETF flips position a handful of times
over a multi-year window. The Evaluator's promote path requires at least 150
trades across the walk-forward before it will even consider edge, so the
overwhelmingly likely verdict is **kill — insufficient trades.** That is the
system working as designed: it kills far more than it promotes, because
promoting noise costs real money and killing real edge only costs the time to
find it again. A kill here is a successful end-to-end test of the funnel, not a
failure.

Per the Evaluator's protocol, passing its tests would only mean "we have failed
to disprove edge under our test protocol," never that edge is real. This seed is
not expected to get that far.

## Kill criteria

Honest, observable falsifiers — the fission-thesis breakers (any of which drops
the world-changer and so kills the anchor) plus plain performance gates:

- world-changer anchor no longer `promoted` in `research.world_changers` (the
  mass-manufactured fission thesis is broken);
- no unsubsidized hyperscaler/industrial nuclear PPA by the world-changer's
  falsifier horizon (2027-12);
- nth-of-a-kind $/kW flattens across two vendor cohorts (the learning curve
  stalls);
- fewer than 150 trades over the walk-forward window — too few to evaluate (this
  daily MA rule is expected to fail this gate);
- out-of-sample Sharpe at or below the promote floor, or edge that dies under the
  realistic-friction stress test.

## Spec

| Field | Value |
|---|---|
| Archetype | `infra-graph-play` (the only archetype the Evaluator's first card runs) |
| Tickers | `XLE` (long/flat) |
| Params | `fast=20`, `slow=100`, `target_weight=1.0`, `long_only=true` |
| Param bounds | `fast [2, 100]`, `slow [5, 400]`, `target_weight [0.0, 1.0]` |
| Regime sizing | neutral (1.0 across all four regimes — no regime opinion) |
| Regime gate | none (regime is a sizing modifier, never an entry/exit gate) |

The params are exactly what the Evaluator's reference trend rule consumes; the
bounds are copied from that rule's own declared bounds so they cannot drift, and
every numeric value lies inside its `[lo, hi]`.

## How to run it

```
shrap-strategy-seed load-first     # insert the seed (idempotent; re-run is a no-op)
shrap-strategy-seed list           # find the strategy_id
shrap-strategy-evaluate --strategy-id 01KYGTRTTQA9X2B2E16N4SBPTG
```

`load-first` is idempotent: it skips if a row with the seed's `spec_hash`
already exists, so a second run creates no duplicate.

## What this card is not

It is not a validated strategy, not a real fission expression, and not a claim of
edge. A strategy with genuine edge — and a real fission expression built on the
right instruments — is downstream research work. This card only makes the
funnel → evaluation path runnable, and gives the firm an honest first thing to
kill.
