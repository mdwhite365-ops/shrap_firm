"""Infrastructure Mapper — step 2 of the Research funnel (ADR-0007).

For each Mike-promoted world-changer, the Mapper builds and maintains a
dependency graph of the layers (suppliers, enablers, contractors, downstream
beneficiaries) required for the thesis to play out, and which layer is on the
critical path *right now* (the Cisco-1999 lesson — see
``docs/agents/research/infrastructure-mapper.md``).

This package is the Month-2 substrate: the graph schema and store. The
hand-seeded first graph, the approval/maintenance CLI, and (later) the
LLM-assisted enumeration, bottleneck integration, and weekly universe
aggregation land in follow-on cards.
"""
