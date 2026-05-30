# World-Changer Archetypes

**Version:** 0.1 (draft)
**Date:** 2026-05-30
**Owner:** Mike White
**Status:** Draft — living document
**Serves:** ADR-0007 (Research Thesis — World-Changers, Infrastructure Graphs,
Bottleneck Scouting). This file is the reference vocabulary the Tech Watcher
agent uses at Step 1 of the funnel.

## Purpose

Taxonomy of the small number of credible patterns by which a technology
or company has actually changed how the general population lives, works,
or consumes — and the larger number of patterns that *looked* the same
and didn't. The Tech Watcher promotes candidates only if they fit a
recognized archetype AND survive the archetype's known impostor list
AND have a written kill criterion.

This is not a prediction list. It is the recognition grammar. Whether
any specific candidate is currently live in May 2026 is for the funnel
to decide, not this file. The LIVE/HISTORICAL flags below mark which
*archetype slots* are currently being scanned, not which candidates
have survived promotion.

Honest base rate: most candidates that fit an archetype still die.
Promotion is necessary, not sufficient. The 80%+ kill-rate target from
ADR-0007 applies inside each archetype, not across them.

## Archetypes

### (a) Compute-substrate revolutions  [LIVE]

A new computational substrate (architecture, instruction set, accelerator
class, or programming model) becomes the platform a generation of
applications is built on, and incumbents in the old substrate either
adapt or are bypassed.

**Signature signals to watch:**
- A workload class shifts from general-purpose to specialized silicon
  and the specialized silicon's TCO advantage is >5x not 1.5x.
- A software moat forms around the new substrate (CUDA-shaped: tooling
  + libraries + community + hiring pool) that incumbents cannot
  replicate by buying silicon alone.
- Hyperscaler capex disclosures redirect toward the substrate at
  multi-quarter sustained rates, not single-quarter spikes.
- Application revenue (not just infrastructure revenue) starts
  attributing to the substrate.

**Canonical case:** NVIDIA AI compute + CUDA, ~2022-onward. Earlier:
x86 + Windows in PCs; ARM in mobile.

**Known impostors:**
- "GPU compute for everything" circa 2010 (right substrate, wrong
  timing — the application demand wasn't there).
- Quantum computing as a general-purpose substrate (perpetual five
  years out since the 2010s).
- Neuromorphic chips as a mainstream alternative.
- Crypto-mining ASIC firms reframed as "AI compute."

**Kill criteria template:**
- Substrate-specific software moat erodes (open replacement reaches
  feature parity AND production adoption at hyperscalers).
- TCO advantage compresses to <2x as competing substrates close.
- Hyperscaler capex toward the substrate decelerates for >=2
  consecutive quarters with explicit guidance attribution.

### (b) Biological-mechanism unlocks  [LIVE]

A specific biological mechanism (receptor, pathway, modality) becomes
clinically and commercially viable at scale and produces a step-change
in a large-population disease.

**Signature signals to watch:**
- Mechanism validated in multiple independent Phase 3 trials, not one.
- Manufacturing scale demonstrated (the molecule can actually be made
  in tonnage at acceptable cost — GLP-1 peptide CDMO capacity is the
  current canonical bottleneck).
- Payer coverage expands beyond on-label to adjacent indications.
- Real-world evidence accumulates from registries or claims data.

**Canonical cases:** GLP-1 agonists for obesity/metabolic disease;
CAR-T for hematologic malignancies; mRNA platforms (COVID + onward).

**Known impostors:**
- Theranos (mechanism never validated; pure fraud, but the early
  signals looked like a real platform unlock).
- Most Alzheimer's amyloid plays (mechanism arguably real, clinical
  benefit marginal-to-absent, commercial outcome poor).
- Gene therapies that worked in trial but failed on durability or
  manufacturing economics.
- Stem-cell hype cycles, 2005-2015.

**Kill criteria template:**
- Phase 3 readout fails primary endpoint OR safety signal forces a
  black-box / withdrawal.
- Manufacturing cost curve fails to bend below payer willingness-to-pay.
- A simpler mechanism (oral small molecule vs. injectable, e.g.) takes
  the market.

### (c) Cost-curve crossings  [LIVE]

A technology's unit cost crosses a threshold where adoption tips from
subsidized/niche to unsubsidized/mass, and the crossing is durable
(not a temporary subsidy or supply-chain anomaly).

**Signature signals to watch:**
- Cost per unit (per watt, per kg-to-orbit, per kWh, per FLOP) declines
  on a learning-curve slope consistent across multiple producers.
- Unsubsidized adoption appears in markets without policy support.
- Adjacent infrastructure (grids, ground stations, charging networks)
  starts being built ahead of demand.

