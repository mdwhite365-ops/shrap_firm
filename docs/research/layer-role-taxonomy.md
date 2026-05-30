# Layer Role Taxonomy

**Version:** 0.1 (draft)
**Date:** 2026-05-30
**Owner:** Mike White
**Status:** Draft — living document
**Serves:** ADR-0007. The Infrastructure Mapper's node-role vocabulary
at Step 2 of the funnel. Every node in every promoted world-changer
graph is tagged with one of these roles (or a manually added one Mike
approves).

## Purpose

A graph node's *role* is the question the Bottleneck Scout asks
about it: "could this layer be the saturating one?" Role determines
which archetype scan (see `bottleneck-archetypes.md`) applies and
which evidence sources are read. Roles are also the units the
Cisco-1999 lesson is taught in: being right about the *world-changer*
and wrong about the *role that captures the spend* is the canonical
failure mode this firm is built to avoid.

## Cisco-1999 critical-path test

For every role assignment on a promoted graph, the Mapper must
answer:

1. **Is the layer on the revenue-attribution path of the world-changer
   thesis?** If the world-changer wins but this layer's revenue
   doesn't grow, the role is not critical-path.
2. **Is the layer substitutable on the world-changer's planning
   horizon?** If a credible substitute exists at scale within 1-2
   years, the role is substitutable and trades against it carry
   timing risk.
3. **Does the layer have pricing power?** A layer can be critical-path
   and still be a poor trade if the buyer side has monopsony
   leverage. Router commodity vendors in the late-90s build were
   critical-path AND substitutable AND price-disciplined by buyers
   — three strikes.

A role passes the critical-path test only if it is on the revenue
path AND non-trivially non-substitutable AND has demonstrable pricing
power (gross-margin trajectory, lead-time discipline, sole-source
positioning). All three.

## Roles

### fab
- **Description:** Wafer fabrication at leading or trailing edge.
- **Typical companies:** TSMC, Samsung Foundry, Intel Foundry, GF,
  SMIC.
- **Critical-path test:** Almost always critical-path on
  silicon-bearing world-changers; substitutability is geographic
  (foundry diversification) more than technical at leading edge.
  Pricing power is real at leading edge, weak at trailing.

### litho
- **Description:** Lithography equipment — the photon source and
  scanner that prints transistors.
- **Typical companies:** ASML (EUV monopoly), Canon, Nikon (DUV
  competition only).
- **Critical-path test:** Trivially critical-path at leading edge.
  Substitutable only via design-side workarounds (chiplets,
  packaging). Pricing power textbook.

### materials
- **Description:** Specialty chemicals, photoresists, gases, slurries,
  high-purity feedstocks.
- **Typical companies:** Shin-Etsu, JSR, Tokyo Ohka, Entegris, Linde,
  Air Liquide.
- **Critical-path test:** Frequently critical-path AND choke-point
  (see bottleneck-archetypes (c)). Substitutability is process-locked:
  qualification takes years. Pricing power varies by chemistry.

### packaging
- **Description:** Advanced packaging — CoWoS, FOPLP, glass substrates,
  hybrid bonding.
- **Typical companies:** TSMC (CoWoS), ASE, Amkor, Powertech; equipment
  from Besi, ASM Pacific, Disco.
- **Critical-path test:** Increasingly critical-path as
  Moore's-law-substitute. Currently the canonical example of (b)
  economic-saturation. Substitutability under active research (glass
  substrates).

### memory
- **Description:** DRAM, HBM, NAND, emerging non-volatile.
- **Typical companies:** SK Hynix, Micron, Samsung, Kioxia, WD/SNDK.
- **Critical-path test:** HBM is critical-path on AI-compute graphs;
  substitutable across the three HBM suppliers but not by other
  memory types. Pricing power oscillates with cycle.

### interconnect
- **Description:** The fabric that moves data inside racks, between
  racks, and between sites. Subdivides into networking-silicon and
  networking-optical (below).
- **Critical-path test:** Critical-path on AI-compute graphs whenever
  the workload is distributed (i.e. always at scale).

### networking-silicon
- **Description:** Switch ASICs, NICs, DPUs, in-silicon photonics.
- **Typical companies:** Broadcom (Tomahawk/Jericho), Marvell, NVIDIA
  (Mellanox), Cisco Silicon One, Arista (white-box partners).
- **Critical-path test:** Critical-path; partially substitutable across
  vendors; pricing power held by Broadcom in merchant silicon.

### networking-optical
- **Description:** Transceivers, CPO modules, LPO modules, optical
  components.
- **Typical companies:** LITE, COHR, FN, AOI, INFN, MRVL.
- **Critical-path test:** Becomes critical-path when (a) interconnect
  bottleneck binds. The Sept 2024 LITE case is the canonical example.
  Substitutability across module vendors moderate; component-level
  (lasers, EMLs) more concentrated.

