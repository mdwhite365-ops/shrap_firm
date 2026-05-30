# TSLA

**Ticker:** TSLA
**Document version:** 0.1 (draft)
**Date:** 2026-05-29
**Owner:** Mike White
**Status:** Draft

## Identification

Tesla, Inc. — electric vehicle manufacturer with adjacent businesses in energy storage, solar, robotaxi/autonomy, and humanoid robotics. TSLA is in the universe as the archetypal high-retail-interest name and as a classic trap-setup candidate. The thesis for inclusion is not the company's fundamental valuation; it is that TSLA has the densest persistent retail option flow, the loudest social-media narrative attachment, and the most-frequently-observed liquidation-sweep behavior of any liquid US single name. TSLA belongs to the High-Retail-Interest category and overlaps the Mega-Cap Tech category — the profile reflects dual-category behavior, with trap-detection priority taking precedence per the universe README.

## Sector / Industry

- **GICS sector:** Consumer Discretionary
- **GICS industry:** Automobiles
- **Market-cap band:** Mega-cap
- **Index memberships:** SPX, NDX, sector-discretionary ETFs (XLY)
- **Notable concentrations:** Single-CEO narrative concentration risk (Elon Musk personal news drives the tape); China-revenue exposure; non-trivial regulatory exposure (NHTSA, FSD/Autopilot investigations)

## Liquidity Profile

| Metric | Value | Notes |
|---|---|---|
| Average daily dollar volume (90d) | Tens of $B/day, regime-dependent | Among the top single-name traded equities globally |
| Spread (typical, RTH) | ~1 bp | Tight throughout RTH |
| Options open interest (total) | Multi-million contracts | One of the deepest single-name options markets |
| Options ADV | Very high; 0DTE/1DTE participation elevated | Retail-flow concentration on weekly expiries observed historically |
| Borrow availability / cost | Easy (general collateral) | Periods of HTB historically rare for TSLA proper |
| After-hours liquidity | Good for a single name | News-gap behavior is pronounced after-hours |

## Behavioral Characteristics

High-beta, high-vol-of-vol, gap-prone. TSLA does not behave like a typical mega-cap; it has single-name idiosyncratic move frequency closer to a mid-cap. Trend persistence is regime-dependent and often narrative-driven (AI/robotaxi cycles, delivery-miss cycles). Intraday mean-reversion is unreliable because narrative shifts and CEO posts can invalidate a level within minutes. Gap behavior is a defining feature: overnight news, delivery prints, and Musk social-media activity routinely produce 3–8% opening gaps. Vol-of-vol is elevated relative to other mega-caps even outside earnings windows.

## News Sensitivity

- **Earnings:** Reliably large post-earnings moves; options-implied move has historically under- and over-priced realized depending on regime (no consistent direction). Drift behavior is unreliable
- **Macro releases (CPI, FOMC, NFP):** Sensitive via duration / discretionary channels but secondary to single-name news
- **Sector-specific catalysts:** EV demand data, lithium/battery supply news, China NEV-policy shifts, competitor delivery results (BYD, Rivian)
- **Single-name catalysts:** Quarterly delivery numbers (start of January/April/July/October), AI Day / Robotaxi / Optimus events, regulatory actions on FSD, Musk personal news (X posts, court rulings, compensation rulings)
- **Social-media sensitivity:** Very high. Direct measurable response to Musk posts and to broader retail sentiment swings. This is a defining feature of the name

## Catalyst Calendar Pattern

- **Earnings cadence:** Late January / late April / late July / mid-to-late October
- **Conferences / analyst days:** Irregular product-event calendar (AI Day, Robotaxi unveiling, shareholder meeting). Cadence is announced ad-hoc and is itself a catalyst
- **Product / regulatory cycles:** Annual model-year cycles for major vehicles; ongoing NHTSA / FSD regulatory engagement
- **Other recurring catalysts:** Quarterly delivery numbers released first few days after quarter-end — historically a high-vol pre-announcement window

## Retail vs Institutional Flow Profile

