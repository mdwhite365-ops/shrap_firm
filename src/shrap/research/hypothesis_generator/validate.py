"""The deterministic gate. No model runs here, and none is consulted.

Every check in this file is a function of the parsed proposal and the registry's
current contents. That is the point: the trust in this agent comes from the fact
that a person can read these rules and know what can and cannot get through,
without reasoning about what a language model might say on a given evening.

Per the spec: **the agent does not retry the model to fix a rejection.** It logs
the reason, marks the item, and moves on. A retry loop is a search over model
outputs until one passes, which is the same failure as a parameter sweep wearing
different clothes.

The two checks that matter most:

``no-prior``
    The literature is this archetype's anchor (ADR-0013 gives
    ``technical-catalyst`` no world-changer node). A price-based strategy with
    no citation is exactly the freelancing the Hypothesis Generator exists to
    prevent, so a missing or unattributed prior ends the proposal.

``already-held``
    A proposal whose ``(rule, factor)`` identity already exists is not a new
    hypothesis — it is attempt N of an existing lineage, and registering it as a
    root would launder a search past the multiple-testing gate. This is the one
    way this archetype could quietly corrupt a promote decision, so the check is
    on identity rather than on the proposer's own ``distinct_from`` claim.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from shrap.research.hypothesis_generator.expressible import (
    EXPRESSIBLE_RULES,
    hypothesis_key,
)
from shrap.research.hypothesis_generator.proposer import RawProposal
from shrap.research.strategy_evaluator.cross_sectional import (
    MOMENTUM_PARAM_BOUNDS,
    REVERSAL_PARAM_BOUNDS,
)
from shrap.research.strategy_evaluator.factors import FACTOR_PARAM_BOUNDS
from shrap.research.strategy_evaluator.pipeline import (
    RULE_CROSS_SECTIONAL_FACTOR,
    RULE_CROSS_SECTIONAL_MOMENTUM,
    RULE_CROSS_SECTIONAL_REVERSAL,
)

REASON_UNPARSEABLE = "unparseable-response"
REASON_NOT_A_MARKET_EFFECT = "not-a-market-effect"
REASON_NO_PRIOR = "no-prior"
REASON_NO_EFFECT_NAME = "no-effect-name"
REASON_NO_SKETCH = "no-scorer-sketch"
REASON_UNKNOWN_RULE = "unknown-rule"
REASON_NO_LOOKBACK = "no-lookback"
REASON_LOOKBACK_OUT_OF_BOUNDS = "lookback-out-of-bounds"
REASON_ALREADY_HELD = "already-held"
REASON_THIN_KILL_CRITERIA = "thin-kill-criteria"
REASON_THIN_THESIS = "thin-thesis"

# Per-rule lookback windows, taken from the engine's own bounds rather than
# restated. Momentum and reversal are disjoint on purpose (21 sessions is the
# boundary): the horizon is what distinguishes the two opposite effects, so a
# spec that could express either has stopped saying which one it is.
_LOOKBACK_BOUNDS: Mapping[str, tuple[float, float]] = {
    RULE_CROSS_SECTIONAL_MOMENTUM: MOMENTUM_PARAM_BOUNDS["lookback"],
    RULE_CROSS_SECTIONAL_REVERSAL: REVERSAL_PARAM_BOUNDS["lookback"],
    RULE_CROSS_SECTIONAL_FACTOR: FACTOR_PARAM_BOUNDS["lookback"],
}

# A single kill criterion is almost always a restatement of the thesis. Two is
# the point at which the proposer has had to think about more than one way the
# effect dies.
MIN_KILL_CRITERIA = 2

# Short enough that a real paragraph clears it, long enough that a sentence
# fragment does not. A thesis nobody can argue with later is not a thesis.
MIN_THESIS_CHARS = 80


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why a proposal did not become a strategy."""

    reason: str
    detail: str

    def render(self) -> str:
        return f"{self.reason}: {self.detail}"


def check_citable(raw: RawProposal) -> Refusal | None:
    """Is there a claim, attributed to someone, that could be implemented?

    Runs *before* the capability classification, because an uncitable item is
    worth nothing as a capability gap either — a build queue entry that cannot
    say which paper asked for it is a suggestion, not evidence.
    """

    if not raw.is_market_effect:
        return Refusal(REASON_NOT_A_MARKET_EFFECT, raw.reason)
    if raw.prior is None:
        return Refusal(
            REASON_NO_PRIOR,
            "no authors, year and claim — the literature is this archetype's only "
            "anchor, so an unattributed effect is freelancing",
        )
    if not raw.effect_name:
        return Refusal(REASON_NO_EFFECT_NAME, "the effect was not named")
    if not raw.scorer_sketch:
        return Refusal(
            REASON_NO_SKETCH,
            "no description of how the per-stock score is computed, so neither a "
            "reviewer nor a later implementer could check the claim",
        )
    return None


def check_spec(raw: RawProposal, held: Mapping[str, str]) -> Refusal | None:
    """Is this a runnable, non-duplicate spec?

    ``held`` maps :func:`hypothesis_key` to the strategy_id that already owns it.
    """

    if raw.rule not in EXPRESSIBLE_RULES:
        known = ", ".join(sorted(EXPRESSIBLE_RULES))
        return Refusal(REASON_UNKNOWN_RULE, f"rule {raw.rule!r} is not one of: {known}")
    if raw.lookback is None:
        return Refusal(REASON_NO_LOOKBACK, "no formation window in trading sessions")
    low, high = _LOOKBACK_BOUNDS[raw.rule]
    if not low <= raw.lookback <= high:
        return Refusal(
            REASON_LOOKBACK_OUT_OF_BOUNDS,
            f"lookback {raw.lookback} is outside {raw.rule}'s window "
            f"[{int(low)}, {int(high)}] — a horizon outside the rule's range is a "
            "different effect being run under this rule's name",
        )
    key = hypothesis_key(raw.rule, raw.factor)
    owner = held.get(key)
    if owner is not None:
        return Refusal(
            REASON_ALREADY_HELD,
            f"{key} is already held by {owner}; a different parameterisation of an "
            "effect the firm holds is attempt N of that lineage, not a new "
            "hypothesis, and registering it as a root would understate the search",
        )
    if len(raw.kill_criteria) < MIN_KILL_CRITERIA:
        return Refusal(
            REASON_THIN_KILL_CRITERIA,
            f"{len(raw.kill_criteria)} kill criteria, need {MIN_KILL_CRITERIA}",
        )
    if len(raw.thesis) < MIN_THESIS_CHARS:
        return Refusal(
            REASON_THIN_THESIS,
            f"thesis is {len(raw.thesis)} characters; a claim nobody can argue "
            "with later is not a thesis",
        )
    return None


__all__ = [
    "MIN_KILL_CRITERIA",
    "MIN_THESIS_CHARS",
    "REASON_ALREADY_HELD",
    "REASON_LOOKBACK_OUT_OF_BOUNDS",
    "REASON_NOT_A_MARKET_EFFECT",
    "REASON_NO_EFFECT_NAME",
    "REASON_NO_LOOKBACK",
    "REASON_NO_PRIOR",
    "REASON_NO_SKETCH",
    "REASON_THIN_KILL_CRITERIA",
    "REASON_THIN_THESIS",
    "REASON_UNKNOWN_RULE",
    "REASON_UNPARSEABLE",
    "Refusal",
    "check_citable",
    "check_spec",
]
