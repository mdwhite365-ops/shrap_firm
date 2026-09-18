"""Opening the intraday gate, and the way opening it could have gone wrong.

Until #234 the Runner held one bar reader, so `intraday returns` was correctly
`missing-data` — the firm could not run such an effect at all. #234 wired the
reader, #236 keeps the bars fresh, and the input became reachable.

**Reachable brought a new way to be silently wrong.** An effect measured over
fifteen minutes, admitted as expressible and then built at a daily grain, is a
six-month ranking citing a within-day paper. Every number is well-formed. So the
gate admits an intraday input ONLY alongside a declared cadence, and the spec
carries that cadence through to the engine with the horizon converted from
sessions to bars.

The module's standing bias is unchanged: admit a WORDING difference, never a
CONSTRUCTION difference.
"""

from __future__ import annotations

import pytest

from shrap.research.hypothesis_generator.expressible import (
    OUTCOME_EXPRESSIBLE,
    OUTCOME_MISSING_DATA,
    classify,
    missing_inputs,
    needs_intraday,
    normalise_input,
)

MOMENTUM = "cross-sectional-momentum"


# --- vocabulary: wordings in, constructions out ------------------------------


@pytest.mark.parametrize(
    "name,series",
    [
        ("intraday returns", "close"),
        ("intraday prices", "close"),
        ("intraday closing price", "close"),
        ("intraday volume", "volume"),
        ("5-minute closes", "close"),
        ("15 minute returns", "close"),
        ("1-minute volume", "volume"),
    ],
)
def test_an_intraday_wording_resolves_to_its_series(name: str, series: str) -> None:
    """The interval is a strategy parameter, not a question about the data.

    The backfill stores whatever grain it is asked for, so `5-minute` and
    `15-minute` are the same availability question with different answers to a
    different question.
    """

    assert normalise_input(name) == series
    assert needs_intraday([name]) is True


@pytest.mark.parametrize(
    "name",
    [
        "realised variance from 5-minute returns",
        "intraday high",
        "intraday bars",
        "signed order flow",
        "tick data",
        "quote midpoint",
        "order imbalance",
    ],
)
def test_a_construction_or_a_missing_series_stays_out_of_reach(name: str) -> None:
    """The bias to drop, unchanged.

    `realised variance from intraday returns` is a construction the firm has no
    scorer for — there is no path that reads an intraday series and emits a
    daily one. `intraday high` names a series the panel does not expose at any
    grain: PanelWindow gives closes and volumes, never highs or lows. Admitting
    either would produce a strategy that silently implements a different effect
    from the one it cites.
    """

    assert normalise_input(name) is None
    assert missing_inputs([name]) == (name,)


def test_a_daily_wording_is_not_marked_intraday() -> None:
    assert needs_intraday(["close", "volume", "daily returns"]) is False


# --- the gate ----------------------------------------------------------------


def test_an_intraday_effect_with_a_cadence_is_expressible() -> None:
    assert classify(MOMENTUM, None, ["intraday returns"], 15) == OUTCOME_EXPRESSIBLE


def test_an_intraday_effect_without_a_cadence_is_not() -> None:
    """THE central test of this card.

    Admitting this would build a within-day effect over six months of daily
    bars, citing the paper that measured it over fifteen minutes. It runs, it
    ranks, it trades, and nothing raises. Recorded as a capability gap instead —
    which is the honest answer, because the firm cannot build what the proposal
    failed to specify.
    """

    assert classify(MOMENTUM, None, ["intraday returns"], None) == OUTCOME_MISSING_DATA


def test_a_daily_effect_is_unaffected_by_any_of_this() -> None:
    """Nine of nine literature items to date named daily series only."""

    assert classify(MOMENTUM, None, ["close", "volume"], None) == OUTCOME_EXPRESSIBLE


def test_a_genuinely_missing_input_outranks_the_cadence_question() -> None:
    """Data first, as the module's docstring says: a missing series is reported
    as missing data whether or not a cadence was declared."""

    assert classify(MOMENTUM, None, ["signed order flow"], 15) == OUTCOME_MISSING_DATA


# --- the spec the gate lets through ------------------------------------------


