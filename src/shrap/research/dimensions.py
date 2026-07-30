"""The axes a strategy can vary along, discovered rather than authored.

``guidance.py`` shipped with a hand-written ``DIMENSIONS`` tuple and a docstring
admitting the problem: *"the firm cannot notice an untried dimension nobody
thought to name."* Mike, 2026-07-30: *"lets fix the authored list."*

**Where the axes actually live.** Every rule the engine can run is a dataclass,
and its fields are exactly the parameters it accepts —
``CrossSectionalFactorStrategy`` has ``factor``, ``lookback``, ``top_n``,
``gross_exposure``, ``long_short``, and that list cannot drift from the code
because it *is* the code. :func:`dataclasses.fields` reads it. Add a rule, or a
parameter to an existing rule, and the axis appears here with no edit.

That matters more than it sounds. The authored list did not contain
``long_short`` — the single axis whose variation the firm has already measured
and been surprised by, when adding a short leg took a strategy from Sharpe
+0.782 to -0.079. A list of dimensions that omits the one dimension the firm
learned something from is a fair summary of why authored lists fail.

**Four things the comparison finds**, in descending order of usefulness:

``never-set``
    The engine accepts the parameter and no strategy has ever supplied it. The
    rule runs on its default and nobody chose that default as an answer.

``held-constant``
    Every strategy that sets it sets the same value. Available to vary, never
    varied — which is what makes a corpus of results narrower than its size
    suggests.

``unused-value``
    An enumerable axis with values the corpus has never selected. Today that is
    ``factor``, where the answer is exactly the set of implemented effects
    nobody has tried.

``ignored-by-the-engine``
    A spec sets a parameter no rule accepts, so the engine silently drops it.
    This one is a defect detector rather than guidance: a strategy whose spec
    says ``skip: 21`` under a rule with no ``skip`` is not the strategy anyone
    thinks it is.

**What this still cannot discover, and why that is now recorded elsewhere.**
Axes the engine has no representation of — bar frequency, asset class, holding
period as distinct from formation window — do not appear, because nothing in
the code mentions them. That residue is real, and it is the same residue
``research.capability_gaps`` collects from the literature (PR #156). The honest
division: this module finds what the firm could vary and has not; the capability
queue finds what the firm cannot express at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from typing import Any

from shrap.research.strategy_evaluator.cross_sectional import (
    CrossSectionalMomentumStrategy,
    CrossSectionalReversalStrategy,
    CrossSectionalTrendStrategy,
)
from shrap.research.strategy_evaluator.factors import (
    FACTOR_SCORERS,
    CrossSectionalFactorStrategy,
)
from shrap.research.strategy_evaluator.pipeline import (
    RULE_CROSS_SECTIONAL_FACTOR,
    RULE_CROSS_SECTIONAL_MOMENTUM,
    RULE_CROSS_SECTIONAL_REVERSAL,
    RULE_CROSS_SECTIONAL_TREND,
    RULE_REFERENCE_TREND,
)
from shrap.research.strategy_evaluator.reference_strategy import ReferenceTrendStrategy

# The rule → implementation binding, mirroring `_default_strategy_factory`'s
# dispatch. Authored, and the only authored thing left here — a name in a dict
# cannot be derived from a function body without parsing it.
#
# It is kept honest by a test rather than by care: `test_dimensions.py` asserts
# these keys are exactly the engine's rule set, so a rule added to the dispatch
# and not to this table fails the build instead of quietly going undiscovered.
RULE_IMPLEMENTATIONS: Mapping[str, type] = {
    RULE_REFERENCE_TREND: ReferenceTrendStrategy,
    RULE_CROSS_SECTIONAL_TREND: CrossSectionalTrendStrategy,
    RULE_CROSS_SECTIONAL_MOMENTUM: CrossSectionalMomentumStrategy,
    RULE_CROSS_SECTIONAL_REVERSAL: CrossSectionalReversalStrategy,
    RULE_CROSS_SECTIONAL_FACTOR: CrossSectionalFactorStrategy,
}

# Axes whose value space is a closed set the code enumerates, so "which values
# has nobody tried" is answerable rather than open-ended. Derived from the
# scorer table, so implementing a factor adds it here.
ENUMERABLE_VALUES: Mapping[str, frozenset[str]] = {
    "factor": frozenset(FACTOR_SCORERS),
    "rule": frozenset(RULE_IMPLEMENTATIONS),
}

# Constructor arguments that are not hypothesis axes. `ticker` names *which*
# instrument a single-name rule trades, which is the universe question the
# corpus reads from `tickers` — counting it here would report the universe
# twice under two names.
_NOT_AXES: frozenset[str] = frozenset({"ticker"})

FINDING_NEVER_SET = "never-set"
FINDING_HELD_CONSTANT = "held-constant"
FINDING_UNUSED_VALUE = "unused-value"
FINDING_IGNORED = "ignored-by-the-engine"


@dataclass(frozen=True, slots=True)
class Axis:
    """One parameter the engine accepts, and which rules accept it."""

    name: str
    rules: tuple[str, ...]
    default: Any = None

    @property
    def is_universal(self) -> bool:
        """Accepted by every rule — so never setting it is a firm-wide gap
        rather than a consequence of which rules happen to be on record."""

        return len(self.rules) == len(RULE_IMPLEMENTATIONS)


def engine_axes() -> dict[str, Axis]:
    """Every parameter the engine accepts, read off the strategy dataclasses."""

    accepted: dict[str, list[str]] = {}
    defaults: dict[str, Any] = {}
    for rule, implementation in RULE_IMPLEMENTATIONS.items():
        if not is_dataclass(implementation):
            continue
        for field in fields(implementation):
            if field.name in _NOT_AXES:
                continue
            accepted.setdefault(field.name, []).append(rule)
            defaults.setdefault(field.name, field.default)
    return {
        name: Axis(name=name, rules=tuple(sorted(rules)), default=defaults.get(name))
        for name, rules in sorted(accepted.items())
    }


def _params(spec: object) -> Mapping[str, Any]:
    if isinstance(spec, Mapping):
        params = spec.get("params")
        if isinstance(params, Mapping):
            return params
    return {}


def _rule(spec: object) -> str | None:
    if isinstance(spec, Mapping):
        rule = spec.get("rule")
        if isinstance(rule, str) and rule.strip():
            return rule.strip()
    return None


def corpus_values(specs: Sequence[object]) -> dict[str, set[str]]:
    """What the corpus has actually set, per axis, as comparable strings.

    Values are stringified because the question is "how many distinct choices",
    and ``252`` from JSON and ``252.0`` from a float column are one choice. The
    ``rule`` axis is folded in here so a rule nobody has run reads the same way
    as a factor nobody has selected.
    """

    seen: dict[str, set[str]] = {}
    for spec in specs:
        rule = _rule(spec)
        if rule is not None:
            seen.setdefault("rule", set()).add(rule)
        for name, value in _params(spec).items():
            if value is None:
                continue
            text = f"{value:g}" if isinstance(value, float) else str(value)
            seen.setdefault(str(name), set()).add(text)
    return seen


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing the corpus has not varied, or has varied wrongly."""

    kind: str
    axis: str
    detail: str
    rules: tuple[str, ...] = ()

    def render(self) -> str:
        where = f" (on {', '.join(self.rules)})" if self.rules else ""
        return f"`{self.axis}`{where} — {self.detail}"


