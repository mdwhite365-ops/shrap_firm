# AAPL

**Ticker:** AAPL
**Document version:** 0.1 (draft)
**Date:** 2026-05-29
**Owner:** Mike White
**Status:** Draft

## Identification

Apple Inc. — designs and sells consumer hardware (iPhone, Mac, iPad, Watch, Vision), wearables, and services (App Store, iCloud, Apple Pay, advertising). AAPL is in the universe as the liquid behavioral anchor: technically mega-cap by market value, but in the context of this universe it functions as the calm reference point — the name with the most predictable catalyst calendar, the deepest institutional ownership, the lowest single-name idiosyncratic surprise frequency, and the cleanest pre/post-event pattern in the mega-cap set. Mike has prior live trading data in AAPL which provides starting calibration. AAPL belongs to the Mega-Cap Tech category.

## Sector / Industry

- **GICS sector:** Information Technology
- **GICS industry:** Technology Hardware, Storage & Peripherals
- **Market-cap band:** Mega-cap (used here as the universe's calm reference rather than as a high-beta exposure)
- **Index memberships:** SPX, NDX, XLK
- **Notable concentrations:** China-revenue exposure (manufacturing + sales); single-product (iPhone) revenue concentration; regulatory exposure (EU DMA, App Store litigation)

## Liquidity Profile

| Metric | Value | Notes |
|---|---|---|
| Average daily dollar volume (90d) | Tens of $B/day | Top of US single-name tape |
| Spread (typical, RTH) | Sub-bp | Tight |
| Options open interest (total) | Multi-million contracts | Deep across weekly, monthly, LEAPS |
| Options ADV | High; 0DTE/weekly participation present but lower retail concentration than TSLA/NVDA | |
| Borrow availability / cost | Easy | General collateral |
| After-hours liquidity | Good | Earnings-gap dynamics are more measured than NVDA/TSLA |

## Behavioral Characteristics

Lower-beta than QQQ in most regimes; trend behavior is more measured and chop is more contained than the high-retail names. AAPL's defining feature in this universe is calmness: realized vol is typically lower than NVDA/TSLA, gaps are smaller, mean-reversion at multi-day timeframes is more reliable, and surprise idiosyncratic moves are less frequent. This makes AAPL useful as the behavioral baseline against which other names' anomalies are measured. Vol-of-vol is moderate. Gap behavior is dominated by earnings and product-cycle news rather than CEO posts or supply-chain headlines.

## News Sensitivity

- **Earnings:** Measured post-earnings moves relative to other mega-caps. Options-implied move has historically been a reasonable estimator of realized; drift behavior is weaker than NVDA/TSLA
- **Macro releases (CPI, FOMC, NFP):** Sensitive but less than long-duration peers; moves more like SPY than like NVDA
- **Sector-specific catalysts:** Smartphone unit data (IDC/Counterpoint), China consumer data, EU regulatory action (DMA fines and remediations)
- **Single-name catalysts:** WWDC (June), iPhone launch event (typically September), earnings (late January / late April / late July / late October — Apple fiscal year ends September)
- **Social-media sensitivity:** Lower than TSLA/NVDA; AAPL is not a narrative-driven retail vehicle to the same degree

## Catalyst Calendar Pattern

- **Earnings cadence:** Late January / late April / late July / late October (Apple fiscal year ends late September)
- **Conferences / analyst days:** WWDC (June), iPhone keynote (September), occasional special events
- **Product / regulatory cycles:** Annual iPhone cycle anchors the narrative calendar; ongoing EU DMA implementation cycle; App Store litigation timeline
- **Other recurring catalysts:** Holiday-quarter sell-through commentary; supply-chain reports from Asian press around new-product launches

## Retail vs Institutional Flow Profile

- **Retail concentration:** Moderate; AAPL is held broadly by retail but is not a meme/narrative ticker in the same sense as TSLA/NVDA
- **Institutional ownership:** Very large active and passive ownership; Berkshire Hathaway position is a public reference point and itself a sentiment signal
- **Short interest:** Low
- **Options skew profile:** Persistent moderate put skew; less dramatic than NVDA earnings-week skew
- **Dark-pool / off-exchange share:** Substantial

## Known Trap Setups

- **Pre-launch event drift fade:** Historical pattern that AAPL rallies into iPhone launch events and gives back some gain post-event. Reliability has degraded over multiple years as the pattern became well-known; treat as hypothesis, not as edge
- **Earnings vol-crush short-premium pattern:** Because implied move is historically a reasonable estimator of realized, short-premium structures around AAPL earnings have a more favorable risk/reward profile than equivalent NVDA/TSLA structures. Unverified going forward; needs the system's own measurement to validate before being used at size
- **DMA / regulatory headline fade:** Regulatory-headline drops have historically retraced because the financial impact has been small relative to the headline reaction. Pattern, not guarantee

Mike's review requested: whether AAPL's role as "calm reference" should be encoded as a beta/vol expectation in the regime classifier inputs.

## Historical Strategy Fit

- **What has worked (historical pattern):** Measured trend-following with longer holding periods; short-premium structures around earnings in low-vol regimes; pair trades (AAPL vs QQQ for hedging beta)
- **What has not worked (historical pattern):** Aggressive intraday momentum strategies (insufficient volatility); narrative-chase strategies (AAPL is not narrative-driven enough); short-dated options strategies that rely on large realized moves
- **Mike's prior live experience (if any):** Documented. AAPL was a primary instrument in scanner Mark 1–11+ work alongside SPY/QQQ/TSLA/NVDA. Lesson recorded: AAPL produced fewer false signals than TSLA/NVDA but also fewer high-edge setups, which is consistent with its "calm reference" framing

## Per-Regime Behavior

| Regime | Expected behavior | Strategy bias |
|---|---|---|
| Late-cycle melt-up | Steady participation, underperforms higher-beta peers; consistent grind | Measured trend-follow long; short-premium around earnings if implied is rich |
| Stagflation | Holds up better than higher-duration peers; consumer-demand concerns dominate | Reduce directional exposure; favor pairs (AAPL long vs higher-beta short) |
| Crisis recovery | Lags initial rally off lows then catches up as breadth broadens | Bias long after regime classifier confirms recovery; longer holding periods than TSLA/NVDA setups |
| Wartime | China-exposure becomes dominant concern; headline-driven gaps possible | Reduce exposure; do not use AAPL as the calm-reference baseline during regimes where China-exposure dominates |

## Maintenance Notes

- **Last reviewed:** 2026-05-29
- **Reviewed by:** seed-subagent
- **Last material change:** 2026-05-29 — initial seed profile created
- **Open issues for next review:**
  - Validate the "calm reference" framing with measured beta and idiosyncratic-vol estimates
  - Quantify earnings implied-vs-realized historical fit before promoting short-premium archetypes
  - Decide whether AAPL's regime-classifier role should be formalized as an input feature

## Confidence Notes

Seed profile. High-confidence portions: AAPL as a measured, institutionally-dominated, calendar-driven mega-cap; the broad shape of its catalyst calendar. Medium-confidence: the calm-reference framing for this universe specifically — it is conceptually right but should be verified by measured idiosyncratic-vol comparisons. Low-confidence (hypothesis-grade): the named trap setups, particularly the short-premium-around-earnings claim, which has destroyed accounts when implied has been wrong. Mike's review and lock-in required before short-premium archetypes are activated on AAPL at size. Help Mike be right, not happy.
