# QQQ

**Ticker:** QQQ
**Document version:** 0.1 (draft)
**Date:** 2026-05-29
**Owner:** Mike White
**Status:** Draft

## Identification

Invesco QQQ Trust (issuer: Invesco). Tracks the Nasdaq-100 index — the 100 largest non-financial Nasdaq listings, modified market-cap weighted. QQQ is in the universe as the regime expression vehicle for tech beta and long-duration growth. It is the dispersion partner to SPY: the SPY-vs-QQQ spread is itself a regime-discrimination signal (tech leadership vs broad market). QQQ belongs to the Liquid ETFs category and overlaps functionally with XLK; the profile distinction is that QQQ carries non-tech mega-cap consumer/communication exposure (AMZN, COST, PEP, TSLA) that XLK does not.

## Sector / Industry

- **GICS sector:** N/A (multi-sector index ETF; tech-heavy)
- **GICS industry:** Equity ETF, US Large-Cap Growth
- **Market-cap band:** N/A (constituents are mega/large-cap growth-tilted)
- **Index memberships:** Tracks NDX
- **Notable concentrations:** Heavy top-10 concentration in mega-cap tech and adjacent platforms; AAPL, MSFT, NVDA, AMZN, META, GOOGL together represent a large fraction of weight. Single-name idiosyncratic shocks in any of these propagate disproportionately into QQQ

## Liquidity Profile

| Metric | Value | Notes |
|---|---|---|
| Average daily dollar volume (90d) | High single-digit $B/day | Second only to SPY among US equity ETFs |
| Spread (typical, RTH) | Sub-bp | Tight throughout RTH |
| Options open interest (total) | Millions of contracts | Daily, weekly, monthly expiries available |
| Options ADV | High six- to low seven-figure contracts/day | 0DTE participation present but lower share than SPY |
| Borrow availability / cost | Easy | General collateral |
| After-hours liquidity | Moderate-to-good vs other ETFs | NQ futures carry overnight signal more cleanly |

## Behavioral Characteristics

QQQ is higher-beta than SPY in most regimes (historically ~1.1–1.3, regime-dependent) and exhibits stronger duration sensitivity to rates. Trends are more persistent in melt-up regimes and drawdowns are deeper in tightening regimes. Vol-of-vol elevated relative to SPY because top constituents have idiosyncratic catalyst calendars (earnings, product launches, regulatory actions). Gap behavior tracks NQ futures overnight. Single-name surprises in NVDA, AAPL, MSFT, AMZN materially move QQQ in a way that SPX-style diversification damps inside SPY.

## News Sensitivity

- **Earnings:** Highly sensitive to mega-cap constituent earnings windows; QQQ's realized vol concentrates around late-January, late-April, late-July, late-October when top-5 weights report
- **Macro releases (CPI, FOMC, NFP):** Highly sensitive, with a duration tilt — surprise hawkish prints typically hit QQQ harder than SPY; surprise dovish prints typically benefit QQQ more than SPY. Worth measuring the SPY-vs-QQQ asymmetric response as a regime signal
- **Sector-specific catalysts:** Semi cycle (NVDA, AMD, AVGO inside QQQ), AI-narrative news, EU/US tech regulatory action, China tech-export controls
- **Single-name catalysts:** NVDA earnings post-2023 have been a QQQ-level event; AAPL product cycles, MSFT cloud/AI guidance, AMZN AWS guidance similarly
- **Social-media sensitivity:** Moderate directly; high indirectly via NVDA / TSLA narrative cycles

## Catalyst Calendar Pattern

- **Earnings cadence:** No direct cadence; tracks mega-cap tech reporting calendar
- **Conferences / analyst days:** WWDC (June, AAPL), GTC (NVDA, typically Q1–Q2), MSFT Build, AWS re:Invent (December), Google I/O
- **Product / regulatory cycles:** Annual iPhone cycle (September), NVDA datacenter product cadence, ongoing antitrust/regulation in EU and US
- **Other recurring catalysts:** Nasdaq-100 annual reconstitution (December), quarterly rebalances, monthly OPEX

## Retail vs Institutional Flow Profile

- **Retail concentration:** Moderate-to-high; QQQ is the default tech-bull vehicle for retail
- **Institutional ownership:** Dominated by asset managers, model portfolios, and ETF-of-ETF holdings
- **Short interest:** Low on the ETF mechanically
- **Options skew profile:** Persistent put skew similar to SPY but with a sharper smile near earnings clusters; call-side bid expands during AI-narrative phases (observed 2023–2025, unverified going forward)
- **Dark-pool / off-exchange share:** Substantial

## Known Trap Setups

- **Post-mega-cap-earnings gap fade:** Historical pattern that a single mega-cap earnings gap (e.g., NVDA) drives QQQ to an opening extreme that is faded during the session as dealers re-hedge. Unverified at this profile's confidence; needs the system's own measurement
- **SPY-QQQ dispersion squeeze:** Hypothesis that when SPY-QQQ correlation pins near 1.0 across multiple sessions, the next dispersion expansion is tradeable as a pairs setup. Explicitly a hypothesis to test, not a known edge
- **Tech-regulation headline trap:** Headline-driven QQQ drops on EU/US regulatory news historically retrace within sessions because the economic impact is small relative to the headline reaction. Pattern, not guarantee

Mike's review requested: whether SPY-QQQ dispersion belongs as a single-strategy archetype or as a regime-input feature.

## Historical Strategy Fit

- **What has worked (historical pattern):** Trend-following long in melt-up regimes; SPY-QQQ pairs trades around macro-surprise asymmetric responses
- **What has not worked (historical pattern):** Naive shorting during AI-narrative phases (2023–2025); mean-reversion at intraday timeframes during sustained-trend regimes
- **Mike's prior live experience (if any):** Substantial 0DTE/1DTE options scanner work on QQQ alongside SPY through Mark 1–11+ versions; same lesson applies — backtest-to-live degradation and event-clustering issues

## Per-Regime Behavior

| Regime | Expected behavior | Strategy bias |
|---|---|---|
| Late-cycle melt-up | Outperforms SPY, trends strongly, low realized vol, call skew bid | Trend-follow long; SPY-QQQ pairs long-QQQ; avoid shorts |
| Stagflation | Underperforms SPY due to duration tilt; sharp factor rotations | Reduce QQQ directional exposure; consider QQQ-short vs XLE-long expressions |
| Crisis recovery | Leads SPY higher off lows historically, but with deeper initial drawdown | Bias long after breadth confirmation; size after regime classifier confirms recovery state, not on dip-buying alone |
| Wartime | Underperforms SPY initially (growth offered, defensives bid); recovery depends on duration of conflict | Reduce QQQ exposure; rotate to DIA / defense names / energy |

## Maintenance Notes

- **Last reviewed:** 2026-05-29
- **Reviewed by:** seed-subagent
- **Last material change:** 2026-05-29 — initial seed profile created
- **Open issues for next review:**
  - Measure actual SPY-QQQ beta and correlation by regime once classifier is online
  - Validate the post-mega-cap-earnings gap-fade pattern against system data
  - Decide whether to maintain a QQQ-vs-XLK comparison sub-document (distinct exposure profile)

## Confidence Notes

Seed profile. High-confidence portions: QQQ as a tech-beta vehicle, duration sensitivity, mega-cap concentration risk. Medium-confidence: per-regime characterizations, which generalize from publicly known market structure but have not been measured by this system. Low-confidence (explicitly hypothesis-grade): the named trap setups. Mike's review and lock-in required before any of these characterizations drive strategy-generation prompts.