def survey(specs: Sequence[object]) -> list[Finding]:
    """Compare what the engine accepts against what the corpus has chosen.

    Pure. Reads specs and code, decides nothing about promotion, and by
    construction cannot: no argument here carries a result, a metric, or a
    verdict, so there is no path by which an outcome could influence what this
    reports. That is the same boundary ``guidance.py`` states out loud, enforced
    here by the shape of the signature rather than by discipline.
    """

    axes = engine_axes()
    used = corpus_values(specs)
    findings: list[Finding] = []

    for name, axis in axes.items():
        values = used.get(name, set())
        if not values:
            findings.append(
                Finding(
                    kind=FINDING_NEVER_SET,
                    axis=name,
                    detail=(
                        f"the engine accepts it and no strategy has ever set it; "
                        f"every run took the default ({axis.default!r})"
                    ),
                    rules=axis.rules,
                )
            )
        elif len(values) == 1:
            only = next(iter(values))
            findings.append(
                Finding(
                    kind=FINDING_HELD_CONSTANT,
                    axis=name,
                    detail=(
                        f"every strategy that sets it sets {only!r} — available to "
                        f"vary, never varied"
                    ),
                    rules=axis.rules,
                )
            )

    for name, known in ENUMERABLE_VALUES.items():
        untried = sorted(known - used.get(name, set()))
        if untried:
            findings.append(
                Finding(
                    kind=FINDING_UNUSED_VALUE,
                    axis=name,
                    detail=f"implemented but never selected: {', '.join(untried)}",
                )
            )

    for name in sorted(used):
        if name != "rule" and name not in axes:
            findings.append(
                Finding(
                    kind=FINDING_IGNORED,
                    axis=name,
                    detail=(
                        "set in a spec but accepted by no rule, so the engine drops "
                        "it — that strategy is not the strategy its spec describes"
                    ),
                )
            )

    return findings


def render(findings: Sequence[Finding]) -> str:
    """Group by kind, most actionable first."""

    if not findings:
        return "Every axis the engine accepts has been varied at least once."
    order = (
        (FINDING_IGNORED, "IGNORED BY THE ENGINE — a spec says something the engine drops"),
        (FINDING_NEVER_SET, "NEVER SET — the engine accepts it, nothing has chosen it"),
        (FINDING_UNUSED_VALUE, "UNUSED VALUES — implemented, never selected"),
        (FINDING_HELD_CONSTANT, "HELD CONSTANT — set the same way every time"),
    )
    lines: list[str] = []
    for kind, title in order:
        group = [f for f in findings if f.kind == kind]
        if not group:
            continue
        lines.append(title)
        lines.extend(f"  - {f.render()}" for f in group)
        lines.append("")
    lines.append(
        "Read off the strategy dataclasses, not a hand-written list. An axis the "
        "engine cannot express at all does not appear here — that residue is what "
        "`research.capability_gaps` collects from the literature."
    )
    return "\n".join(lines)


__all__ = [
    "ENUMERABLE_VALUES",
    "FINDING_HELD_CONSTANT",
    "FINDING_IGNORED",
    "FINDING_NEVER_SET",
    "FINDING_UNUSED_VALUE",
    "RULE_IMPLEMENTATIONS",
    "Axis",
    "Finding",
    "corpus_values",
    "engine_axes",
    "render",
    "survey",
]
