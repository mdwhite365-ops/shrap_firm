# NVDA

**Ticker:** NVDA
**Document version:** 0.1 (draft)
**Date:** 2026-05-29
**Owner:** Mike White
**Status:** Draft

## Identification

NVIDIA Corporation — designer of GPUs and accelerated-computing platforms; primary supplier of AI training/inference silicon to hyperscalers and enterprises. NVDA is in the universe as the AI/semi liquidity bellwether and as one of the deepest single-name options markets in the world. The thesis is twofold: NVDA earnings have become index-level events since 2023, and NVDA's options open interest concentrates retail and institutional positioning in ways that make trap-detection and dealer-positioning analysis particularly tractable. NVDA belongs to the Mega-Cap Tech category and overlaps the High-Retail-Interest category — both labels apply and the profile reflects dual-category behavior.

## Sector / Industry

- **GICS sector:** Information Technology
- **GICS industry:** Semiconductors & Semiconductor Equipment
- **Market-cap band:** Mega-cap
- **Index memberships:** SPX, NDX, SOX (PHLX Semiconductor), XLK, SMH, SOXX
- **Notable concentrations:** Top-weight in SOX and SMH; significant single-customer concentration risk (hyperscaler capex cycles); meaningful China-export-control exposure; founder-CEO narrative attachment

## Liquidity Profile

| Metric | Value | Notes |
|---|---|---|
| Average daily dollar volume (90d) | Tens of $B/day | Routinely top of US single-name tape |
| Spread (typical, RTH) | Sub-bp | Tight |
| Options open interest (total) | Among deepest single-name options markets globally | OI distribution skews call-heavy in narrative-bull regimes |
| Options ADV | Very high; weekly and 0DTE participation elevated | Earnings-week volumes are extreme |
| Borrow availability / cost | Easy | General collateral |
| After-hours liquidity | Good for a single name | Post-earnings gap dynamics are pronounced |

## Behavioral Characteristics

High-beta to AI/semi narrative, high vol-of-vol around earnings, trend-persistent in narrative-bull regimes, gap-prone on supply-chain and customer-capex news. NVDA does not mean-revert reliably on short timeframes during sustained trend regimes; mean-reversion strategies that worked in 2022 broke through 2023–2025. Single-name idiosyncratic moves are frequent and large relative to QQQ/SPY moves on the same day. Options-OI concentration creates measurable dealer-gamma effects near large strikes, particularly into and out of earnings.

## News Sensitivity

- **Earnings:** Reliably large post-earnings moves with index-level implications since 2023. Options-implied move has often underpriced realized in narrative-bull phases and overpriced realized in consolidation phases. Drift behavior post-earnings is regime-dependent
- **Macro releases (CPI, FOMC, NFP):** Sensitive via duration channel; rate-cut narratives historically benefit NVDA disproportionately
- **Sector-specific catalysts:** Hyperscaler capex guidance (MSFT, GOOGL, META, AMZN earnings days), TSMC monthly revenue, China export-control headlines, competitor product launches (AMD MI-series, custom hyperscaler silicon)
- **Single-name catalysts:** GTC conference (typically Q1–Q2), Computex, product roadmap updates, partnership announcements, US/China export-license news
- **Social-media sensitivity:** High. NVDA is a primary AI-narrative ticker for retail and a frequent subject of social signal in both directions

## Catalyst Calendar Pattern

- **Earnings cadence:** Mid-to-late February / May / August / November (fiscal year ends late January)
- **Conferences / analyst days:** GTC (annual, typically Q1–Q2), Computex (May/June), CES (January), various hyperscaler events
- **Product / regulatory cycles:** Annual datacenter GPU generation cadence; ongoing US export-control review cycles
- **Other recurring catalysts:** TSMC monthly revenue (mid-month), hyperscaler quarterly capex commentary, semiconductor industry monthly billings (SIA)

## Retail vs Institutional Flow Profile

