"""A strategy is a JSON document: validated first, registered once."""

from __future__ import annotations

import json
from typing import Any

import pytest

from shrap.research.strategy_evaluator.pipeline import RULE_SIGNAL_SPEC
from shrap.research.strategy_registry import STATUS_HYPOTHESIS
from shrap.research.strategy_seed.cli import load_specs
from shrap.research.strategy_seed.factor_strategies import COMMON_KILL_CRITERIA
from shrap.research.strategy_seed.spec_strategies import SpecDocumentError, spec_record
from tests.research.test_strategy_seed import FakeRegistry

THESIS = (
    "Large one-day moves in either direction partly reverse the next day because "
    "liquidity providers are paid to absorb them; the magnitude, not the sign, is the signal."
)


def _doc(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "name": "Magnitude shrinkage (1d, bottom 10)",
        "thesis": THESIS,
        "kill_criteria": ["the reversal does not survive one day of execution lag"],
        "params": {"signal": {"feature": "abs_return"}, "select": "bottom", "top_n": 10},
    }
    doc.update(overrides)
    return doc


def test_a_document_becomes_a_hypothesis_root_with_the_common_falsifiers() -> None:
    record = spec_record(_doc())

    assert record.status == STATUS_HYPOTHESIS
    assert record.spec["rule"] == RULE_SIGNAL_SPEC
    assert record.parent_strategy_id is None
    assert record.kill_criteria[0].startswith("the reversal")
    assert record.kill_criteria[1:] == list(COMMON_KILL_CRITERIA)
    assert len(record.tickers["long"]) == 50


def test_the_hash_ignores_the_id_so_a_reload_is_a_duplicate() -> None:
    assert spec_record(_doc()).spec_hash == spec_record(_doc()).spec_hash


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"thesis": "momentum works"}, "thesis"),
        ({"kill_criteria": []}, "kill criterion"),
        ({"params": {"signal": {"feature": "vibes"}}}, "unknown feature"),
        ({"name": ""}, "name"),
    ],
)
def test_bad_documents_are_refused(override: dict[str, Any], message: str) -> None:
    with pytest.raises(SpecDocumentError, match=message):
        spec_record(_doc(**override))


async def test_a_batch_loads_once_and_a_bad_member_refuses_the_whole_file() -> None:
    registry = FakeRegistry()
    text = json.dumps([_doc(), _doc(name="Magnitude shrinkage (1d, bottom 5)")])

    first = await load_specs(registry, text)  # type: ignore[arg-type]
    again = await load_specs(registry, text)  # type: ignore[arg-type]

    assert first.count("loaded:") == 2
    assert again.count("already present") == 2
    assert len(registry.by_id) == 2

    bad = json.dumps([_doc(name="fine but new"), _doc(thesis="short")])
    with pytest.raises(SystemExit, match="refused"):
        await load_specs(registry, bad)  # type: ignore[arg-type]
    assert len(registry.by_id) == 2  # nothing from the refused file was written
