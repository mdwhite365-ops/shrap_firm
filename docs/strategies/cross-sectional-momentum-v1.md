# Cross-sectional momentum (126/21, top 10) — the first strategy with a real prior

**Strategy ID:** `01KYNH9VKXVQXJ48T4MF306PHE`
**Archetype:** `technical-catalyst` (Framework #3) · **Anchor:** none
**Rule:** `cross-sectional-momentum` · **Universe:** all 50 Tier-3 launch names
**Code:** `src/shrap/research/strategy_seed/technical_strategies.py`

## What it is

Rank the 50 launch names on their trailing six-month return, excluding the most
recent month, and hold the top ten equal-weighted. Rebalance as the ranking
changes.

## Why this one, and not another crossover

Every strategy the firm has evaluated so far was a moving-average crossover
seeded because *something* had to run. This is the first with a documented
out-of-sample prior behind it: cross-sectional momentum is one of the most
replicated effects in the equity literature.

That raises the odds of surviving evaluation. It does not guarantee it, and the
reason is worth stating plainly — **it is also one of the most crowded trades in
the market.** An effect published for decades and traded by everyone is exactly
the kind that decays. Prior evidence moves the prior; it does not settle the
question, which is what the Evaluator is for.

**The skip is not a tuning knob.** Short-horizon reversal runs opposite to
momentum, so including the last month in the formation window mixes two opposing
signals. 126/21/10 is the textbook construction, not a search result. The
parameters were not tuned against this data, and tuning them would make the
out-of-sample claim meaningless.

## Why it can clear the trade-count gate when nothing else could

The engine counts a trade **per ticker per weight change**, so breadth supplies
sample size. Measured in PR #110 on the same rule and window:

| Tickers | Trades over 5 years |
|---|---|
| 1 | 89 — fails the 150 gate |
| 50 | 28,139 |

A single-name daily rule cannot clear the gate without flipping every ~8 bars,
which is noise-trading rather than trend-following. Fifty names clear it by
having fifty times the decisions.

**The honest caveat:** trades across 50 US equities are not 50 independent
observations. Daily equity returns are heavily cross-correlated — in a drawdown
every name moves together — so the effective sample is materially smaller than
the nominal count. Read the trade count as an upper bound on statistical power,
never as the power itself.

## What it has to beat

Not zero. **Equal-weight buy-and-hold of the same 50 names.**

Absolute Sharpe cannot separate skill from market exposure: naive buy-and-hold
with no timing rule scored 1.03–1.16 through this engine on drifting data,
clearing the old 1.0 promote floor purely by being invested. So the promote gate
is now the **information ratio** — active return over tracking error against that
benchmark (`docs/research/eval-protocol.md` §6b).

For this strategy the question is therefore exact and fair: *did rotating into
the top decile beat simply owning all fifty?*

## Kill criteria

Note what is absent: no world-changer criterion, because there is no
world-changer.

1. **Costs.** A monthly top-decile rotation over 50 names has high turnover, and
   momentum is small enough per name that friction is the likeliest way it dies.
2. **It does not beat equal-weight buy-and-hold** — an information ratio at or
   below zero means the rotation destroyed value against owning the names.
3. **Momentum crashes.** The effect is known to invert sharply after drawdowns,
   so a single fold with a large negative return is evidence about the strategy
   rather than noise to be averaged away.
4. Fewer than 150 trades over the walk-forward window.
5. Out-of-sample Sharpe at or below zero, or below the promote floor.
6. Edge does not survive the realistic-friction stress test.

## Running it

**All 50 tickers need daily bars and Tier-3 `active` status.** A missing one
produces a *refusal* naming the offending ticker — not a kill, so nothing is
terminated by a data gap.

```bash
cd /mnt/Archive/shrap/shrap_firm/infra

# Build first — `docker compose run` never rebuilds, and this is new code.
sudo docker compose --profile tools build strategy-evaluator

# 1. Populate research.universe_tiers (idempotent; has never been run).
sudo docker compose exec universe-curator shrap-universe-promote load-launch-list

# 2. Backfill all 50 names. --launch-list beats pasting tickers: it cannot
#    drift out of step with the universe the Evaluator checks against.
sudo docker compose --profile tools run --rm market-data \
  shrap-market-data-backfill --launch-list --since 2018-01-01 --dry-run
sudo docker compose --profile tools run --rm market-data \
  shrap-market-data-backfill --launch-list --since 2018-01-01

# 3. Seed it.
sudo docker compose --profile tools run --rm strategy-evaluator \
  shrap-strategy-seed load-momentum xs-momentum-126-21-10

# 4. Evaluate. Dry run first, always.
sudo docker compose --profile tools run --rm strategy-evaluator \
  shrap-strategy-evaluate --strategy-id 01KYNH9VKXVQXJ48T4MF306PHE --dry-run
```

Or seed it and leave it: the Evaluator trigger sweeps hypothesis-stage
strategies every 15 minutes and will evaluate it unattended, holding any
promotion for review (ADR-0015).

`--since 2018-01-01` rather than five years back: the momentum rule needs 127
bars of warmup before its first decision, and a longer window buys folds that
the warmup would otherwise eat.

## How to read the verdict

| Verdict | What it means |
|---|---|
| `promote` | Beat buy-and-hold by an information ratio ≥ 0.5, cleared every other gate. **The first defensible promotion the firm has produced.** |
| `hold-for-data` / `below-information-ratio-floor` | Beat the benchmark, but not by enough to distinguish skill from luck. |
| `kill` / `no-active-edge` | Lost to simply owning the names. The most likely outcome, and a real result. |
| `kill` / `fails-friction-stress` | The effect exists but turnover eats it — kill criterion 1, confirmed. |

A kill here is not a failed card. It is the first time the firm will have asked a
strategy the right question with enough data to answer it.
