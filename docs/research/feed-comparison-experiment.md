# IEX vs SIP — measured, on one strategy

**Version:** 1.0 (result)
**Date:** 2026-09-18
**Owner:** agents (the measurement); Mike (the ruling)
**Serves:** KI-036, ADR-0003.
**Status:** Complete. **Negative result** — the feed does not reliably change
this strategy's edge.

## The question

`01KYRG32PAW0V0D8RVRBHAJ9HA` — *High-volume return premium (50d baseline, top
10)* — is at `paper` and trades on `cross-sectional-factor` with
`factor: volume-shock`, which scores `volumes[-1] / mean(volumes[-51:-1])`.

The firm stores **IEX** bars. IEX is one venue carrying a median **3.7%** of a
name's consolidated volume. That ratio is self-normalising, so a *constant* IEX
share cancels out entirely. The share is not constant: its within-name
coefficient of variation is **0.246**, and the strategy's top-10 selection
agrees with consolidated volume on only **68.2%** of names, with **1 of 88**
sessions producing an identical basket.

So the strategy demonstrably measures something different from what its thesis
claims. The question this experiment answers is the one that actually matters:
**does that difference cost it anything?**

## Method

Same strategy record, same `walk_forward`, same `_default_strategy_factory`,
same 6 folds, same costs. **The only variable is `source`.** Nothing was
written — no evaluation row, no transition, no event; the strategy stayed at
`paper` throughout.

SIP daily bars were backfilled for all 50 launch names, 2018-11-01 to
2026-09-17: **91,051 rows**, stored alongside the 74,247 IEX rows rather than
over them (this is what #245 exists for).

## The first run was confounded, and it looked like a win

| | IEX | SIP |
|---|---|---|
| OOS start | 2020-10-07 | **2019-01-18** |
| OOS periods | 1,492 | **1,925** |
| information ratio | +0.2332 | **+0.5372** |

SIP clears the 0.50 promote floor. It should not have been reported.

**IEX has almost no history before mid-2020** — zero bars in 2019 and under
half of 2020 — so the SIP panel was 433 sessions longer and included a regime
(2019 plus the COVID crash) the IEX panel barely touches. That comparison
confounds feed with window, and the number it produced is exactly the kind
KI-036 warns about: a favourable measurement error that clears a gate.

## The controlled run

Both feeds pinned to 2021-01-04 .. 2026-09-17, where IEX coverage is complete.
**Identical panels: 1,433 sessions, same OOS window, same 1,380 periods.**

| | IEX | SIP |
|---|---|---|
| trades | 15,136 | 13,586 |
| sharpe | +0.5188 | +0.6775 |
| benchmark sharpe | +0.7727 | +0.7683 |
| active return | **−22.20%** | **+15.31%** |
| **information ratio** | **−0.1315** | **+0.2275** |
| stress sharpe | +0.3535 | +0.4284 |

The near-identical benchmark Sharpe is the control that makes this readable:
**closes agree between the feeds**, so the entire difference is volume — which
is what `volume-shock` ranks on.

On the aggregate the sign flips. That is a +0.359 IR swing from the feed alone,
and it is tempting.

## It does not survive the folds

| fold | IEX | SIP | delta |
|---|---|---|---|
| 2021-03-19 .. 2022-02-14 | +0.2445 | −0.2988 | −0.5432 |
| 2022-02-15 .. 2023-01-13 | −0.6554 | +0.0214 | +0.6768 |
| 2023-01-17 .. 2023-12-13 | +0.0604 | +0.4237 | +0.3633 |
| 2023-12-14 .. 2024-11-12 | −0.5871 | +0.8959 | **+1.4830** |
| 2024-11-13 .. 2025-10-15 | +0.2003 | −0.2317 | −0.4320 |
| 2025-10-16 .. 2026-09-16 | +0.3990 | +0.3396 | −0.0594 |

```
mean delta  +0.2481    sd 0.7623    se 0.3112    t = 0.80 on 5 df   (p ~ 0.46)
SIP higher in 3 of 6 folds
excluding the single +1.4830 fold:  mean delta  +0.0011
```

**SIP wins a coin flip of folds, and the entire aggregate advantage is one
year.** Drop 2023-12 .. 2024-11 and the mean difference is one tenth of one
percent of an IR — zero to three decimal places.

## Conclusion

**The feed changes what the strategy selects. It does not reliably change what
the strategy earns.**

Switching this strategy to SIP on the strength of the +0.359 aggregate would be
promoting on a single fold — the same error as reading the confounded first run
as a pass. Neither feed clears the 0.50 floor, and neither is distinguishable
from the other at this sample size.

This is consistent with KI-036 rather than a new finding against it: this
panel cannot separate differences of this size, and the honest response to
*"which feed is better"* is **the measurement cannot tell, and the difference
is small enough that it probably does not matter.**

## What is worth keeping anyway

1. **SIP is the correct tape** and now costs nothing to hold. The strategy's
   stated thesis is about a name's volume against its own history; consolidated
   volume is that quantity, and IEX is a 3.7% sample of it. Preferring SIP is
   defensible on grounds of *measuring the thing you said you were measuring*,
   independent of whether it pays.
2. **SIP carries ~1.7 years more history** — 2019 and most of 2020, including
   the COVID crash, a regime absent from every backtest the firm has ever run.
   That is a real gain for future evaluations and has nothing to do with this
   result.
3. **The comparison is now cheap to repeat.** `--bar-source` plus a stored
   second feed makes "does this strategy depend on the tape" a question any
   future card can ask in one run.

## What was NOT done

- No strategy was switched, promoted, killed or re-staged.
- The intraday trigger still runs on IEX. SIP refuses the current session on
  this plan (measured: `end=2026-09-17` returns 200, `end=2026-09-18` returns
  403), and the intraday sweep runs to the present.
- The paired standard error above treats folds as independent draws, which is
  the same assumption the fold-consistency metric already makes. It is a weaker
  claim than a block bootstrap would give and is not offered as a precise
  p-value — only as evidence that 3-of-6 and one dominant fold is not a result.
