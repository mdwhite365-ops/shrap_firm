# Universe: Discovery, Watch, and Active Tiers

**Document version:** 0.2 (draft)
**Last updated:** 2026-07-22
**Owner:** Mike White
**Status:** Draft — Tier 3 launch proposal awaiting Mike's lock-in (DQ-004, see `docs/status/decision-queue.md`)

> **TIERED MODEL — see ADR-0012 (2026-07-19).**
>
> The universe is not one list. It is three tiers — Discovery, Watch, and
> Active — each with its own membership rules, cost model, and owner. This
> document is organized around that model and summarizes it; ADR-0012 is the
> authority for the tier definitions and the reasoning behind them (including
> the RKLB/Iridium worked example) and should be read for the full argument.
> ADR-0010 (2026-05-31) established that the universe is merged from
> multiple approved sources rather than a single derived graph; ADR-0012
> refines that merged-universe idea into the three-tier structure below.
>
> **What is and isn't built:** this document describes the accepted model,
> not deployed reality — no Universe Curator service exists, Tier 2 has no
> state store, and the Pre-Trade Checker's Tier 3 membership check is a
> pending card.

## Purpose

The universe is the set of tickers Shrap is allowed to trade — Tier 3 in the
tiered model below. The vision document calls for a focused *tradeable* set
rather than a broad one, on the thesis that depth of understanding per
ticker compounds advantage in a way breadth does not. That focus applies to
Tier 3 only: Tier 1 (discovery) is deliberately market-wide, because a
funnel that can only discover names it already knows cannot discover
anything. This document defines the three tiers, then the Tier 3 selection
methodology and the proposed 50-name Tier 3 launch list.

A focused Tier 3 is not a constraint applied to make the system feasible. It
is a deliberate edge: every Tier 3 name has a maintained behavioral profile,
the regime classifier can be calibrated to how these specific names move,
the news intelligence agents can deeply understand each issuer's catalyst
calendar, and the structural-analysis department can read filings for the
full set without skimming. The cost is foregone opportunities outside Tier
3. The cost is deliberate; the curators (Mike, with the Universe Curator
Agent once built) treat it as the price of focus.

## The three tiers

Per ADR-0012, the universe is three sets, not one. Full reasoning lives in
the ADR; this section summarizes the membership rule, cost model, and owner
for each.

### Tier 1 — Discovery (market-wide)

- **Membership:** none — this tier is the market itself, everything the
  ingest sources see (Tech Watcher's EDGAR, USASpending, arXiv, and DOE
  newsroom feeds today).
- **Cost model:** bulk, cheap, local-classification only. No per-name state
  is maintained.
- **Tradeable:** no. This tier has no bearing on order eligibility.
- **Owner:** Tech Watcher and future ingest sources.

### Tier 2 — Watch (unbounded, evidence-gated)

- **Membership:** names elevated out of discovery by an approved mechanism —
  funnel candidate promotion, Forced-Proxy staging (ADR-0011), Structural
  Analysis findings, or Mike seeding. Every entry requires a recorded
  elevation event with source and evidence, plus a falsifier or expiry;
  watch entries that stop earning attention age out.
- **Cost model:** bounded per-name review cost. Unbounded in principle, kept
  small in practice by the expiry/falsifier requirement acting as a soft
  cap.
- **Tradeable:** no.
- **Owner:** Universe Curator.

### Tier 3 — Active (hard-capped, tradeable)

- **Membership:** the curated set with full per-name treatment — behavioral
  profile, regime calibration, Structural Analysis coverage, strategy
  eligibility. Initial cap: 50 names, per the launch Universe proposal
  below. Promotion from Tier 2 requires the profile to exist and Mike's
  approval; because the cap is hard, a promotion may force an eviction, and
  eviction criteria (profile decay, liquidity loss, thesis falsified) are
  recorded on the evicted name's profile.
- **Cost model:** full per-name cost — structural deep reads, behavioral
  profile maintenance, regime calibration, order flow.
- **Tradeable:** yes — the only tier that will be order-eligible once the
  Pre-Trade Checker's membership check ships (see "What is and isn't built"
  above).
- **Owner:** Universe Curator, with Mike approving all Tier 3 membership
  changes.

Tier filters bound cost, not curiosity: no agent may apply a Tier 3 filter
at ingest or discovery time. Filters apply only where per-name cost is
actually incurred.

