# SPY short-horizon trend (5/20) — the firm's first Framework #3 seed

**Strategy ID:** `01KYNCX02WTPS9ZJ52QX8GD4PJ`
**Archetype:** `technical-catalyst` (Research Thesis Framework #3, ADR-0013)
**Anchor:** none — and that is the point
**Code:** `src/shrap/research/strategy_seed/technical_strategies.py`
**Status:** hypothesis

## What this is

A 5/20 daily moving-average crossover on SPY, long-only, full target weight. It
is the first strategy in the firm's registry that **is what it says it is.**

Every prior strategy carried the mass-manufactured-fission `world_changer_id`,
including two that were moving-average crossovers on an energy ETF with no
relationship to fission whatsoever. They carried it because the Evaluator killed
anchor-less strategies before the backtest ran, so a protocol probe had to claim
a thesis in order to be measured at all. `probe_strategies.py` says so in its own
docstring. ADR-0013's archetype-conditional gates (PR #102) removed the
requirement; this seed is the first use of that fix.

The thesis is entirely about price behaviour: short-horizon trend persistence in
a broad, highly liquid index ETF. There is no claim about the physical world, no
anchor to go stale, and no falsifier about a technology thesis — because there is
no technology thesis.

## Why SPY

ADR-0013 notes that Framework #3 "will eventually pressure" the launch universe,
because microstructure and short-horizon strategies want liquid, high-turnover
names while the 50-name list was chosen for structural expressiveness. SPY is the
most liquid name on the list and one of only six with a written per-ticker profile
(`docs/universe/spy.md`). Starting the fast layer on the most liquid available
instrument keeps the friction model as honest as it can be on daily bars.

## Expectation, written before the run

**This will very likely be killed on `insufficient-trades`, and that outcome is a
data limitation rather than a defect in the rule.**

The arithmetic is not close:

```
5-year window x ~252 sessions  =  ~1,260 daily bars
150-trade gate                 =>  a position flip every ~8 bars
```

A daily-bar trend rule that flips every eight bars is not following a trend, it is
trading noise. The probes already showed this empirically rather than only
arithmetically — same rule, same instrument, same window:

| fast/slow | Trades | Base Sharpe |
|---|---|---|
| 20/100 | 20 | 0.415 |
| 10/50 | 43 | **−0.157** |
| 3/10 | 145 | 0.745 |

Trade count moves monotonically with the parameters. Sharpe does not, and changes
sign. Picking windows to scrape past 150 would produce a promotion built on
exactly the noise those three runs exposed.

So the parameters here were chosen to be a defensible short-horizon trend filter
and for no other reason. **The conclusion this seed is designed to make
unavoidable is that the fast layer needs intraday data, not different
parameters** — see Phase 2, Track B in `docs/roadmap/implementation-timeline.md`.

## Why run it anyway

1. **It is the first strategy whose archetype matches its content.** The registry
   stops containing a claim nobody believes.
2. **First live exercise of the archetype-conditional gates.** The anchor-less
   path has only ever run in tests; this is the first time production data goes
   through it.
3. **It produces a fresh `research.strategy.verdict`** — the only way to observe
   two fixes that are unit-tested and have never been seen live: the Strategy
   Librarian's INFO convergence path (PR #100) and the Evaluator trigger (PR
   #103). Neither can be verified against old events, because `start_id` applies
   only at consumer-group creation and the existing verdicts are already acked.

## Kill criteria

Note what is absent: no world-changer criterion. The probes inherited one with
their borrowed anchor, and it was already satisfied on the day they were written.

1. Short-horizon trend persistence in SPY does not survive realistic costs — the
   effect is small per trade and dies to friction before it dies to being wrong.
2. Fewer than 150 trades over the walk-forward window — too few to evaluate.
3. Out-of-sample Sharpe at or below zero — no edge to measure.
4. Edge does not survive the realistic-friction stress test.
5. Out-of-sample Sharpe below the promote floor.

## How to run it

Requires SPY daily bars in `market_data.daily_bars` and SPY at tier `active` in
`research.universe_tiers`. Both prerequisites are checked before the backtest and
produce a *refusal*, not a kill.

```bash
cd /mnt/Archive/shrap/shrap_firm/infra

# Build first — `docker compose run` never rebuilds, and this seed is new code.
sudo docker compose --profile tools build strategy-evaluator

sudo docker compose --profile tools run --rm strategy-evaluator \
  shrap-strategy-seed load-technical spy-trend-5-20

# Backfill SPY if it is not already present. Dry run first.
sudo docker compose --profile tools run --rm market-data \
  shrap-market-data-backfill --tickers SPY --since 2020-01-01 --dry-run
sudo docker compose --profile tools run --rm market-data \
  shrap-market-data-backfill --tickers SPY --since 2020-01-01

# Dry run reads `anchor=not-required` — that is the gate working, not a failure.
sudo docker compose --profile tools run --rm strategy-evaluator \
  shrap-strategy-evaluate --strategy-id 01KYNCX02WTPS9ZJ52QX8GD4PJ --dry-run
```

Do **not** drop `--dry-run` until the summary shows `anchor=not-required` and
`engine_ran=True`. Alternatively, leave it: the Evaluator trigger sweeps
hypothesis-stage strategies every 15 minutes and will evaluate it unattended,
which is the more interesting test of the two.

## What this card is not

It is not a candidate for edge, and nothing here should be read as expecting one.
It is not a demonstration that the fast layer works — it is the cleanest available
demonstration of why the fast layer cannot work on daily bars. It does not change
any gate: `DEFAULT_MIN_TRADES` stays 150 for both archetypes, per
`docs/research/eval-protocol.md` §6.