**Canonical cases:** Solar PV crossing ~$1/W and continuing down;
SpaceX reusable launch crossing legacy $/kg-to-LEO; lithium-iron-
phosphate (LFP) battery chemistry crossing into mass EV and storage.

**Known impostors:**
- "Hydrogen economy by 2025" — green hydrogen $/kg cost curve never
  crossed, blue hydrogen depends on persistent subsidy and CCS that
  hasn't scaled.
- Carbon capture at $/ton needed for unsubsidized deployment.
- Fuel cells for passenger vehicles.
- Synthetic biology consumer products (Solazyme et al., ~2014).

**Kill criteria template:**
- Cost curve flattens for >=4 consecutive quarters with no path back
  on slope.
- A competing technology crosses the same threshold first and locks
  in installed base / standards.
- Subsidy withdrawal exposes that adoption was policy-dependent.

### (d) Physical-realization breakthroughs  [HISTORICAL — no current promoted candidate]

A long-theorized physical capability (fusion ignition, room-temperature
superconductivity, useful quantum advantage, true autonomy) becomes
real at lab scale with a credible path to engineering scale.

**Signature signals to watch:**
- Independent replication of the result by a different group with a
  different apparatus.
- Engineering metrics (Q > 1, T_c above accessible temperatures, gate
  fidelity at fault-tolerance thresholds) cross the published
  theoretical floors.
- Capital and personnel flows shift from theory groups to engineering
  groups.

**Canonical cases:** NIF fusion ignition (Dec 2022) — lab milestone,
no commercial path within a decade; transistor invention; lasers;
GPS-as-utility.

**Known impostors:**
- LK-99 room-temp superconductor, 2023 (failed replication within
  weeks).
- Cold fusion, 1989.
- "Full self-driving next year" (2016-onward), repeated.
- Most fusion-startup timelines.

**Kill criteria template:**
- Replication fails OR primary group retracts.
- Engineering-scale metrics plateau before crossing commercial-
  viability thresholds.
- Decade rolls over without a deployed unit producing the claimed
  output economically.

**[MIKE INPUT REQUIRED]** Is this archetype LIVE or HISTORICAL as of
May 2026? Default-classified HISTORICAL because no current promoted
world-changer fits, but the archetype itself should remain in scope
for Tech Watcher scanning. Flag any candidates Mike is tracking
informally that should move this to LIVE.

### (e) Platform shifts  [LIVE]

A new computing or interaction platform emerges and a generation of
applications is rebuilt natively on it; incumbents who don't make the
jump lose distribution.

**Signature signals to watch:**
- A new primary interaction surface gets >100M daily active users
  inside ~24 months.
- Developer-tool ecosystem and revenue-share economics get standardized
  on the platform.
- Native applications dominate retention/engagement vs. ports of older
  platforms.

**Canonical cases:** Mobile internet (iPhone + Android, 2007-2012);
cloud (AWS, 2006-onward); SaaS replacing on-prem; possibly AI agents
as a platform now.

**Known impostors:**
- Metaverse (Meta's bet, 2021-2023; no native application killer hit
  scale).
- Smart-speaker as a primary commerce surface.
- VR/AR as a mass platform (perpetual, ~2014-onward).
- Wearables-as-a-platform beyond fitness.

**Kill criteria template:**
- DAU growth on the new surface stalls before generational scale.
- Native developer revenue share fails to mature; ports remain
  dominant.
- A competing surface takes the attention budget.

## Cross-archetype rules

1. **Survivorship-bias prior.** Promotion of any candidate MUST include
   the impostor list for its archetype in the thesis card. ADR-0007
   §Survivorship-bias surveillance is binding.
2. **One archetype per promotion.** If a candidate fits multiple
   archetypes, pick the dominant one and document why — multi-fit is a
   signal of vague thesis, not strength.
3. **Kill criteria are mandatory and observable.** No
   "vibes-deteriorating" criteria. Named metric, named threshold,
   named source.
4. **LIVE/HISTORICAL is reviewed quarterly by Mike.** This file's
   LIVE/HISTORICAL markings are not auto-maintained.

## Mike-input-required sections

- (d) LIVE vs HISTORICAL classification confirmation.
- Whether (e) AI agents specifically belongs as its own subtype or as
  an instance of (a).
- Adding new archetypes Mike encounters (the Bottleneck Scout's
  archetype list and this one are both manually expanded — there is
  no auto-discovery of new world-changer shapes).

## Open

- Should this file include an explicit archetype for **regulatory-
  unlock world-changers** (Dodd-Frank-style structural rewrites,
  spectrum re-auctions, drug-scheduling changes)? Mike defer.
- Should the impostor lists be moved to `calibration.md` as the
  living kill-graveyard, with this file holding only the canonical
  template? Probably yes once `calibration.md` has structure. Mike
  defer.
