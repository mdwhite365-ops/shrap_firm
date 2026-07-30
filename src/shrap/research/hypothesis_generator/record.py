"""Turning a validated proposal into a registry row.

Construction is assembled here, not by the model, and that is deliberate. Three
things are stapled on regardless of what the proposal said:

**The deviation always names the long-only launch universe.** The firm's
momentum strategy dropped the short leg of Jegadeesh-Titman and nothing recorded
that it had, so a one-sided book was read for months as evidence about momentum
rather than about the deviation. Every proposal this agent writes is long-only
over 50 large caps, and every one of them says so in its own thesis.

**The protocol kill criteria are appended.** The proposer supplies the ways its
particular effect is known to die; the three ways *any* strategy fails the firm's
protocol — losing to buy-and-hold, dying under friction, winning in fewer than
half the folds — are not the proposer's to omit.

**Provenance is stapled to the spec.** Which literature item, which prompt
version, which model, and the prior in the proposer's own words. A strategy is
going to be read months from now by someone deciding whether its result meant
anything, and the answer depends on what it was implementing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ulid import ULID

from shrap.research.hypothesis_generator.literature import LiteratureItem
from shrap.research.hypothesis_generator.proposer import (
    PROPOSER_PROMPT_VERSION,
    RawProposal,
)
from shrap.research.strategy_evaluator.cross_sectional import (
    DEFAULT_GROSS_EXPOSURE,
    DEFAULT_REVERSAL_SKIP,
    DEFAULT_SKIP,
    DEFAULT_TOP_N,
    MOMENTUM_PARAM_BOUNDS,
    REVERSAL_PARAM_BOUNDS,
)
from shrap.research.strategy_evaluator.factors import FACTOR_PARAM_BOUNDS
from shrap.research.strategy_evaluator.pipeline import (
    ARCHETYPE_TECHNICAL_CATALYST,
    RULE_CROSS_SECTIONAL_FACTOR,
    RULE_CROSS_SECTIONAL_MOMENTUM,
    RULE_CROSS_SECTIONAL_REVERSAL,
)
from shrap.research.strategy_registry import STATUS_HYPOTHESIS, StrategyRecord
from shrap.research.strategy_seed.factor_strategies import COMMON_KILL_CRITERIA
from shrap.research.strategy_seed.technical_strategies import (
    _MOMENTUM_TICKERS,
    ANCHOR,
    REGIME_SIZING_MODIFIER,
)

CODE_REF = "src/shrap/research/hypothesis_generator/record.py"
SOURCE = "hypothesis-generator"

# Construction the proposer does not get to vary. Held identical so that a
# comparison between two proposals measures the two EFFECTS; free to vary, it
# measures two implementations and says nothing about either.
FIXED_TOP_N = DEFAULT_TOP_N
FIXED_GROSS_EXPOSURE = DEFAULT_GROSS_EXPOSURE
FIXED_LONG_SHORT = False

# Stapled to every deviation. The universe and the missing short leg are real
# departures from nearly every paper's construction, and the reason this text is
# unconditional is that the one time it was left to a human to remember, it was
# not remembered.
STRUCTURAL_DEVIATION = (
    "Implemented long-only over the firm's 50-name large-cap launch universe, "
    "equal-weighted across the top 10 ranked names. Most published constructions "
    "are long/short over a far broader cross-section, so the short leg and the "
    "breadth of the original are both absent here."
)

_UNIVERSE: tuple[str, ...] = _MOMENTUM_TICKERS


def _params_for(raw: RawProposal) -> tuple[dict[str, Any], dict[str, list[float]]]:
    """Parameters and their declared bounds, per rule.

    Bounds come from the engine's own tables rather than being restated, so a
    rule whose bounds move cannot leave a proposal declaring the old ones.
    """

    lookback = int(raw.lookback or 0)
    common: dict[str, Any] = {
        "lookback": lookback,
        "top_n": FIXED_TOP_N,
        "gross_exposure": FIXED_GROSS_EXPOSURE,
        "long_short": FIXED_LONG_SHORT,
    }
    if raw.rule == RULE_CROSS_SECTIONAL_MOMENTUM:
        return {**common, "skip": DEFAULT_SKIP}, _bounds(MOMENTUM_PARAM_BOUNDS)
    if raw.rule == RULE_CROSS_SECTIONAL_REVERSAL:
        return {**common, "skip": DEFAULT_REVERSAL_SKIP}, _bounds(REVERSAL_PARAM_BOUNDS)
    if raw.rule == RULE_CROSS_SECTIONAL_FACTOR:
        return {**common, "factor": raw.factor}, _bounds(FACTOR_PARAM_BOUNDS)
    raise ValueError(f"no parameter template for rule {raw.rule!r}")


def _bounds(table: dict[str, tuple[float, float]]) -> dict[str, list[float]]:
    return {name: [lo, hi] for name, (lo, hi) in table.items()}


def proposal_name(raw: RawProposal) -> str:
    """A readable name. The registry enforces uniqueness on ``(name, version)``."""

    words = raw.effect_name.replace("-", " ").strip()
    title = (words[:1].upper() + words[1:]) if words else "Unnamed effect"
    return f"{title} ({raw.lookback}d, top {FIXED_TOP_N})"[:120]


def deviation_text(raw: RawProposal) -> str:
    stated = raw.deviation.strip()
    if stated.lower() in {"", "none", "none."}:
        return STRUCTURAL_DEVIATION
    return f"{stated} {STRUCTURAL_DEVIATION}"


def thesis_text(raw: RawProposal, item: LiteratureItem) -> str:
    """The proposal's own paragraph, then the three fields that make it checkable.

    Appended deterministically rather than requested from the model: a citation
    the model was asked to remember to include is a citation that will be missing
    from the one proposal nobody re-reads.
    """

    prior = raw.prior.render() if raw.prior is not None else "(no prior recorded)"
    source = item.url or item.item_id
    return (
        f"{raw.thesis}\n\n"
        f"PRIOR: {prior}\n"
        f"DEVIATION: {deviation_text(raw)}\n"
        f"SOURCE: {item.title} — {source}\n"
        f"No world-changer anchor: the thesis is entirely about price and volume "
        f"behaviour (ADR-0013)."
    )


def build_spec(raw: RawProposal, item: LiteratureItem) -> dict[str, Any]:
    params, bounds = _params_for(raw)
    return {
        "rule": raw.rule,
        "params": params,
        "param_bounds": bounds,
        # Inert to the engine, load-bearing for a reviewer. `distinct_from` is
        # the proposer's CLAIM that this is not an effect the firm already
        # holds; nothing here verifies it. The verification is the fold-IR
        # correlation named as card 3 of this agent's prerequisites, and until
        # that exists the claim is a claim.
        "provenance": {
            "archetype": ARCHETYPE_TECHNICAL_CATALYST,
            "effect_name": raw.effect_name,
            "prior": (
                None
                if raw.prior is None
                else {
                    "authors": raw.prior.authors,
                    "year": raw.prior.year,
                    "claim": raw.prior.claim,
                }
            ),
            "deviation": deviation_text(raw),
            "distinct_from": "unverified — see fold-IR correlation card",
            "scorer_sketch": raw.scorer_sketch,
            "literature_item_id": item.item_id,
            "literature_url": item.url,
            "prompt_version": PROPOSER_PROMPT_VERSION,
            "model": raw.model,
        },
    }


def compute_spec_hash(name: str, spec: dict[str, Any]) -> str:
    """Dedup key, same material as every hand-written seed family."""

    material = json.dumps(
        {
            "name": name,
            "archetype": ARCHETYPE_TECHNICAL_CATALYST,
            "anchor": ANCHOR,
            "tickers": {"long": list(_UNIVERSE), "short": []},
            "spec": spec,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_record(raw: RawProposal, item: LiteratureItem) -> StrategyRecord:
    """A lineage root at ``hypothesis``, ready for the registry.

    **Always a root.** ``parent_strategy_id`` is ``None`` and this agent has no
    path to set it. A proposer that could nominate a parent could also decline
    to, and declining is how a variant gets registered as a fresh idea with its
    attempt count reset to one — the single way this archetype could corrupt a
    promote decision (PR #148).
    """

    name = proposal_name(raw)
    spec = build_spec(raw, item)
    return StrategyRecord(
        strategy_id=str(ULID()),
        name=name,
        version=1,
        archetype=ARCHETYPE_TECHNICAL_CATALYST,
        status=STATUS_HYPOTHESIS,
        source=SOURCE,
        thesis=thesis_text(raw, item),
        anchor=dict(ANCHOR),
        tickers={"long": list(_UNIVERSE), "short": []},
        spec=spec,
        spec_hash=compute_spec_hash(name, spec),
        regime_sizing_modifier=dict(REGIME_SIZING_MODIFIER),
        kill_criteria=[*raw.kill_criteria, *COMMON_KILL_CRITERIA],
        code_ref=CODE_REF,
        created_at=None,
        updated_at=None,
        parent_strategy_id=None,
        revision_reason=None,
        derived_from_evaluation_id=None,
    )


__all__ = [
    "CODE_REF",
    "FIXED_GROSS_EXPOSURE",
    "FIXED_LONG_SHORT",
    "FIXED_TOP_N",
    "SOURCE",
    "STRUCTURAL_DEVIATION",
    "build_record",
    "build_spec",
    "compute_spec_hash",
    "deviation_text",
    "proposal_name",
    "thesis_text",
]