### power-gen
- **Description:** Generation assets — nuclear, gas-turbine, renewables
  with firming.
- **Typical companies:** CEG, VST, TLN (nuclear); GEV, MIR (gas
  turbines); NEE; SMR developers.
- **Critical-path test:** Critical-path on AI-compute graphs via
  data-center power demand. Substitutability constrained by
  interconnection queues and permitting (see archetype (d)).

### power-delivery
- **Description:** Transmission, transformers, switchgear, on-site
  distribution.
- **Typical companies:** ETN, HUBB, GEV (grid), Siemens Energy; large
  power transformer manufacturers (limited Western supply).
- **Critical-path test:** Critical-path; transformer lead times are
  the current (b) economic-saturation example. Pricing power strong
  in transformers; weaker in switchgear.

### cooling
- **Description:** Air, liquid (DLC), immersion, two-phase cooling
  for compute infrastructure.
- **Typical companies:** VRT, Schneider Electric, JCI, Munters; CDU
  and cold-plate specialists.
- **Critical-path test:** Critical-path when rack density crosses the
  air-cooling wall (~50-130 kW depending on design). Substitutability
  moderate across vendors; the substitution from air to liquid is the
  trade.

### EDA-tools
- **Description:** Chip design software, IP libraries, verification.
- **Typical companies:** Cadence, Synopsys, Siemens EDA (formerly
  Mentor), Arm (IP).
- **Critical-path test:** Critical-path for any silicon graph but
  rarely the *saturating* layer — pricing power is high but capacity
  isn't constrained. Useful as a beta on silicon volume, not as a
  bottleneck trade.

### foundry-services
- **Description:** Pure-play foundry capacity contracts, packaging
  service contracts, design-services.
- **Typical companies:** TSMC, Samsung, GF; design-service houses
  (Alchip, GUC).
- **Critical-path test:** Overlaps with fab and packaging. Useful when
  the bottleneck is *contractual allocation* rather than physical
  capacity.

### CDMO
- **Description:** Contract development and manufacturing organizations
  for biopharma.
- **Typical companies:** Lonza, Catalent, Samsung Biologics, WuXi
  Biologics (geopolitically caveated), Thermo Fisher.
- **Critical-path test:** Critical-path on biological-mechanism
  world-changers when in-house capacity lags. GLP-1 peptide CDMO is
  the current canonical case.

### raw-inputs
- **Description:** Mined / refined feedstocks — copper, lithium, rare
  earths, silicon metal, helium, uranium.
- **Typical companies:** Freeport, Albemarle, MP Materials, Cameco;
  varies by commodity.
- **Critical-path test:** Critical-path only when commodity intensity
  is high AND substitutability is low. Most raw-input plays end up
  as cyclical betas, not bottleneck trades; flag only with explicit
  intensity math.

### end-user
- **Description:** Hyperscalers, OEMs, payers, consumers — the demand
  side that pays for the buildout.
- **Typical companies:** MSFT, AMZN, GOOG, META, ORCL (hyperscalers);
  Apple, Dell, SMCI (OEMs); UNH, payers; consumers.
- **Critical-path test:** Always critical-path as the demand source,
  but almost never the bottleneck trade. Useful for sizing the
  world-changer's revenue ceiling. Hyperscaler capex disclosures are
  the primary read here.

## Cross-role rules

1. **One primary role per node, optional secondary tags.** A node that
   is both `fab` and `foundry-services` is tagged `fab` primary,
   `foundry-services` secondary. Forced choice prevents fuzzy
   classification.
2. **Role does not imply criticality.** Criticality is per-graph and
   per-thesis. The same role can be critical-path on one world-changer
   and non-critical on another.
3. **The role list is closed.** Novel layers require Mike's manual
   addition. The Mapper cannot invent new roles at runtime; that
   prevents role-inflation and forces explicit vocabulary discipline.

## Mike-input-required sections

- Whether `software-platform` and `model-weights` deserve roles of
  their own (AI agents as a world-changer arguably needs them).
- Whether `regulatory-permits` belongs as a role or stays as a
  bottleneck-archetype-only concern.
- The "pricing power" leg of the critical-path test needs an
  operational definition — gross margin trajectory? lead-time
  discipline? sole-source designation? Mike to specify before the
  Mapper starts emitting critical-path flags in production.

## Open

- Sub-role granularity inside `materials` and `raw-inputs` (the
  chemistries / commodities differ enough that a single role may be
  too coarse for the Scout's archetype scan).
- Should `cooling` split into `cooling-air` and `cooling-liquid`?
  Probably no — the substitution between them IS the trade, so they
  belong to the same role.
