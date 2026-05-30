# LMT

**Ticker:** LMT
**Document version:** 0.1 (draft)
**Date:** 2026-05-29
**Owner:** Mike White
**Status:** Draft

## Identification

Lockheed Martin Corporation — largest US defense prime by revenue. Four reporting segments: Aeronautics (F-35, F-22, F-16), Missiles and Fire Control (PAC-3, HIMARS, JASSM, THAAD), Rotary and Mission Systems (Sikorsky, Aegis-related systems), and Space (satellites, launch). LMT is in the universe as the archetypal defense contractor for the Government-Contract Intelligence category. The thesis for inclusion is explicit and bounded: the edge comes from primary-source reading of USASpending obligation data, SAM.gov solicitations, congressional appropriations text, Senate Lobbying Disclosure Act filings, and DOD contract announcements — not from speculation about geopolitical conflict outcomes. LMT belongs to the Defense Contractors category.

## Sector / Industry

- **GICS sector:** Industrials
- **GICS industry:** Aerospace & Defense
- **Market-cap band:** Large-cap
- **Index memberships:** SPX, XLI, ITA, PPA, defense ETFs
- **Notable concentrations:** Heavy US government revenue concentration (a large majority of revenue is US government, primarily DOD); F-35 program is a substantial program-level concentration; significant foreign military sales exposure through FMS channel

## Liquidity Profile

| Metric | Value | Notes |
|---|---|---|
| Average daily dollar volume (90d) | Several hundred $M/day | Sufficient for sizing within Shrap's expected position scale |
| Spread (typical, RTH) | Low single-digit bps | Tight enough for the strategies the system expects to run |
| Options open interest (total) | Moderate vs mega-caps; substantial for a defense name | Monthly cycle dominant; weeklies thinner |
| Options ADV | Moderate | Less retail flow than TSLA/NVDA; institutional skew |
| Borrow availability / cost | Easy | General collateral |
| After-hours liquidity | Moderate | Earnings gaps occur but are less violent than mega-cap tech |

## Behavioral Characteristics

Lower-beta than the broad market in most regimes; defensive characteristics with cyclical overlays tied to defense budget cycles. Trend persistence is moderate and frequently driven by appropriations and contract-award cadence rather than by short-term news. Single-name idiosyncratic moves cluster around: earnings, contract-award announcements, program milestone news (F-35 delivery milestones, classified-program revelations), congressional appropriations markups, and Section 809 / DOD acquisition reform headlines. Gap behavior is relatively contained. Vol-of-vol is lower than mega-cap tech. LMT does not chase retail narratives and is rarely a meme target.

## News Sensitivity

- **Earnings:** Measured moves; guidance language about program execution and free-cash-flow conversion drives the reaction more than headline EPS
- **Macro releases (CPI, FOMC, NFP):** Less sensitive than typical SPX names; rate sensitivity is moderated by defensive characteristics
- **Sector-specific catalysts:** DOD budget request release (typically spring), congressional appropriations markup and conference reports, Continuing Resolution / shutdown news, FMS approvals (State Department / DSCA notifications), Defense Authorization Act (NDAA) sections affecting LMT programs
- **Single-name catalysts:** Contract awards posted on the DOD daily contract announcements page; F-35 program milestones; AUSA / Air Force Association / Navy League annual conferences
- **Social-media sensitivity:** Low. LMT is not a retail-narrative ticker

## Catalyst Calendar Pattern

- **Earnings cadence:** Late January / late April / late July / late October
- **Conferences / analyst days:** Annual investor day (irregular); AUSA (October), Air Force Association Air, Space & Cyber (September), Navy League Sea-Air-Space (April), Paris Air Show / Farnborough (alternating years)
- **Product / regulatory cycles:** Annual federal budget cycle: President's Budget request (typically February/March), HASC/SASC and HAC-D/SAC-D markups (spring/summer), conference and signing (varies), execution in fiscal year starting October 1
- **Other recurring catalysts:** DOD daily contract announcements (5pm ET on contract-award days), monthly USASpending updates, quarterly Senate LDA disclosures, end-of-fiscal-year obligation surges (September)

## Retail vs Institutional Flow Profile

