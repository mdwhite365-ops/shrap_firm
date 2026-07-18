"""World-changer archetype vocabulary (code mirror).

Mirrors ``docs/research/world-changer-archetypes.md`` v0.1 — the doc is the
decision record (Mike-owned, reviewed quarterly); this module is the runtime
mirror the filter prompt and validator use, same pattern as the LLM registry
mirror. HISTORICAL archetypes stay in scanning scope per the doc's (d) note —
the flag marks promotion posture, not filter scope.

The signature signals and impostor lists are condensed from the doc so the
filter prompt carries the full recognition grammar, not just definitions —
the 2026-07-17 first-batch calibration showed that a definitions-only prompt
over-flags methods papers and impostor-shaped items the doc already warns
about.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Archetype:
    """One recognized world-changer pattern."""

    key: str
    name: str
    definition: str
    live: bool
    signals: tuple[str, ...]
    impostors: tuple[str, ...]


ARCHETYPES: tuple[Archetype, ...] = (
    Archetype(
        key="compute-substrate",
        name="Compute-substrate revolutions",
        definition=(
            "A new computational substrate (architecture, accelerator class, or "
            "programming model) becomes the platform a generation of applications is "
            "built on; incumbents in the old substrate adapt or are bypassed."
        ),
        live=True,
        signals=(
            "specialized silicon showing >5x (not 1.5x) TCO advantage on a workload class",
            "a software moat forming around the substrate (tooling + libraries + hiring pool)",
            "sustained multi-quarter hyperscaler capex redirection toward the substrate",
            "application revenue (not just infrastructure revenue) attributing to it",
        ),
        impostors=(
            "GPU-for-everything hype without application demand (circa 2010)",
            "quantum computing as a general-purpose substrate (perpetually five years out)",
            "neuromorphic chips as a mainstream alternative",
            "crypto-mining ASIC firms reframed as AI compute",
            "ML methods/architecture papers — a new model or training technique is not a "
            "substrate; evidence must show adoption economics, not algorithmic novelty",
        ),
    ),
    Archetype(
        key="bio-mechanism",
        name="Biological-mechanism unlocks",
        definition=(
            "A specific biological mechanism (receptor, pathway, modality) becomes "
            "clinically and commercially viable at scale and produces a step-change in "
            "a large-population disease."
        ),
        live=True,
        signals=(
            "mechanism validated in multiple independent Phase 3 trials, not one",
            "manufacturing scale demonstrated at acceptable cost",
            "payer coverage expanding beyond on-label to adjacent indications",
        ),
        impostors=(
            "platform claims with no validated mechanism (Theranos-shaped)",
            "mechanism-real-but-benefit-marginal plays (amyloid-shaped)",
            "trial success without durability or manufacturing economics",
        ),
    ),
    Archetype(
        key="cost-curve",
        name="Cost-curve crossings",
        definition=(
            "A technology's unit cost durably crosses the threshold where adoption tips "
            "from subsidized/niche to unsubsidized/mass."
        ),
        live=True,
        signals=(
            "unit cost declining on a learning-curve slope consistent across producers",
            "unsubsidized adoption in markets without policy support",
            "adjacent infrastructure built ahead of demand",
        ),
        impostors=(
            "hydrogen-economy-shaped curves that never actually cross",
            "adoption that is subsidy-dependent presented as a crossing",
            "carbon capture above unsubsidized-viable $/ton",
        ),
    ),
    Archetype(
        key="physical-realization",
        name="Physical-realization breakthroughs",
        definition=(
            "A long-theorized physical capability (fusion ignition, room-temperature "
            "superconductivity, useful quantum advantage, true autonomy) becomes real at "
            "lab scale with a credible path to engineering scale."
        ),
        live=False,
        signals=(
            "independent replication by a different group with different apparatus",
            "engineering metrics crossing published theoretical floors",
            "capital and personnel shifting from theory groups to engineering groups",
        ),
        impostors=(
            "unreplicated headline claims (LK-99-shaped)",
            "cold-fusion-shaped announcements",
            "perpetual next-year autonomy timelines",
        ),
    ),
    Archetype(
        key="platform-shift",
        name="Platform shifts",
        definition=(
            "A new computing or interaction platform emerges and a generation of "
            "applications is rebuilt natively on it; incumbents who miss the jump lose "
            "distribution."
        ),
        live=True,
        signals=(
            "a new primary interaction surface reaching generational DAU scale in ~24 months",
            "developer-tool ecosystem and revenue-share economics standardizing on it",
            "native applications dominating retention vs ports",
        ),
        impostors=(
            "metaverse-shaped platform bets with no killer application at scale",
            "VR/AR as a perpetually emerging mass platform",
            "smart-speaker-shaped commerce surfaces",
        ),
    ),
)

ARCHETYPE_KEYS: frozenset[str] = frozenset(a.key for a in ARCHETYPES)

_BY_KEY: dict[str, Archetype] = {a.key: a for a in ARCHETYPES}


def get_archetype(key: str) -> Archetype | None:
    return _BY_KEY.get(key)


def archetype_prompt_block() -> str:
    """The archetype vocabulary as a compact prompt fragment (definitions only)."""

    lines = []
    for a in ARCHETYPES:
        lines.append(f"- {a.key}: {a.name}. {a.definition}")
    return "\n".join(lines)


def archetype_filter_prompt_block() -> str:
    """The full recognition grammar for the bulk filter: definitions,
    signature signals, and known impostors per archetype."""

    lines = []
    for a in ARCHETYPES:
        lines.append(f"### {a.key}: {a.name}")
        lines.append(a.definition)
        lines.append("Signature signals (what real evidence looks like):")
        lines.extend(f"  - {s}" for s in a.signals)
        lines.append("Known impostors (looks similar, is NOT evidence):")
        lines.extend(f"  - {i}" for i in a.impostors)
        lines.append("")
    return "\n".join(lines).strip()


def archetype_impostor_block(key: str) -> str:
    """One archetype's impostor list for the synthesis prompt."""

    archetype = _BY_KEY.get(key)
    if archetype is None:
        return ""
    lines = [f"Known impostors for {archetype.key} (do not build a candidate that is one):"]
    lines.extend(f"- {i}" for i in archetype.impostors)
    return "\n".join(lines)
