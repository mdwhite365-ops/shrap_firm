"""The first hand-seeded infrastructure graph (Month-2, deterministic).

Anchored on the promoted world-changer *mass-manufactured fission cost-curve
crossing* (``candidate_id = 01KXVVPXDMB4HS1QNRPQWRP1RX``) — the same anchor the
seed strategy uses, so the whole chain stays coherent.

**Honest structure (read this).** The layer-role taxonomy is a closed list built
around the AI-compute supply chain; the fission thesis's *critical-path* layers —
``raw-inputs`` (uranium: CCJ), ``power-gen`` (nuclear operators: CEG, VST, TLN),
and SMR developers — are **not in the locked Tier-3 universe**. That is a real
universe gap, and forcing a wrong-layer Tier-3 ticker into the graph would be the
exact Cisco-1999 failure the Mapper exists to avoid. So the graph is deliberately
small and honest: the only fission layer with clean Tier-3 representation is the
``end-user`` / downstream-beneficiary demand side — hyperscalers buying cheap firm
power for data centers, each with a real 2024 nuclear deal. They are marked
``downstream-beneficiary`` at ``low`` confidence (the taxonomy notes end-user is
"almost never the bottleneck trade"). The message the graph honestly sends: the
fission thesis is only weakly expressible in today's 50-name universe.

LLM-assisted layer/ticker enumeration (which would build a rich AI-compute graph)
and the ``universe.gap.detected`` emission are later cards. Evidence refs here are
hand-entered Month-2 seed pointers (company + deal + date), resolvable by a human.
"""

from __future__ import annotations

from typing import NamedTuple

from shrap.research.infra_mapper.store import (
    CONFIDENCE_LOW,
    CRITICAL_DOWNSTREAM,
    NODE_ACTIVE,
)

# Fixed identity so reloads and the graph card always reference the same graph.
SEED_GRAPH_ID = "01KYH0MAPFISSIONGRAPH0001A"
SEED_WORLD_CHANGER_ID = "01KXVVPXDMB4HS1QNRPQWRP1RX"
SEED_GRAPH_TITLE = "Mass-manufactured fission cost-curve crossing"

# The one fission layer with clean Tier-3 representation.
LAYER_END_USER = "end-user"

# Fission thesis breaker shared by every node: if the anchor world-changer is no
# longer promoted, the whole graph's thesis is dead.
_ANCHOR_KILL = (
    "world-changer anchor no longer 'promoted' in research.world_changers "
    "(mass-manufactured fission thesis broken)"
)


class SeedNode(NamedTuple):
    """One hand-seeded graph node bound for ``research.graph_nodes``."""

    ticker: str
    layer_role: str
    confidence: str
    critical_path_status: str
    evidence_ref: str
    evidence_source_class: str
    kill_criteria: tuple[str, ...]


# Hyperscaler beneficiaries of cheap firm (fission) power for data centers. Each
# carries a real 2024 nuclear procurement as its evidence. All Tier-3 launch
# names (mega-cap-tech). Downstream-beneficiary, low confidence — honest: this is
# the demand side, not the bottleneck trade.
SEED_NODES: tuple[SeedNode, ...] = (
    SeedNode(
        ticker="MSFT",
        layer_role=LAYER_END_USER,
        confidence=CONFIDENCE_LOW,
        critical_path_status=CRITICAL_DOWNSTREAM,
        evidence_ref="Microsoft-Constellation Three Mile Island (Crane) 20-yr PPA, 2024-09-20",
        evidence_source_class="issuer",
        kill_criteria=(_ANCHOR_KILL, "Microsoft data-center / AI capex guidance cut materially"),
    ),
    SeedNode(
        ticker="AMZN",
        layer_role=LAYER_END_USER,
        confidence=CONFIDENCE_LOW,
        critical_path_status=CRITICAL_DOWNSTREAM,
        evidence_ref="Amazon-Talen Susquehanna nuclear data-center campus + X-energy SMR, 2024",
        evidence_source_class="issuer",
        kill_criteria=(_ANCHOR_KILL, "AWS capacity build-out slows or nuclear siting stalls"),
    ),
    SeedNode(
        ticker="GOOGL",
        layer_role=LAYER_END_USER,
        confidence=CONFIDENCE_LOW,
        critical_path_status=CRITICAL_DOWNSTREAM,
        evidence_ref="Google-Kairos Power SMR offtake agreement, 2024-10-14",
        evidence_source_class="issuer",
        kill_criteria=(_ANCHOR_KILL, "Google cloud capex guidance cut materially"),
    ),
    SeedNode(
        ticker="META",
        layer_role=LAYER_END_USER,
        confidence=CONFIDENCE_LOW,
        critical_path_status=CRITICAL_DOWNSTREAM,
        evidence_ref="Meta nuclear-energy RFP (1-4 GW) for AI data centers, 2024-12",
        evidence_source_class="issuer",
        kill_criteria=(_ANCHOR_KILL, "Meta AI infrastructure spend guidance cut materially"),
    ),
)

# Node lifecycle status the seed load writes (below the 8-node auto-activation
# threshold in the spec, so nodes go straight to active).
SEED_NODE_STATUS = NODE_ACTIVE

# Universe gap recorded honestly for the graph card / daily summary: the
# critical-path fission layers that have NO Tier-3 representation today.
UNIVERSE_GAP_LAYERS: tuple[tuple[str, str], ...] = (
    ("raw-inputs", "uranium mining/enrichment (e.g. CCJ) — not in Tier-3"),
    ("power-gen", "nuclear operators / SMR developers (e.g. CEG, VST, TLN, SMR) — not in Tier-3"),
    ("power-delivery", "transmission / transformers (e.g. ETN, GEV) — not in Tier-3"),
)


__all__ = [
    "LAYER_END_USER",
    "SEED_GRAPH_ID",
    "SEED_GRAPH_TITLE",
    "SEED_NODES",
    "SEED_NODE_STATUS",
    "SEED_WORLD_CHANGER_ID",
    "UNIVERSE_GAP_LAYERS",
    "SeedNode",
]
