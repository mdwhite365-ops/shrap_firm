# Stagflation

**Regime ID:** stagflation
**Document version:** 0.1 (draft)
**Date:** 2026-05-29
**Owner:** Mike White
**Status:** Draft

## Identification

Stagflation is an environment combining persistent above-trend inflation with weak or negative real growth, an environment in which the central bank's two mandates point in opposite directions. Equities tend to derate as discount rates rise and earnings growth slows simultaneously. Commodities, hard assets, and select pricing-power names typically outperform; long-duration assets and rate-sensitive growth names underperform. Correlations break in ways that punish naive 60/40 allocation and reward strategies tuned to real-asset and pricing-power exposure.

## Statistical Markers

| Marker | Expected range | Notes |
|---|---|---|
| Realized vol (SPX, 20d) | 15-25% | Elevated baseline; episodic spikes |
| Implied vol (VIX) | 20-30 with periodic spikes to 35+ | Persistently above long-run norm |
| VIX term structure | Frequently flat or backwardated during stress | Term structure unstable |
| Trend strength (SPX ADX 14) | Variable; often choppy with weak underlying trend | Sustained trends are harder to come by |
| Breadth (% above 200dma) | 30-50%, often deteriorating | Few names carry the tape; many lag |
| Sector dispersion | High; energy/materials/staples diverge from tech/discretionary | Sector rotation is the dominant feature |
| Credit spreads | IG 150-300 bps, HY 500-900 bps; widening trend | Risk premia priced explicitly |
| Dollar trend | Often strong (capital seeks yield in USD) but episodic | Variable; not always diagnostic |
| Rate vol (MOVE) | Elevated, 130-180+ | Policy uncertainty embedded |

## Historical Examples

- **1973-1975 (oil shock, recession overlap):** OPEC embargo (1973-10), wage-price spiral, Nixon-era controls and their unwind, deep recession concurrent with double-digit inflation. SPX peak-to-trough drawdown ~48% from January 1973 to October 1974.
- **1978-1982 (second oil shock, Volcker disinflation):** Iranian Revolution (1979) and second oil shock, double-digit CPI prints, Volcker's 1979-1980 rate-hike campaign that drove Fed Funds above 19% and produced the 1980 and 1981-1982 recessions. Equities oscillated in a wide range until the August 1982 disinflation rally.
- **(Adjacent) 2022 (partial analog):** CPI peaked at 9.1% in 2022-06, SPX drew down ~25%, energy outperformed dramatically. Did not become a full stagflation regime because growth held up; useful partial reference.

## Macro Backdrop

Stagflation typically follows a supply shock or a long period of accommodative policy that fed an inflation impulse the central bank then must reverse. The qualitative features:

- Inflation prints persistently above the central bank's tolerance; 3-year breakeven inflation elevated and sticky.
- Wage growth strong but lagging price growth; real wages compressed.
- Earnings growth weakens as input costs rise faster than firms can pass through.
- Policy stance must be tight, but tightening into weak growth is politically and economically painful; policy mistakes in both directions are common.
- A dominant macro narrative around energy, commodities, or supply chains.
- Geopolitical tension is often present (cause or consequence of supply pressures).

## What Works

- **Commodity-equity exposure during inflation upswings:** Energy producers, miners, agricultural names benefit from input-price tailwinds.
- **Pricing-power equities:** Companies with demonstrated ability to raise prices faster than costs — typically consumer staples, regulated utilities with passthrough, certain industrials.
- **Short-duration relative to long-duration:** When rates trend higher, short-duration assets and short-duration equities (lower P/E, current cash flow) outperform long-duration growth.
- **Trend-following on commodities and the dollar:** Sustained macro trends in these instruments tend to be tradable.
- **Vol-buying around CPI prints and Fed meetings:** Realized vol on macro release days tends to exceed pre-event implied.

## What Fails

- **Long-duration growth at premium multiples:** Discount-rate compression goes into reverse.
- **Buy-the-dip on the index:** Dips extend; the up-and-to-the-right tape is over.
- **Volatility-selling carry strategies at full size:** The vol regime is structurally different; persistent dips are no longer free money.
- **60/40 allocation in any form:** Stocks and bonds correlate positively in inflation regimes; the diversification benefit collapses.
- **Mean-reversion at index level:** Trends persist longer than buyers of dips expect.

## Duration Expectations

The 1973-1975 episode ran roughly two years; the 1978-1982 episode ran roughly four years if dated from the second oil shock to the August 1982 turn. Stagflation regimes are slow to start and slow to end. Honest range from historical examples: 18 months to 8+ years, with the long tail driven by policy credibility being hard to rebuild. Median plausibly 3 years. Point estimates are not useful; the regime ends when inflation breakeven trends durably lower or when policy demonstrably gets ahead of the curve.

## Detection Signals

1. CPI YoY persistently above target (>3.5% for 6+ months) while real GDP growth weak or negative.
2. Breakeven inflation curve elevated and not anchoring down.
3. SPX 20d realized vol persistently above 15%; VIX above 20 baseline.
4. Sector dispersion high; energy/materials leading on multi-month basis.
5. MOVE elevated above 130; rate vol embedded.
6. Credit spreads widening, particularly HY.
7. Yield curve behavior unusual (steepener driven by inflation expectations, or bear-flattening as the central bank tightens).

## Exit Triggers

1. **Durable disinflation:** CPI YoY trend lower for two consecutive quarters with broad-basket confirmation (not just one component).
2. **Policy credibility restoration:** The central bank visibly gets ahead of the curve (real rates positive and confirmed sustainable).
3. **Energy or commodity price normalization:** The supply-shock root cause resolves.
4. **Bond market relief rally that holds:** Long-end yields decline 100+ bps and stay down.
5. **Equity leadership rotation back to long-duration growth that holds for 60+ trading days.**

Historically the turn has been hard to call in real time. The August 1982 turn was the most-cited example; it was missed by many at the start. Exit signals must hold for weeks before the regime label should change.

## Confidence Notes

The historical record for stagflation regimes is the deepest of the four seed profiles because of the well-studied 1970s episodes. Primary sources, academic literature, and policy memoirs are abundant.

Areas of lower confidence:
- The statistical-marker ranges are drawn from the 1970s episodes and may not translate cleanly to a modern market structure with different microstructure (electronic trading, ETFs, derivatives volume, central-bank balance-sheet tools that didn't exist).
- The 2022 partial analog is informative but not conclusive. Whether it should count as a "mini-stagflation episode" or as a "growth-scare with concurrent inflation" is genuinely debatable.
- Causal versus correlational features are mixed in the markers list; tighter work distinguishing "what defines the regime" from "what often appears alongside it" would help.

This profile is a starting point. Refinement should track both fresh stagflation episodes (if any) and ongoing research into the 1970s record.

## Open Questions

- **Treatment of energy-led inflation versus broad-basket inflation:** Are these the same regime or distinct sub-regimes? Blocks: precise routing. Owner: Research Department.
- **How does QE/QT history change the markers?** Pre-2008 episodes occurred without modern balance-sheet policy; current markers may differ. Blocks: confidence in marker ranges. Owner: Research Department.
