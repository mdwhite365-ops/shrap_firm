# Universe

**Document version:** 0.1 (draft)
**Last updated:** 2026-05-29
**Owner:** Mike White
**Status:** Draft — proposed 50-name list requires Mike's review and lock-in

> **SUPERSEDED IN PART — see ADR-0007 (2026-05-30).**
>
> As of ADR-0007, the universe is no longer a hand-curated 50-name locked
> list. It is derived continuously from the Infrastructure Mapper's active
> graphs. The list below is preserved as the **SEED GRAPH** — the initial
> universe extracted from currently-obvious world-changers (NVIDIA AI
> compute, GLP-1 obesity drugs, reusable launch, energy infrastructure for
> AI training). The Universe Curator Agent
> (see `docs/agents/research/universe-curator.md`) maintains this list
> going forward based on Infrastructure Mapper outputs, not on the fixed
> category quotas described below. The selection methodology, category
> tags, and per-ticker profile schema below remain valid as the seed-graph
> documentation and as advisory tagging for new graph nodes; they are no
> longer treated as locked quotas.

## Purpose

The universe is the set of tickers Shrap is allowed to trade. The vision document calls for "50 deliberately-chosen stocks" rather than a broad universe, on the thesis that depth of understanding per ticker compounds advantage in a way that breadth does not. This document defines the selection methodology and proposes a concrete 50-name list as a starting point.

A focused universe is not a constraint applied to make the system feasible. It is a deliberate edge: every name has a maintained behavioral profile, the regime classifier can be calibrated to how these specific names move, the news intelligence agents can deeply understand each issuer's catalyst calendar, and the structural-analysis department can read filings for the full set without skimming. The cost is foregone opportunities outside the universe. The cost is deliberate; the curators (Mike, with the Universe Curator Agent) treat it as the price of focus.

## Selection methodology

The universe is constructed to span five behavioral categories, each chosen for a specific edge mechanism. The categories are not equal in size, and the membership of each is chosen to support a specific set of strategy archetypes.

**1. Liquid ETFs (regime expression and hedging).** Index and sector ETFs are how the system expresses macro-level views, hedges idiosyncratic risk, and trades regime transitions. ETF selection prioritizes deep liquidity, tight spreads, deep options markets, and meaningful regime-discrimination value. Sector ETFs are chosen so that the system can express cross-sector dispersion views (a key signature of several regimes).

**2. Mega-cap tech and growth leaders.** These names dominate the index, drive single-name risk exposure for the system, and exhibit deeply-studied behavioral patterns around earnings, guidance, and macro factor exposure. Most of Mike's prior trading data is in this set, which gives the system a starting calibration advantage. The Trap Detection subsystem also gets useful signal here, because retail flow concentrates in these names.

**3. High-retail-interest names (trap setups).** This is the subset where Mike's liquidation-sweep and trap-detection work has the highest expected value. Selection prioritizes names with dense retail option-flow, persistent social-media presence, history of squeeze and fade dynamics, and elevated short interest or persistent options skew. Some names overlap with mega-cap tech (NVDA, TSLA) and are tagged in both categories.

**4. Defense contractors (government-contract intelligence leverage).** The thesis is explicit and bounded: the leverage comes from primary-source reading — USASpending obligations, SAM.gov solicitations, congressional appropriations text, lobbying disclosures (Senate LDA), DOD contract announcements — not from "war drum" macro speculation. Names are chosen for: depth of government revenue exposure, observable contract-award cadence, public-filings discipline, options liquidity sufficient for sizing. This is the most concentrated category by edge-mechanism specificity.

**5. Liquid mid-caps (catalyst trading and dispersion).** Names in this band exhibit larger idiosyncratic moves around earnings and catalysts than mega-caps, while remaining liquid enough for sizing and options-strategy use. Selection prioritizes: average daily dollar volume above a threshold ($200M+ ADV), active options market, catalyst calendar density (earnings, product cycles, regulatory milestones), and behavioral pattern recognizability.

**6. Small crypto allocation.** Per the vision document, a "small crypto allocation" is part of the universe. The system implements this through spot-Bitcoin and spot-Ethereum ETFs (preferred over direct crypto for accounting, settlement, and custody simplicity) plus a small selection of crypto-equity proxies. The allocation is deliberately small in count because the strategy library here is shallow at sprint launch.

## How tickers are tagged

Every universe member has a profile under `docs/universe/<ticker>.md` following `_template.md`. Profiles capture:

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

## The proposed 50-name list

This is a draft for Mike's review. The categories and counts are designed to support the strategy archetypes the system intends to run; specific tickers within each category are debatable. The Universe Curator Agent will propose substitutions over time, but the initial set must be locked in by Mike before the system goes live on paper.

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

## What this list is and isn't

This is a draft proposed by a subagent based on the vision document, Mike's prior trading history, and standard market-structure reasoning. It is not:

- Lock-in. Mike must review and approve before the system goes live.
- Optimized. No formal screening was applied; selection was reasoned, not optimized over a screener.
- Permanent. The Universe Curator Agent will propose substitutions as evidence accumulates. Names that fail to provide trade frequency, behave unrecognizably, or lose liquidity will be candidates for replacement.

Items where Mike's review is specifically requested:

1. **Defense bucket sizing.** 6 names is a defensible count given concentration and contract-data overlap, but Mike may prefer 8 (adding KTOS for drones/space and BA for the commercial/defense split) or 4 (cutting LDOS and LHX as less primary-source-readable).
2. **High-retail-interest names.** GME and AMC have meme-era staying power but may have lost the persistent option-flow density that justified their inclusion. Mike's call: keep both, swap one, or cut both?
3. **Crypto exposure design.** The 4-name allocation uses ETFs + miners. Mike may prefer direct crypto pairs (BTC, ETH) routed through a separate venue, which would change the universe definition.
4. **Mid-cap selection.** The 10 mid-cap names are reasonable choices but not unique; Mike may have specific names he wants in (e.g., AMD-cycle adjacencies, biotech catalysts, retail names he has prior signal on) that should displace current proposals.
5. **Sector ETF count.** 12 ETFs is generous for a 50-name budget. If Mike wants more single-name slots, cutting XLU/UUP and one other could free three slots.

## Maintenance cadence

Per the vision document, the Universe Curator Agent maintains the list. Material change cadence:

- **Reviewed quarterly** by the Universe Curator Agent for liquidity, behavior changes, and category fit.
- **Substitutions proposed** as standalone PRs with reasoning. Mike approves each.
- **Profile updates** are higher-cadence (weekly, when new data warrants); these do not require Mike's approval unless they change strategy-relevant fields.
- **Category structure changes** are rare and require an ADR plus Mike's approval.

Names age out for specific reasons: lost liquidity (ADV drops below threshold), structural change (acquisition, delisting), or behavioral drift (name stops trading like the category it was placed in). The Universe Curator surfaces these candidates with evidence; Mike makes the call.

## Honest limits

The 50-name choice is not optimal in any provable sense. It is a working hypothesis: that focused depth beats broad shallow coverage for a system at Shrap's scale. If the trade frequency this list supports proves insufficient — too few setups, too many days without action — the universe will need to expand. If the trade frequency proves excessive — too many concurrent signals, too much correlation — the universe may need to contract or be rebalanced. These adjustments are expected; the Curator Agent's primary job is to surface evidence for them.

The seed profiles for SPY, QQQ, TSLA, NVDA, AAPL, and LMT are starting points written by a subagent. They will be wrong in places. They are explicitly draft-quality and will be refined by the Universe Curator Agent as the system accumulates evidence. Mike should treat the seeds as scaffolding, not as authoritative documents.
