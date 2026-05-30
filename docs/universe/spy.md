# SPY

**Ticker:** SPY
**Document version:** 0.1 (draft)
**Date:** 2026-05-29
**Owner:** Mike White
**Status:** Draft

## Identification

SPDR S&P 500 ETF Trust (issuer: State Street Global Advisors). Tracks the S&P 500 index — 500 large-cap US equities, market-cap weighted. SPY is in the universe as the regime expression anchor: it is the cleanest, deepest instrument the system has for taking a directional view on US large-cap risk, and it is the reference point against which every other universe member's beta and dispersion behavior is measured. SPY belongs to the Liquid ETFs category and does not overlap any single-name category, but it is the implicit benchmark for the mega-cap and mid-cap names.

## Sector / Industry

- **GICS sector:** N/A (broad index ETF)
- **GICS industry:** Equity ETF, US Large-Cap Blend
- **Market-cap band:** N/A (fund AUM is mega-scale; constituents are mega/large-cap)
- **Index memberships:** Tracks SPX; itself a constituent of broad-ETF baskets
- **Notable concentrations:** Top-10 holdings concentration has been historically elevated through the 2023–2026 mega-cap run; tech / communication-services weight is the dominant single-factor exposure

## Liquidity Profile

| Metric | Value | Notes |
|---|---|---|
| Average daily dollar volume (90d) | Tens of $B/day (top of US tape) | Among the deepest-traded instruments globally; exact figure to be populated by data agent |
| Spread (typical, RTH) | Sub-bp during RTH | Tightest of any universe member |
| Options open interest (total) | Multi-million contracts across strikes/expiries | Daily, weekly, monthly, and quarterly expiries; among deepest options markets in the world |
| Options ADV | High millions/day | Includes 0DTE flow that meaningfully shapes intraday gamma |
| Borrow availability / cost | Easy, general collateral | Effectively unlimited |
| After-hours liquidity | Good vs other ETFs; thinner than RTH | ES futures carry the overnight signal more cleanly |

## Behavioral Characteristics

SPY trends and chops in line with the broader index. By construction it is low-idiosyncratic — moves are driven by macro, factor rotations, and the largest constituents. Beta to itself is 1.00 by definition; vol-of-vol is dominated by VIX dynamics and is regime-dependent rather than name-specific. Gap behavior is driven by overnight ES action; daytime drift patterns (opening drive, midday chop, last-hour rebalancing) are well-documented in the public literature but should be validated by the system on current data rather than assumed. SPY is not the place to look for single-name surprise; it is the place to express conviction about state.

## News Sensitivity

- **Earnings:** No direct earnings event for SPY itself; sensitivity comes via mega-cap constituent earnings (AAPL, MSFT, NVDA, GOOGL, META, AMZN) which together can move SPY meaningfully on their report dates
- **Macro releases (CPI, FOMC, NFP):** Highly sensitive. FOMC days and CPI prints are the single largest scheduled-event drivers; observed pattern is that implied moves on these days are frequently underpriced relative to realized, though the system must measure this on current data
- **Sector-specific catalysts:** Sector rotations show up as factor moves inside SPY; the magnitude depends on the rotating sector's index weight
- **Single-name catalysts:** Only when a top-5 weight name has a binary catalyst (e.g., NVDA earnings post-2023)
- **Social-media sensitivity:** Low directly; high indirectly via mega-cap names that dominate retail attention

## Catalyst Calendar Pattern

- **Earnings cadence:** No direct cadence; mirrors aggregate S&P 500 reporting calendar (mid-Jan, mid-Apr, mid-Jul, mid-Oct peak weeks)
- **Conferences / analyst days:** N/A directly; sector conferences (e.g., semis in summer, healthcare in January) can move sub-sector weights
- **Product / regulatory cycles:** N/A
- **Other recurring catalysts:** FOMC (8/yr), CPI (monthly), NFP (monthly), PCE (monthly), ISM, quad-witch expiries (quarterly), index rebalances (quarterly), end-of-month / end-of-quarter rebalancing flows

