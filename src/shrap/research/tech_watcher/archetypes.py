"""World-changer archetype vocabulary (code mirror).

Mirrors ``docs/research/world-changer-archetypes.md`` v0.1 — the doc is the
decision record (Mike-owned, reviewed quarterly); this module is the runtime
mirror the filter prompt and validator use, same pattern as the LLM registry
mirror. HISTORICAL archetypes stay in scanning scope per the doc's (d) note —
the flag marks promotion posture, not filter scope.
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
    ),
    Archetype(
        key="cost-curve",
        name="Cost-curve crossings",
        definition=(
            "A technology's unit cost durably crosses the threshold where adoption tips "
            "from subsidized/niche to unsubsidized/mass."
        ),
        live=True,
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
    ),
)

ARCHETYPE_KEYS: frozenset[str] = frozenset(a.key for a in ARCHETYPES)


def archetype_prompt_block() -> str:
    """The archetype vocabulary as a prompt fragment."""

    lines = []
    for a in ARCHETYPES:
        lines.append(f"- {a.key}: {a.name}. {a.definition}")
    return "\n".join(lines)