- **Retail concentration:** Low. LMT is an institutional name; retail flow is incidental
- **Institutional ownership:** Heavy active and passive ownership; large defense-dedicated funds
- **Short interest:** Low; defense primes are not typical short-squeeze profiles
- **Options skew profile:** Modest put skew; earnings-week steepening is measured
- **Dark-pool / off-exchange share:** Substantial but not narrative-relevant

## Known Trap Setups

- **Continuing-Resolution / shutdown headline fade:** Historical pattern that shutdown headlines drive LMT and defense-peer drops that retrace because actual program execution is rarely materially affected by short CRs. Pattern, not guarantee — long CRs in different regimes have produced different reactions
- **Contract-award misinterpretation gap:** Headline reaction to a DOD contract announcement frequently misreads the contract's economic significance (ceiling vs obligated, multi-year vs single-year, IDIQ vs definitive). Hypothesis: primary-source reading of the announcement consistently provides higher-quality signal than headline reaction. This is precisely the edge the Government-Contract Intelligence thesis claims; it is unverified at this profile's confidence level
- **NDAA-markup overreaction:** Specific NDAA section additions or removals affecting LMT programs sometimes produce price reactions disproportionate to the financial impact. Requires primary-source NDAA section-level reading to evaluate, not headline reaction

Defense leverage in this universe is government-contract intelligence — USASpending obligations, SAM.gov solicitations, lobbying disclosures, congressional appropriations text. It is not war speculation. Strategy generation must respect this distinction; geopolitical-event-driven trade ideas on LMT require explicit Mike approval and additional risk controls because they violate the bounded-thesis framing.

Mike's review requested: explicit lock-in that war-speculation trades on LMT are out of scope for autonomous generation.

## Historical Strategy Fit

- **What has worked (historical pattern):** Multi-day to multi-week trades around appropriations cycles and contract-announcement clusters with primary-source confirmation; pair trades against XLI for defense-specific exposure isolation
- **What has not worked (historical pattern):** Intraday momentum strategies (insufficient volatility); narrative-chase strategies around conflict headlines (high false-positive rate, ethically and tactically problematic)
- **Mike's prior live experience (if any):** Limited. LMT was not a primary scanner-development instrument. This profile is built on category-thesis reasoning rather than on Mike's direct trade history, which is itself a confidence flag

## Per-Regime Behavior

| Regime | Expected behavior | Strategy bias |
|---|---|---|
| Late-cycle melt-up | Underperforms growth peers; steady dividend-yield-driven participation | Reduce defense overweight; favor pairs over outright |
| Stagflation | Outperforms broad market due to defensive characteristics and government-revenue insulation from consumer cycle | Lean to defense overweight with primary-source confirmation of appropriations trajectory |
| Crisis recovery | Lags initial recovery rallies; catches up as breadth broadens and budgets are reaffirmed | Underweight defense early in recovery; rotate in with regime-classifier confirmation |
| Wartime | Headline-driven moves are common; the bounded-thesis framing requires that LMT trades be driven by contract-award and appropriations evidence, NOT by conflict-outcome speculation | Strategy generation must surface the distinction explicitly; war-speculation trades require Mike approval |

## Maintenance Notes

- **Last reviewed:** 2026-05-29
- **Reviewed by:** seed-subagent
- **Last material change:** 2026-05-29 — initial seed profile created
- **Open issues for next review:**
  - Build the primary-source intake (USASpending API, SAM.gov, Senate LDA, DOD contract announcements RSS) before this profile can be relied on for strategy generation
  - Validate or kill the three named trap setups with system-measured data
  - Confirm the bounded-thesis framing is encoded as a guardrail in Hypothesis Generator prompts for defense names

## Confidence Notes

Seed profile, lower confidence than the ETF and mega-cap profiles because Mike's prior live experience on LMT is limited. High-confidence portions: government-revenue concentration, appropriations-cycle catalyst pattern, lower retail concentration. Medium-confidence: per-regime behavioral characterizations. Low-confidence (hypothesis-grade): the named trap setups and the magnitude of edge from primary-source reading — this is the category's central thesis and is unproven at the system level. Mike's review and lock-in required before any defense-name strategy is activated, and the bounded-thesis framing (contract intelligence, not war speculation) must be explicitly enforced upstream of strategy generation. Help Mike be right, not happy.