- **Retail concentration:** Very high. TSLA is consistently among the top retail-flow tickers across brokers and is heavily represented in retail option volume
- **Institutional ownership:** Substantial but with concentrated active-manager positioning; passive ownership via index inclusion is large
- **Short interest:** Historically moderate; periodic spikes during narrative-bear phases
- **Options skew profile:** Call-side bid persistent during narrative-bull phases; put skew steepens around earnings and Musk-news clusters
- **Dark-pool / off-exchange share:** Substantial; consult intelligence layer rather than lit-tape inferences

## Known Trap Setups

- **Post-delivery-miss overnight gap fade:** Historical pattern in which a delivery miss produces a large overnight gap that fades intraday as institutions cover shorts and re-position. The fade is not reliable; it has failed in narrative-bear regimes. Hypothesis worth instrumenting
- **CEO-headline impulse reversal:** Single Musk post can drive a sharp impulse that retraces partially within session. Pattern observed across multiple years; direction of reversal depends on whether the headline confirms or contradicts the prevailing narrative
- **0DTE call-flow chase trap:** Observed pattern where retail 0DTE call buying into an intraday rally produces a late-day fade as dealers re-hedge and supply expires worthless. This is the canonical liquidation-sweep setup Mike's prior detector targets — TSLA is a high-priority instrument for trap-detection development
- **Earnings-call narrative whipsaw:** After-hours reaction to the prepared remarks frequently reverses during Q&A as forward-guidance language is parsed. Reliably large move; direction is not predictable

Mike's review requested: which of these to prioritize for the Trap Detection subsystem's initial calibration set.

## Historical Strategy Fit

- **What has worked (historical pattern):** Trap-detection / liquidation-sweep fade strategies on retail-driven impulses; volatility selling around well-priced earnings (regime-dependent)
- **What has not worked (historical pattern):** Trend-following on intraday timeframes during narrative-shift periods; static breakout strategies that ignore CEO-news risk; naive mean-reversion without sweep confirmation
- **Mike's prior live experience (if any):** Substantial. TSLA was a primary instrument in scanner Mark 1–11+ development. Documented lessons: signal clustering around delivery/earnings windows produced false positives; regime-blindness was a recurring failure mode; live-vs-backtest degradation was material. This profile inherits those lessons as load-bearing constraints

## Per-Regime Behavior

| Regime | Expected behavior | Strategy bias |
|---|---|---|
| Late-cycle melt-up | Outperforms broad market, narrative-bull dominant, call skew bid, sharp trend periods | Trap-detection on retail call chases; lean against extreme call-volume days; do not blindly trend-follow |
| Stagflation | Underperforms due to discretionary + duration; narrative becomes margin/demand-focused | Reduce directional exposure; favor short-vol around earnings if implied is rich |
| Crisis recovery | Sharp asymmetric rallies as risk-on returns; high-beta leader | Bias long with regime-classifier confirmation; size with Kelly-fraction awareness given vol |
| Wartime | Headline-driven, China-exposure becomes the dominant concern; sharp gaps possible | Reduce exposure; avoid systematic directional bias; trap-detection remains valid |

## Maintenance Notes

- **Last reviewed:** 2026-05-29
- **Reviewed by:** seed-subagent
- **Last material change:** 2026-05-29 — initial seed profile created
- **Open issues for next review:**
  - Quantify retail-concentration signal once intelligence layer is live
  - Validate the four named trap setups against system-measured data
  - Decide whether CEO-news headline classifier is a TSLA-specific subsystem or a general intelligence-department feature

## Confidence Notes

Seed profile. High-confidence portions: TSLA as a high-retail-interest, narrative-driven, gap-prone single name; CEO-news as a defining sensitivity. Medium-confidence: per-regime behaviors. Low-confidence (hypothesis-grade): the specific trap-setup patterns and the direction of their reliability — Mike's prior 0DTE work informs them but the system has not yet measured them. Mike's review and lock-in is required before these characterizations are treated as authoritative inputs to strategy generation. Help Mike be right, not happy.
