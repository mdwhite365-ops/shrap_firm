# Bottleneck Archetypes

**Version:** 0.1 (draft)
**Date:** 2026-05-30
**Owner:** Mike White
**Status:** Draft — living document
**Serves:** ADR-0007. This file is the reference vocabulary the
Bottleneck Scout agent uses at Step 3 of the funnel. Referenced
explicitly in `docs/agents/research/bottleneck-scout.md` §Inputs.

## Purpose

Taxonomy of bottleneck patterns the Bottleneck Scout is permitted to
detect. The Scout cannot identify bottlenecks outside the archetype
list — novel constraint types require Mike's manual archetype
expansion (bottleneck-scout.md §Open questions). This file IS that
archetype list.

A bottleneck only counts when it is on a critical-path layer of a
*currently promoted* world-changer infrastructure graph. Bottlenecks
in general supply chains, unattached to a promoted world-changer, are
out of scope. This is what stops the agent from drowning in
supply-chain news.

The trade is the **forced substitute**: the technology, vendor, or
layer that necessarily replaces the saturating one. Whether the
forced substitute is investable (public ticker, liquid, tradable) is a
separate filter applied downstream.

## Archetypes

### (a) Physical-limit walls

A layer is hitting a boundary set by physics — signal integrity,
thermal density, atomic-scale geometry, channel capacity, energy
density. The wall does not yield to capex; it yields only to a
substrate or architecture change.

**Detection signals:**
- Engineering-conference talks (Hot Chips, OFC, ISSCC, SC, OCP) cite
  the named limit as a binding design constraint, not a roadmap goal.
- Industry roadmap bodies (IRDS, OIF, ASHRAE) publish the limit and
  the proposed substitute side-by-side.
- arXiv preprints in cs.AR / cs.NI / cond-mat cluster on alternative
  architectures around the same limit.
- Supplier patent activity pivots CPC class toward the substitute
  technology.

**Canonical example — WORKED, BACKWARDS-TEST CASE [confirm with Mike]:**
- **World-changer:** NVIDIA AI compute buildout.
- **Layer:** server / rack interconnect.
- **Named limit:** 224 Gbps PAM4 copper signal-integrity reach. At
  224G lane rates, passive copper loss and crosstalk push usable reach
  below modern AI rack and inter-rack distance requirements. Physical,
  Shannon-channel-adjacent; documented in IEEE 802.3 and OIF working
  group material as of 2024.
- **Forced substitute:** co-packaged optics (CPO) and linear pluggable
  optics (LPO); silicon photonics integrated into switch and
  accelerator silicon. Public tickers: LITE, COHR, FN, AOI for
  components; ANET, CSCO, NVDA for integration.
- **Time-to-bind:** ~1-4 quarters from Aug 2024 vintage evidence.
- **Kill criteria:** OFC 2025+ demonstrates 224G PAM4 copper at >=2m
  reach with acceptable BER margins; hyperscaler AI rack designs
  publish majority-copper interconnect at 224G; optical-component
  datacom revenue declines YoY for two consecutive quarters.

**Other live examples to develop  [MIKE INPUT REQUIRED — confirm fit]:**
- HBM bandwidth per stack (compute-side memory wall behind interconnect).
- Atomic-scale EUV lithography limits as transistor scaling slows.
- Rack thermal density (~130-150 kW) as the wall air cooling cannot
  cross — forced substitute: liquid / immersion cooling (VRT).

**Time-to-bind heuristic for physical walls:** when industry roadmaps
*name* the substitute in their next-generation release, the wall is
~4-8 quarters from binding in production. Heuristic only.

### (b) Economic-saturation walls

A layer is not physically blocked but has hit an economic constraint:
capex inflection (the next doubling costs disproportionately more),
lead-time blowout (orders extend beyond the world-changer's planning
horizon), or yield ceiling (incremental yield gains stop paying for
themselves).

**Detection signals:**
- Capex per unit of capacity ratchets up step-wise in earnings
  disclosures.
- Lead-time citations in transcripts cross documented thresholds (e.g.
  CoWoS >40 weeks; large power transformers ~100+ weeks).
- Yield commentary plateaus at the same number for multiple quarters
  in a row.
- "Allocated" / "supply constrained" language becomes routine, not
  exceptional.

**Canonical example  [MIKE CONFIRM]:** CoWoS-L advanced packaging
capacity at TSMC, FY24-FY27. Forced substitute candidates: glass
substrates, alternative 2.5D packaging providers, intra-die HBM-
integration approaches that reduce CoWoS dependency.

**Other examples:**
- HBM stack assembly capacity at Hynix/Micron/Samsung.
- Large power transformer lead times (100+ weeks) as the wall behind
  AI data-center power buildout.