def _proposal(**kw: object):  # type: ignore[no-untyped-def]
    from shrap.research.hypothesis_generator.proposer import Prior, RawProposal

    defaults: dict[str, object] = {
        "item_id": "item-1",
        "is_market_effect": True,
        "reason": "r",
        "effect_name": "test-effect",
        "prior": Prior(authors="A", year=2020, claim="c"),
        "rule": MOMENTUM,
        "factor": None,
        "lookback": 126,
        "cadence_minutes": None,
        "required_inputs": ("close",),
        "scorer_sketch": "s",
        "deviation": "none",
        "kill_criteria": ("k1", "k2"),
        "thesis": "t",
        "model": "test-model",
    }
    defaults.update(kw)
    return RawProposal(**defaults)  # type: ignore[arg-type]


def _spec(**kw: object) -> dict:
    from shrap.research.hypothesis_generator.literature import LiteratureItem
    from shrap.research.hypothesis_generator.record import build_spec

    item = LiteratureItem(
        item_id="item-1",
        source="arxiv",
        category="q-fin",
        title="T",
        abstract="A",
        url="http://x",
        authors=("A",),
    )
    return build_spec(_proposal(**kw), item)


def test_a_daily_proposal_writes_no_cadence_and_no_conversion() -> None:
    """Every proposal already in the registry must hash exactly as before."""

    spec = _spec()
    assert "cadence" not in spec
    assert spec["params"]["lookback"] == 126


def test_an_intraday_proposal_carries_its_cadence_to_the_engine() -> None:
    """Without this, #234 resolves the daily reader and the grain is lost."""

    spec = _spec(cadence_minutes=15, required_inputs=("intraday returns",))
    assert spec["cadence"] == {"kind": "intraday", "interval_minutes": 15}


def test_the_horizon_is_converted_from_sessions_to_bars() -> None:
    """The proposer states sessions; the engine counts bars. 26x at 15 minutes.

    Passing 126 straight through would form the ranking over 126 bars — under
    five sessions — implementing a short-horizon effect the literature says
    REVERSES, while citing a six-month paper.
    """

    spec = _spec(cadence_minutes=15, required_inputs=("intraday returns",))
    assert spec["params"]["lookback"] == 126 * 26 == 3276
    assert spec["params"]["skip"] > 0


def test_bounds_scale_with_the_horizons_they_bound() -> None:
    spec = _spec(cadence_minutes=15, required_inputs=("intraday returns",))
    lo, hi = spec["param_bounds"]["lookback"]
    assert lo <= spec["params"]["lookback"] <= hi
    assert spec["param_bounds"]["top_n"] == [1.0, 50.0]  # a count, never scaled


# --- parsing -----------------------------------------------------------------


def _parse(payload: dict):  # type: ignore[no-untyped-def]
    import json

    from shrap.research.hypothesis_generator.literature import LiteratureItem
    from shrap.research.hypothesis_generator.proposer import parse_proposal

    item = LiteratureItem(
        item_id="i",
        source="arxiv",
        category="q-fin",
        title="T",
        abstract="A",
        url="u",
        authors=("A",),
    )
    base = {
        "is_market_effect": True,
        "reason": "r",
        "effect_name": "e",
        "prior": {"authors": "A", "year": 2020, "claim": "c"},
        "rule": MOMENTUM,
        "factor": None,
        "lookback": 126,
        "required_inputs": ["close"],
        "scorer_sketch": "s",
        "deviation": "none",
        "kill_criteria": ["a"],
        "thesis": "t",
    }
    base.update(payload)
    return parse_proposal(item, json.dumps(base), "m")


def test_a_declared_cadence_is_parsed() -> None:
    assert _parse({"cadence_minutes": 15}).cadence_minutes == 15


def test_an_absent_cadence_is_none_not_a_default() -> None:
    assert _parse({}).cadence_minutes is None
    assert _parse({"cadence_minutes": None}).cadence_minutes is None


@pytest.mark.parametrize("value", [0, -5, 391, 10000, "fifteen", 1.5e400])
def test_an_out_of_range_cadence_is_refused_not_clamped(value: object) -> None:
    """Clamping would attach a decision frequency nobody proposed to a paper
    that proposed a different one — and `classify` reads None as "no grain
    declared", so an intraday proposal lands in the gap queue instead of being
    built at the wrong horizon."""

    assert _parse({"cadence_minutes": value}).cadence_minutes is None


def test_the_prompt_version_was_bumped() -> None:
    """The provenance stamp is how a later review knows which prompt ran."""

    from shrap.research.hypothesis_generator.proposer import PROPOSER_PROMPT_VERSION

    assert PROPOSER_PROMPT_VERSION >= 3
