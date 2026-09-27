"""Seed a ``signal-spec`` strategy from a JSON document instead of from code.

Every earlier seed family is a Python module of constants, which made adding a
strategy a PR. A document looks like::

    {"name": "Short-horizon magnitude, bottom 10",
     "thesis": "... what effect, who found it, why it should exist here ...",
     "kill_criteria": ["... how this specific effect is known to die ..."],
     "params": {"signal": {...}, "select": "bottom", "top_n": 10}}

The document is validated by building the strategy (so a bad expression is
refused before anything is written), the firm's common falsifiers are appended
to its own, and it is registered at ``hypothesis`` as a lineage root over the
launch universe. Idempotent on ``spec_hash``: loading the same document twice
writes one row.

A thesis and at least one effect-specific kill criterion are required. A spec
with neither is a formula, not a hypothesis, and the Evaluator's report would
have nothing to hold the result against.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from ulid import ULID

from shrap.research.strategy_evaluator.pipeline import (
    ARCHETYPE_TECHNICAL_CATALYST,
    RULE_SIGNAL_SPEC,
)
from shrap.research.strategy_evaluator.signals import PARAM_BOUNDS, SignalSpecStrategy
from shrap.research.strategy_registry import STATUS_HYPOTHESIS, StrategyRecord
from shrap.research.strategy_seed.factor_strategies import COMMON_KILL_CRITERIA
from shrap.research.strategy_seed.technical_strategies import (
    _MOMENTUM_TICKERS,
    ANCHOR,
    REGIME_SIZING_MODIFIER,
    SOURCE,
)

CODE_REF = "src/shrap/research/strategy_evaluator/signals.py"
MIN_THESIS_CHARS = 80


class SpecDocumentError(ValueError):
    """The document is not a seedable strategy. Nothing is written."""


def _spec(params: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "rule": RULE_SIGNAL_SPEC,
        "params": dict(params),
        "param_bounds": {k: list(v) for k, v in PARAM_BOUNDS.items()},
    }


def spec_record(doc: Mapping[str, Any], *, strategy_id: str | None = None) -> StrategyRecord:
    """Validate ``doc`` and build its registry record."""

    name = str(doc.get("name", "")).strip()
    thesis = str(doc.get("thesis", "")).strip()
    kills = doc.get("kill_criteria")
    params = doc.get("params")
    if not name:
        raise SpecDocumentError("a spec document needs a name")
    if len(thesis) < MIN_THESIS_CHARS:
        raise SpecDocumentError(
            f"'{name}': the thesis must say what effect this is and why it should exist "
            f"(at least {MIN_THESIS_CHARS} characters)"
        )
    if not isinstance(kills, list) or not [k for k in kills if str(k).strip()]:
        raise SpecDocumentError(f"'{name}': at least one effect-specific kill criterion")
    if not isinstance(params, Mapping):
        raise SpecDocumentError(f"'{name}': params must be an object")
    try:
        SignalSpecStrategy.from_spec(params)
    except ValueError as exc:
        raise SpecDocumentError(f"'{name}': {exc}") from exc

    spec = _spec(params)
    tickers = {"long": list(_MOMENTUM_TICKERS), "short": []}
    material = json.dumps(
        {
            "name": name,
            "archetype": ARCHETYPE_TECHNICAL_CATALYST,
            "anchor": ANCHOR,
            "tickers": tickers,
            "spec": spec,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return StrategyRecord(
        strategy_id=strategy_id or str(ULID()),
        name=name,
        version=1,
        archetype=ARCHETYPE_TECHNICAL_CATALYST,
        status=STATUS_HYPOTHESIS,
        source=str(doc.get("source", SOURCE)),
        thesis=thesis,
        anchor=dict(ANCHOR),
        tickers=tickers,
        spec=spec,
        spec_hash="sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest(),
        regime_sizing_modifier=dict(REGIME_SIZING_MODIFIER),
        kill_criteria=[str(k).strip() for k in kills if str(k).strip()]
        + list(COMMON_KILL_CRITERIA),
        code_ref=CODE_REF,
        created_at=None,
        updated_at=None,
        parent_strategy_id=None,
        revision_reason=None,
        derived_from_evaluation_id=None,
    )


def load_documents(text: str) -> list[Mapping[str, Any]]:
    """One document, or a list of them."""

    raw = json.loads(text)
    docs = raw if isinstance(raw, list) else [raw]
    if not all(isinstance(d, Mapping) for d in docs):
        raise SpecDocumentError("expected a JSON object or a list of objects")
    return docs


__all__ = [
    "CODE_REF",
    "MIN_THESIS_CHARS",
    "SpecDocumentError",
    "load_documents",
    "spec_record",
]
