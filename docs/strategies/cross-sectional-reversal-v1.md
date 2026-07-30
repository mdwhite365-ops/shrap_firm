# Cross-sectional reversal (5/1, top 10)

**Archetype:** `technical-catalyst` (Framework #3) — no world-changer anchor
**Rule:** `cross-sectional-reversal`
**Universe:** the 50-name launch list, identical to the momentum seeds
**Status:** seeded 2026-07-30, unevaluated
**Prior:** Lehmann (1990); Lo & MacKinlay (1990) — short-term contrarian profits

Two seeds, both lineage roots:

| Key | Strategy ID | Legs |
|---|---|---|
| `xs-reversal-5-1-10-longshort` | `01KYRECMH8WZ2WZYB4ZE217E37` | long/short — the documented construction |
| `xs-reversal-5-1-10-longonly` | `01KYRECMH8WZ2WZYB4ZE217E38` | long only — a recorded deviation |

## Why this strategy exists

The firm's momentum strategy was evaluated on 2026-07-29 and produced this fold
table:

| Fold | Return | IR | |
|---|---|---|---|
| 2021 | +70.41% | **+1.090** | beat |
| 2022 | −33.76% | **−0.004** | level |
| 2023 | +9.05% | **−0.457** | lost |
| 2024 | +68.84% | **+0.692** | beat |
| 2025 | +69.95% | **+1.073** | beat |
| 2026 | +6.58% | **−0.241** | lost |

The obvious reading — "it lost 33% in 2022, it needs a bear-market hedge" — is
wrong, and the information ratio is what shows it. Relative to simply owning the
same 50 names, momentum was **dead level in the crash**: IR −0.004. Everyone lost
money in 2022; momentum lost no more than the basket did. The strategy's own kill
criterion #3, which anticipates momentum crashes, never fired.

It lost in **2023 and 2026** — modestly-positive, low-dispersion years, where it
turned over 455 and 330 trades to lag a basket that sat still. That is the gap:
not downside, but churn in quiet markets.

Short-horizon reversal is the documented effect that earns in exactly those
conditions, and it is the effect the momentum rule already steps around — the
21-day skip exists *because* reversal runs opposite to momentum over the recent
window. This rule trades what that skip discards.

## Construction

```
formation return = close[t-1] / close[t-6] - 1      # 5 sessions, skipping 1
rank ascending
long  the bottom 10 (the fallers)
short the top 10 (the risers)                        # long/short seed only
dollar-neutral, half the gross per side
```

Deliberately near-identical to the momentum rule: same formation-return function,
same parameter names, same `_long_short_weights` helper, same universe. **The only
difference is the sort direction and the horizon.** Anything else would confound a
comparison between the two with an implementation difference.

`skip=1` is not a tuned value. The most recent close is where bid-ask bounce
lives, and buying yesterday's worst close is the classic way to harvest a spread
that does not exist at fill time.

`REVERSAL_PARAM_BOUNDS` caps `lookback` at 21 sessions where `MOMENTUM_PARAM_BOUNDS`
starts at 21. The two ranges are disjoint on purpose: a spec that could express
either has stopped saying which effect it trades, and a parameter sweep could
otherwise turn one strategy silently into the other.

## The falsifiable claim

Written before the run. This strategy exists to cover the two folds momentum
lost, so:

**It must beat the benchmark in 2023 and 2026.** An aggregate that looks
respectable while losing those same two folds has failed the hypothesis whatever
its headline number says. Winning where momentum already wins means the firm has
built a second copy of one bet while believing it diversified.

The second criterion is the sharper one: **its fold information ratios must not
correlate positively with momentum's.** That comparison is not something the
Evaluator currently computes — it scores strategies in isolation — so it has to be
done by hand until a complementarity measure exists.

Full criteria are on the record in `reversal_kill_criteria()`.

## Expected difficulties, stated in advance

**Costs are the likeliest way this dies.** A 5-day formation rotated across 50
names trades far harder than the 126/21 rule, and reversal profits are small per
name. The honest test is net of the cost model, never gross.

**It may be a liquidity premium the firm cannot harvest.** Reversal profits
concentrate in the names that are hardest to trade. The evaluator's ADV filter may
be admitting fills that would not exist at size.

**The trade-count gate will pass easily and mean less than usual.** Breadth times
a 5-day horizon produces a very large nominal trade count, and daily equity
returns are heavily cross-correlated — so the effective sample is far smaller than
the number implies. Read the count as an upper bound on statistical power, as
`cross_sectional.py` already warns.

## The long-only seed is a deviation, recorded as one

Dropping a leg is exactly the error the momentum rule made: a one-sided book turned
a factor bet into a trend amplifier whose fold IR correlated **+0.97** with fold
return. The same distortion should be expected here, in the opposite direction.

It is seeded anyway because the Strategy Runner cannot open a short — `_invested`
treats a negative weight as flat — so the long-only version is the only one the
firm can put on an account today. Its thesis says so in those words.

**Do not assign the long/short seed an account.** It would trade only its long leg,
silently, at half the intended book.

Whether the short leg pays is answerable by comparing the two evaluations. That
answer decides whether making the Runner short-capable is worth building, which is
the cheap ordering: measure first, build second.

## Running it

```bash
cd /mnt/Archive/shrap/shrap_firm/infra
sudo docker compose --profile tools build strategy-evaluator

# Order does not matter here — both are roots, neither names the other as parent.
sudo docker compose --profile tools run --rm strategy-evaluator \
  shrap-strategy-seed load-reversal xs-reversal-5-1-10-longshort
sudo docker compose --profile tools run --rm strategy-evaluator \
  shrap-strategy-seed load-reversal xs-reversal-5-1-10-longonly

# Dry run first, always.
sudo docker compose --profile tools run --rm strategy-evaluator \
  shrap-strategy-evaluate --strategy-id 01KYRECMH8WZ2WZYB4ZE217E37 --dry-run
```

Every one of the 50 tickers needs daily bars in `market_data.daily_bars` and tier
`active` in `research.universe_tiers`, or the evaluation is **refused** rather than
killed, naming the first offending ticker.

Read the **fold table**, not the aggregate. The claim this card makes lives in two
specific rows.