## Tier-transition events

Tier transitions are the audit mechanism for this model — the firm needs to
be able to answer "why is this name tradeable" the same way it answers "why
did the system trade." Per ADR-0012, every transition publishes to the bus
under the ADR-0006 envelope convention as one of:

- `research.universe-watch-added`
- `research.universe-watch-expired`
- `research.universe-promoted`
- `research.universe-evicted`

Each event carries the ticker, source tier, destination tier, elevation
mechanism, and a reference to the evidence record.

## Tier 3 selection methodology

The methodology below governs Tier 3 — the tradeable, hard-capped set. Tier
1 and Tier 2 have no per-name curation and are not covered here.

The Tier 3 set is constructed to span five behavioral categories, each
chosen for a specific edge mechanism. The categories are not equal in size,
and the membership of each is chosen to support a specific set of strategy
archetypes.

**1. Liquid ETFs (regime expression and hedging).** Index and sector ETFs are how the system expresses macro-level views, hedges idiosyncratic risk, and trades regime transitions. ETF selection prioritizes deep liquidity, tight spreads, deep options markets, and meaningful regime-discrimination value. Sector ETFs are chosen so that the system can express cross-sector dispersion views (a key signature of several regimes).

**2. Mega-cap tech and growth leaders.** These names dominate the index, drive single-name risk exposure for the system, and exhibit deeply-studied behavioral patterns around earnings, guidance, and macro factor exposure. Most of Mike's prior trading data is in this set, which gives the system a starting calibration advantage. The Trap Detection subsystem also gets useful signal here, because retail flow concentrates in these names.

**3. High-retail-interest names (trap setups).** This is the subset where Mike's liquidation-sweep and trap-detection work has the highest expected value. Selection prioritizes names with dense retail option-flow, persistent social-media presence, history of squeeze and fade dynamics, and elevated short interest or persistent options skew. Some names overlap with mega-cap tech (NVDA, TSLA) and are tagged in both categories.

**4. Defense contractors (government-contract intelligence leverage).** The thesis is explicit and bounded: the leverage comes from primary-source reading — USASpending obligations, SAM.gov solicitations, congressional appropriations text, lobbying disclosures (Senate LDA), DOD contract announcements — not from "war drum" macro speculation. Names are chosen for: depth of government revenue exposure, observable contract-award cadence, public-filings discipline, options liquidity sufficient for sizing. This is the most concentrated category by edge-mechanism specificity.

**5. Liquid mid-caps (catalyst trading and dispersion).** Names in this band exhibit larger idiosyncratic moves around earnings and catalysts than mega-caps, while remaining liquid enough for sizing and options-strategy use. Selection prioritizes: average daily dollar volume above a threshold ($200M+ ADV), active options market, catalyst calendar density (earnings, product cycles, regulatory milestones), and behavioral pattern recognizability.

**6. Small crypto allocation.** Per the vision document, a "small crypto allocation" is part of the universe. The system implements this through spot-Bitcoin and spot-Ethereum ETFs (preferred over direct crypto for accounting, settlement, and custody simplicity) plus a small selection of crypto-equity proxies. The allocation is deliberately small in count because the strategy library here is shallow at sprint launch.

## How Tier 3 tickers are tagged

Every Tier 3 member has a profile under `docs/universe/<ticker>.md` following `_template.md`. Profiles capture:

- Sector / industry / market-cap band
- Liquidity profile (ADV, options open interest, spread tightness)
- Behavioral characteristics (vol regime, trend persistence, gap behavior)
- News sensitivity (which news types move it, how reliably)
- Catalyst calendar pattern (earnings, conferences, product cycles, regulatory dates)
- Retail vs institutional flow profile (where positioning is concentrated)
- Known trap setups (specific patterns observed historically)
- Historical strategy fit (what archetypes have worked, what has not)
- Per-regime behavior (how the name behaves across the four seed regimes)
- Maintenance notes (when last updated, by whom, what changed)

Profiles are starting points. The Universe Curator Agent — once operational — proposes refinements as evidence accumulates. Mike approves material changes. Seed profiles are explicitly draft-quality and labeled as such.

## Tier 3 launch proposal (awaiting lock-in — DQ-004 in docs/status/decision-queue.md)