- Fab capex per node-shrink (the GAA / 2nm-class capex step).

**Time-to-bind heuristic:** economic walls bind faster than physical
ones — when a single supplier's lead time crosses 40 weeks in two
consecutive quarterly disclosures, treat as `near` (1-4 quarters).
Heuristic only.

### (c) Supply-chain choke points

A layer routes through a single vendor, single geography, or single
process step that the rest of the chain cannot route around quickly.
Distinct from (b): a choke point may not be saturated *yet*, but
asymmetric dependency means saturation, disruption, or pricing
power will eventually bind.

**Detection signals:**
- 10-K risk-factor disclosures name a single supplier or geography.
- Hyperscaler / OEM second-source qualification programs accelerate.
- Patent licensing disputes at the choke point.
- Geopolitical positioning around the choke geography.

**Canonical example  [MIKE CONFIRM]:** ASML EUV monopoly on leading-
edge lithography. The forced substitute is not a competing litho
vendor (there isn't one); it's design approaches that reduce
litho-criticality (chiplet decomposition, advanced packaging
substitution).

**Other examples:**
- Behind-the-meter power dependency on a small set of nuclear and
  gas-turbine operators (CEG, VST, TLN, GEV).
- Rare-earth processing geographic concentration.
- Peptide CDMO capacity for GLP-1 manufacturing.
- Photoresist materials from a small Japanese supplier set.

**Time-to-bind heuristic:** choke points bind on exogenous shocks
(geopolitics, fire, strike) rather than on schedule. The Scout flags
them as `forming` and revisits on event triggers; promoting to `near`
requires a documented second-source qualification timeline.

### (d) Regulatory-induced bottlenecks

A regulation, export control, environmental permit, or licensing
regime constrains a layer's expansion independent of physics or
economics.

**Detection signals:**
- Export-control rule changes (BIS, EAR, OFAC) explicitly name a
  technology layer.
- Permitting timelines on the layer's expansion (interconnection
  queues, NEPA reviews, NRC licensing) extend past the world-changer's
  planning horizon.
- Litigation activity around facility siting.
- Tariff or content-rules changes that reroute supply.

**Canonical example  [MIKE CONFIRM]:** US export controls on advanced
AI accelerators to China (Oct 2022 + amendments). Forced substitute:
domestic-China alternatives (Huawei Ascend etc.) for the China market;
no direct trade for the firm but graph-relevant for
revenue-attribution.

**Other examples:**
- Grid interconnection queues for hyperscaler data centers (forced
  substitute: behind-the-meter generation).
- NRC licensing duration for new nuclear (forced substitute: SMRs IF
  licensing path differentiates, otherwise existing-plant relicensing).
- Environmental permitting for new fabs and mines.

**Time-to-bind heuristic:** regulatory bottlenecks bind on
rule-publication date or on injunction date, which the Scout can
read directly. When a proposed rule enters comment period AND the
affected layer is critical-path on a promoted graph, flag `near`.

## Cross-archetype rules

1. **Critical-path requirement.** No archetype counts unless the
   constrained layer is critical-path on a promoted world-changer
   graph. The Mapper owns the critical-path designation.
2. **Triangulation.** Two independent source classes required, with at
   least one engineering-trade source and one financial source
   (bottleneck-scout.md §Processing step 5).
3. **Kill criteria are mandatory and observable.** Named metric, named
   threshold, named source. No "we'll see how it plays out."
4. **Forced substitute must include public tickers OR be marked
   non-actionable.** A bottleneck whose forced substitute is entirely
   private is stored but not surfaced for trade.
5. **Time-to-bind is ordinal, not calendar.** `forming` / `near` /
   `binding-now` / `solved-or-deferred`. The Scout is explicitly
   forbidden from inventing dates it cannot justify
   (bottleneck-scout.md §Open questions).

## Mike-input-required sections

- All "MIKE CONFIRM" canonical-example tags above.
- Whether **yield-ceiling** deserves its own archetype or stays
  inside (b).
- Whether **standards-fragmentation** (multiple incompatible standards
  competing, e.g. early CXL vs proprietary) deserves an archetype or
  is just a sub-pattern of (c).
- Time-to-bind heuristics are first-guesses; calibrate against the
  forward-test ledger in `calibration.md` after Month 6.

## Open

- Cross-archetype scoring: a bottleneck that is simultaneously a
  physical wall AND a regulatory bottleneck (e.g. EUV + export
  controls) should rank higher than a single-archetype hit, but the
  scoring formula is unspecified. Bottleneck-scout.md §Open questions
  flags this; resolve in this file when the formula lands.