- **Retail concentration:** High, sustained through 2023–2026 narrative cycle. Retail option flow concentrates in weekly call buying near earnings
- **Institutional ownership:** Large active and passive ownership; momentum-factor exposure is substantial
- **Short interest:** Historically low to moderate; not a typical short-squeeze profile, but dealer-hedging flows create squeeze-like dynamics
- **Options skew profile:** Persistent call-side bid during narrative-bull periods (observed 2023–2025, unverified going forward); put skew steepens into earnings
- **Dark-pool / off-exchange share:** Substantial; off-exchange flow consultation via intelligence layer recommended over lit-tape inferences

## Known Trap Setups

- **Earnings-week call-flow chase trap:** Historical pattern of retail call-buying into earnings producing post-event vol crush + directional retracement. Reliability has been inconsistent across cycles; needs regime-conditional measurement
- **GTC-week narrative impulse fade:** Pattern of sharp pre-conference call accumulation followed by sell-the-news fade. Observed multiple years; not a reliable standalone setup
- **OPEX-week pin near max-pain strike:** Options-OI concentration produces measurable pin dynamics around large monthly expiries when dealer-gamma positioning is long-gamma. Requires the system's own dealer-positioning measurement to validate; do not rely on third-party gamma estimates
- **Hyperscaler-capex-cut headline trap:** Headline-driven gaps on hyperscaler capex guidance have produced asymmetric reactions; the fade direction depends on whether the cut is interpreted as cyclical or structural. Pattern, not edge

Mike's review requested: dealer-gamma measurement is a build-vs-buy decision that affects this profile's strategy fit.

## Historical Strategy Fit

- **What has worked (historical pattern):** Trend-following long with regime confirmation during narrative-bull phases; trap-detection on extreme retail call-flow days
- **What has not worked (historical pattern):** Naive shorting during AI-narrative phases; mean-reversion at short timeframes during sustained trends; static breakout strategies that ignored hyperscaler-capex context
- **Mike's prior live experience (if any):** Substantial. NVDA was a primary instrument in scanner Mark 1–11+ work. Lessons documented: live-vs-backtest degradation was material; event-cluster false-positives were recurring; regime-blindness produced systematic losses in 2023 melt-up phase. These lessons are load-bearing for this profile

## Per-Regime Behavior

| Regime | Expected behavior | Strategy bias |
|---|---|---|
| Late-cycle melt-up | Strong outperformance, call skew bid, narrative-driven trends | Trend-follow long; trap-detection on extreme call-flow days; avoid contrarian shorts |
| Stagflation | Underperforms due to duration; capex-cycle concerns dominate narrative | Reduce directional exposure; favor pairs (NVDA vs AVGO) over outright positions |
| Crisis recovery | Leads semis higher off lows historically, with deep initial drawdown | Bias long after regime classifier confirms recovery; size with vol-awareness |
| Wartime | China-export-control becomes dominant concern; supply-chain headlines drive gaps | Reduce exposure; trap-detection remains valid; avoid systematic directional bias |

## Maintenance Notes

- **Last reviewed:** 2026-05-29
- **Reviewed by:** seed-subagent
- **Last material change:** 2026-05-29 — initial seed profile created
- **Open issues for next review:**
  - Quantify options-OI concentration and dealer-gamma effects once measurement infrastructure exists
  - Validate or kill the four named trap setups with system-measured data
  - Decide whether NVDA-AVGO and NVDA-AMD pairs warrant dedicated strategy archetype documents

## Confidence Notes

Seed profile. High-confidence portions: NVDA as AI/semi bellwether, deep options market, hyperscaler-capex dependence, narrative-bull historical context. Medium-confidence: per-regime behavior characterizations. Low-confidence (hypothesis-grade): the named trap setups, dealer-gamma claims, and the specifics of OPEX pin dynamics — these need the system's own measurement before being treated as edge. Mike's review and lock-in required before strategy generation conditions on these characterizations. Help Mike be right, not happy.