This is a draft for Mike's review. The categories and counts are designed to support the strategy archetypes the system intends to run; specific tickers within each category are debatable. The Universe Curator Agent will propose substitutions over time, but the initial set must be locked in by Mike before the system trades against Tier 3 membership.

**Counts:** 12 ETFs + 8 mega-cap tech + 10 high-retail-interest + 6 defense + 10 liquid mid-caps + 4 crypto exposure = 50.

### Liquid ETFs — 12 names (regime expression and hedging)

| Ticker | Role |
|---|---|
| SPY | Broad index, the regime expression anchor |
| QQQ | Tech/growth proxy, dispersion partner to SPY |
| IWM | Small-cap proxy, key for breadth-thrust and crisis-recovery regimes |
| DIA | Industrial proxy, often diverges from SPY in wartime/stagflation |
| XLE | Energy sector, critical for stagflation and wartime regime expression |
| XLF | Financials, credit-sensitive, useful in crisis-recovery rotation |
| XLK | Tech sector cleaner exposure than QQQ |
| XLI | Industrials, includes meaningful defense weight |
| XLV | Healthcare, defensive in stagflation, growth in expansion |
| GLD | Gold, key hedge in stagflation and crisis regimes |
| TLT | Long Treasury, regime-discrimination signal and hedge |
| UUP | Dollar index proxy, useful for cross-asset regime signal |

### Mega-cap tech and growth leaders — 8 names

| Ticker | Notes |
|---|---|
| AAPL | Mike has prior trading history; index-weight giant |
| MSFT | Index-weight giant; rate-sensitive long-duration cash flows |
| GOOGL | Ad-cycle exposure, regulatory overhang |
| META | Ad-cycle, advertising-sentiment proxy |
| AMZN | Consumer + cloud blend, retail-discretionary read |
| NVDA | Mike has prior trading history; AI-narrative leader |
| AMD | Mike has prior trading history; semi cycle, NVDA-correlated but not identical |
| AVGO | Semi infrastructure, less retail attention than NVDA, often a cleaner pure-cycle read |

### High-retail-interest — 10 names (trap setup priority)

| Ticker | Notes |
|---|---|
| TSLA | Mike has prior trading history; archetypal high-retail-interest name |
| PLTR | Persistent retail / meme adjacency, dense options activity |
| GME | Meme original; persistent squeeze risk and fade-pattern density |
| AMC | Meme original; retail-flow proxy |
| COIN | Crypto-equity proxy with high retail interest |
| RIVN | EV retail-favorite, persistent short interest, catalyst-driven |
| MSTR | Bitcoin-proxy equity with retail amplification |
| SOFI | Fintech retail-favorite, options-volume-rich |
| HOOD | Retail-flow infrastructure name; reflexive exposure to its own user base |
| NIO | Volatile EV name with episodic retail attention spikes |

### Defense contractors — 6 names (government-contract intelligence)

| Ticker | Notes |
|---|---|
| LMT | Largest defense prime; F-35 program is the cleanest contract-cycle anchor |
| RTX | Raytheon-Pratt-Collins, broad portfolio including missiles and engines |
| NOC | Northrop Grumman, B-21 and space programs |
| GD | General Dynamics, includes submarines (Columbia program) and combat vehicles |
| LHX | L3Harris, communications and electronic warfare focus |
| LDOS | Leidos, IT services to DOD/IC; cleaner gov-contract pure-play |

### Liquid mid-caps — 10 names (catalyst trading and dispersion)

| Ticker | Notes |
|---|---|
| MU | Memory cycle, earnings-driven moves |
| MRVL | Data-center semi, NVDA-adjacent but distinct cycle |
| CRWD | Cybersecurity, quarterly cadence and product-cycle |
| NET | Cloudflare, edge infrastructure, cycle-sensitive |
| SNOW | Data infrastructure, consumption-revenue model, distinctive earnings dynamics |
| DKNG | Gambling/sports-betting, state-by-state catalyst calendar |
| ROKU | Streaming/ads, retail-attention overlap |
| AFRM | BNPL, credit-cycle-sensitive, retail interest |
| U | Unity, gaming/3D platform, volatile catalysts |
| PYPL | Payments, large-cap-adjacent but trades with mid-cap dispersion characteristics |

### Crypto exposure — 4 names (small allocation)