## Retail vs Institutional Flow Profile

- **Retail concentration:** Moderate; SPY is the default retail and advisor-allocator vehicle, but 0DTE flow has skewed share toward shorter-dated speculative positioning. Unverified hypothesis worth measuring: 0DTE share of SPY options volume materially shapes intraday pinning behavior near round strikes
- **Institutional ownership:** Dominated by asset managers and ETF-of-ETF holdings; not a useful idiosyncratic signal
- **Short interest:** Mechanically low on the ETF (creation/redemption arb)
- **Options skew profile:** Persistent put skew (insurance bid) is the structural baseline; flattens in melt-ups, steepens in crisis. Worth tracking as a regime input rather than a trade trigger
- **Dark-pool / off-exchange share:** Substantial; consult TRF / off-exchange data via the intelligence layer rather than relying on lit-tape inferences

## Known Trap Setups

- **0DTE pin / unpin near OPEX-adjacent strikes:** Historical pattern (post-2022 0DTE expansion) is that dealer gamma near large open-interest strikes can compress intraday range, then snap on close-time hedging flow. Treat as hypothesis pending the system's own dealer-positioning measurement; do not trade on third-party gamma estimates without validating
- **FOMC-day implied-move fade:** Observed pattern that the initial post-statement move frequently reverses during the press conference; this is a popular retail setup and therefore a candidate for being faded in the opposite direction. Unverified at this profile's confidence level
- **Index-rebalance day flows:** Quarter-end rebalance windows have documented mechanical flow; whether they produce a tradable edge on SPY itself (vs on the rebalanced single names) is unverified for this system

Mike's review requested: which of these the system should prioritize instrumenting first.

## Historical Strategy Fit

- **What has worked (historical pattern):** Trend-following with regime-conditioning (avoiding the chop-around-FOMC windows); macro-overlay positioning (long SPY when statistical-regime layer signals expansion + breadth confirmation)
- **What has not worked (historical pattern):** Naive mean-reversion on intraday timeframes during trending regimes; static breakout strategies that ignore regime
- **Mike's prior live experience (if any):** Substantial scanner / 0DTE-options work on SPY through Mark 1–11+ versions; documented lesson that backtest win rates did not transfer cleanly to live, and that signal clustering near macro events was a recurring failure mode. Treat that lesson as load-bearing

## Per-Regime Behavior

| Regime | Expected behavior | Strategy bias |
|---|---|---|
| Late-cycle melt-up | Persistent grind higher, low realized vol, put skew flattens, dispersion compresses | Trend-follow long, fade vol-spikes; avoid mean-reversion short |
| Stagflation | Choppy, headline-driven, range-bound with sharp factor rotations underneath | Volatility-of-volatility plays; reduce directional sizing; favor dispersion via XLE/XLV/XLK vs SPY |
| Crisis recovery | Sharp asymmetric rallies, breadth thrusts, put-skew collapse | Bias long with breadth confirmation; size up after volatility regime change is confirmed by classifier, not before |
| Wartime | Headline-driven gaps, sector dispersion (energy + defense bid, growth offered), correlation regime shifts | Reduce SPY directional exposure; express views via DIA / XLE / defense names instead |

## Maintenance Notes

- **Last reviewed:** 2026-05-29
- **Reviewed by:** seed-subagent
- **Last material change:** 2026-05-29 — initial seed profile created
- **Open issues for next review:**
  - Populate liquidity table with measured values from data agent
  - Validate or kill the three "known trap setups" with the system's own measurements
  - Decide whether per-regime behavior table should be machine-readable (YAML sidecar) for the Regime Router

## Confidence Notes

This is a seed profile. The high-level framing (regime anchor, factor exposures, macro-event sensitivity) is well-grounded in publicly known market structure. The specific trap setups and per-regime behaviors are descriptive starting points drawn from market lore and the vision document, not from measurements the system itself has produced. They are explicitly hypotheses to be confirmed or rejected by the Universe Curator Agent once real data is flowing. Mike's review and lock-in is required before any of these characterizations are treated as authoritative for strategy generation.