| Ticker | Notes |
|---|---|
| IBIT | Spot Bitcoin ETF, cleanest BTC exposure within equity wrappers |
| ETHA | Spot Ethereum ETF; ETH-specific regime exposure |
| MARA | Bitcoin miner, leveraged BTC exposure with operational overlay |
| RIOT | Bitcoin miner, alternate leverage profile to MARA |

### Tickers that overlap categories

Several names sit in multiple buckets functionally; the table-level assignment is for accounting clarity, but their profiles should reflect dual-category behavior:

- NVDA: mega-cap tech + high-retail-interest
- TSLA: mega-cap-adjacent + high-retail-interest (kept in retail category for trap-detection priority)
- MSTR: high-retail-interest + crypto-proxy (kept in retail category; crypto exposure is via ETFs/miners)
- COIN: high-retail-interest + crypto-proxy

### What the Tier 3 proposal is and isn't

This is a draft proposed by a subagent based on the vision document, Mike's prior trading history, and standard market-structure reasoning. It is not:

- Lock-in. Mike must review and approve before the system trades against it (DQ-004).
- Optimized. No formal screening was applied; selection was reasoned, not optimized over a screener.
- Permanent. The Universe Curator Agent will propose substitutions as evidence accumulates, and Tier 2 promotions/evictions will move names in and out over time per the tier model above. Names that fail to provide trade frequency, behave unrecognizably, or lose liquidity will be candidates for replacement.

Items where Mike's review is specifically requested:

1. **Defense bucket sizing.** 6 names is a defensible count given concentration and contract-data overlap, but Mike may prefer 8 (adding KTOS for drones/space and BA for the commercial/defense split) or 4 (cutting LDOS and LHX as less primary-source-readable).
2. **High-retail-interest names.** GME and AMC have meme-era staying power but may have lost the persistent option-flow density that justified their inclusion. Mike's call: keep both, swap one, or cut both?
3. **Crypto exposure design.** The 4-name allocation uses ETFs + miners. Mike may prefer direct crypto pairs (BTC, ETH) routed through a separate venue, which would change the universe definition.
4. **Mid-cap selection.** The 10 mid-cap names are reasonable choices but not unique; Mike may have specific names he wants in (e.g., AMD-cycle adjacencies, biotech catalysts, retail names he has prior signal on) that should displace current proposals.
5. **Sector ETF count.** 12 ETFs is generous for a 50-name budget. If Mike wants more single-name slots, cutting XLU/UUP and one other could free three slots.

## Maintenance cadence

Per ADR-0012, the Universe Curator owns Tier 2 and Tier 3 state and publishes every transition as one of the tier-transition events above. The Tier 3 launch list below is the starting point once locked in; further Tier 3 membership changes come only through Tier 2 promotion (or, rarely, a direct Mike-approved add) and are always Mike-approved. Material change cadence:

- **Reviewed quarterly** by the Universe Curator Agent for liquidity, behavior changes, and category fit.
- **Tier 3 changes proposed** as standalone promotion/eviction events with reasoning attached. Mike approves each.
- **Profile updates** are higher-cadence (weekly, when new data warrants); these do not require Mike's approval unless they change strategy-relevant fields.
- **Category structure changes** are rare and require an ADR plus Mike's approval.

Tier 3 names age out for specific reasons: lost liquidity (ADV drops below threshold), structural change (acquisition, delisting), or behavioral drift (name stops trading like the category it was placed in). The Universe Curator surfaces these candidates with evidence; Mike makes the call.

## Honest limits

The 50-name Tier 3 choice is not optimal in any provable sense. It is a working hypothesis: that focused depth beats broad shallow coverage for a system at Shrap's scale — a hypothesis Tier 1's market-wide discovery does not have to share, which is why the tiers are split rather than uniformly bounded. If the trade frequency this list supports proves insufficient — too few setups, too many days without action — Tier 3 will need to expand (subject to the cap, which is itself a decision Mike can revisit). If the trade frequency proves excessive — too many concurrent signals, too much correlation — Tier 3 may need to contract or be rebalanced. These adjustments are expected; the Curator Agent's primary job is to surface evidence for them.

The seed profiles for SPY, QQQ, TSLA, NVDA, AAPL, and LMT are starting points written by a subagent. They will be wrong in places. They are explicitly draft-quality and will be refined by the Universe Curator Agent as the system accumulates evidence. Mike should treat the seeds as scaffolding, not as authoritative documents.
