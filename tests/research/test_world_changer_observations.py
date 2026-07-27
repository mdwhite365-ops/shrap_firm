"""The thesis observation log enforces the falsifier link and reports honest
accounting — especially when nothing bears on a kill criterion."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from shrap.research.tech_watcher.observations import (
    BEARING_CONTRADICTS,
    BEARING_NEUTRAL,
    BEARING_SUPPORTS,
    ObservationError,
    render_summary,
    summarize,
    validate_observation,
)
from shrap.research.tech_watcher.observe_cli import (
    STREAM_WORLD_CHANGER_OBSERVED,
    add_observation,
    list_observations,
    parse_observed_at,
)

CRITERIA = [
    "no commercial unit achieves <$60/MWh LCOE by 2027-12",
    "no NRC design certification for a mass-manufactured unit by 2027-12",
    "cumulative built units stay below 10 by 2027-12",
]
WC_ID = "01KXVVPXDMB4HS1QNRPQWRP1RX"


def _obs(
    bearing: str = BEARING_SUPPORTS,
    hard: bool = False,
    kc: int | None = None,
    observed_at: datetime | None = None,
    observation: str = "something happened",
    origin: str = "issuer",
) -> dict[str, Any]:
    return {
        "observation": observation,
        "bearing": bearing,
        "hard": hard,
        "kill_criterion_index": kc,
        "origin": origin,
        "observed_at": observed_at or datetime(2026, 7, 6, tzinfo=UTC),
    }


# --- validation ---------------------------------------------------------------


def test_valid_observation_passes() -> None:
    validate_observation(
        observation="reactor powered an NVIDIA Spark in a demo",
        evidence_ref="Valar Atomics announcement video, 2026-07-06",
        origin="issuer",
        bearing=BEARING_SUPPORTS,
        kill_criterion_index=None,
        kill_criteria=CRITERIA,
    )


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [
        ("observation", {"observation": "   "}),
        ("evidence_ref", {"evidence_ref": ""}),
        ("origin", {"origin": ""}),
    ],
)
def test_empty_required_fields_are_rejected(field: str, kwargs: dict[str, Any]) -> None:
    base: dict[str, Any] = {
        "observation": "x",
        "evidence_ref": "y",
        "origin": "issuer",
        "bearing": BEARING_SUPPORTS,
        "kill_criterion_index": None,
        "kill_criteria": CRITERIA,
    }
    with pytest.raises(ObservationError):
        validate_observation(**{**base, **kwargs})


def test_unknown_bearing_is_rejected() -> None:
    with pytest.raises(ObservationError, match="bearing must be"):
        validate_observation(
            observation="x",
            evidence_ref="y",
            origin="issuer",
            bearing="looks-good",
            kill_criterion_index=None,
            kill_criteria=CRITERIA,
        )


def test_dangling_kill_criterion_index_is_rejected() -> None:
    # A criterion link that points nowhere would fake falsifier coverage.
    with pytest.raises(ObservationError, match="out of range"):
        validate_observation(
            observation="x",
            evidence_ref="y",
            origin="issuer",
            bearing=BEARING_SUPPORTS,
            kill_criterion_index=7,
            kill_criteria=CRITERIA,
        )


def test_no_kill_criterion_link_is_allowed() -> None:
    # Omitting the link is legitimate and is exactly what the summary counts.
    validate_observation(
        observation="x",
        evidence_ref="y",
        origin="issuer",
        bearing=BEARING_SUPPORTS,
        kill_criterion_index=None,
        kill_criteria=CRITERIA,
    )


# --- accounting ---------------------------------------------------------------


def test_summary_counts_bearings_and_hardness() -> None:
    summary = summarize(
        [
            _obs(BEARING_SUPPORTS, hard=True, kc=0),
            _obs(BEARING_CONTRADICTS),
            _obs(BEARING_NEUTRAL),
        ],
        CRITERIA,
    )

    assert (summary.total, summary.supports, summary.contradicts, summary.neutral) == (3, 1, 1, 1)
    assert summary.hard == 1
    assert summary.soft == 2
    assert summary.bearing_on_criteria == 1
    assert summary.criteria_touched == (0,)
    assert summary.criteria_untouched == (1, 2)


def test_narrative_pile_warns_that_it_is_not_validation() -> None:
    # The headline case: lots of supportive soft evidence, no falsifier contact.
    summary = summarize([_obs() for _ in range(5)], CRITERIA)

    assert summary.bearing_on_criteria == 0
    warnings = " ".join(summary.warnings)
    assert "narrative accumulation, not validation" in warnings
    assert "every observation is soft" in warnings
    assert "confirmation pattern" in warnings


def test_falsifier_contact_silences_the_headline_warning() -> None:
    summary = summarize(
        [_obs(hard=True, kc=1), _obs(BEARING_CONTRADICTS, hard=True, kc=0)], CRITERIA
    )

    assert not any("narrative accumulation" in w for w in summary.warnings)
    assert not any("every observation is soft" in w for w in summary.warnings)


def test_empty_log_produces_no_warnings() -> None:
    summary = summarize([], CRITERIA)

    assert summary.total == 0
    assert summary.warnings == ()


def test_render_marks_untouched_criteria() -> None:
    rendered = render_summary("Fission thesis", summarize([_obs(kc=0)], CRITERIA), [_obs(kc=0)])

    assert "UNTOUCHED" in rendered
    assert "bearing on a kill criterion: 1 of 1" in rendered


def test_parse_observed_at_accepts_date_and_assumes_utc() -> None:
    parsed = parse_observed_at("2026-07-06")

    assert parsed.tzinfo is not None
    assert (parsed.year, parsed.month, parsed.day) == (2026, 7, 6)


# --- the CLI path -------------------------------------------------------------


class _FakeStore:
    def __init__(self, thesis: dict[str, Any] | None) -> None:
        self._thesis = thesis
        self.rows: list[dict[str, Any]] = []

    async def get_thesis(self, world_changer_id: str) -> dict[str, Any] | None:
        return self._thesis

    async def insert_observation(self, **kwargs: Any) -> None:
        self.rows.append(kwargs)

    async def observations_for(self, world_changer_id: str) -> list[dict[str, Any]]:
        return self.rows


class _FakeRedis:
    def __init__(self) -> None:
        self.streams: list[str] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.streams.append(stream)
        return "1-0"


def _thesis(kill_criteria: Any = None) -> dict[str, Any]:
    return {
        "candidate_id": WC_ID,
        "name": "Mass-manufactured fission cost-curve crossing",
        "status": "promoted",
        "kill_criteria": CRITERIA if kill_criteria is None else kill_criteria,
    }


async def _add(store: Any, redis: Any, **overrides: Any) -> str:
    kwargs: dict[str, Any] = {
        "world_changer_id": WC_ID,
        "observation": "reactor powered an NVIDIA Spark in a demo",
        "evidence_ref": "Valar Atomics announcement, 2026-07-06",
        "origin": "issuer",
        "bearing": BEARING_SUPPORTS,
        "hard": False,
        "kill_criterion_index": None,
        "observed_at": datetime(2026, 7, 6, tzinfo=UTC),
    }
    kwargs.update(overrides)
    return await add_observation(store, redis, **kwargs)


async def test_add_persists_emits_and_reports() -> None:
    store = _FakeStore(_thesis())
    redis = _FakeRedis()
    out = await _add(store, redis)

    assert len(store.rows) == 1
    assert redis.streams == [STREAM_WORLD_CHANGER_OBSERVED]
    assert "recorded" in out
    # A soft, unlinked observation must say so rather than read as progress.
    assert "narrative accumulation, not validation" in out


async def test_add_against_unknown_thesis_is_rejected() -> None:
    with pytest.raises(ObservationError, match="no world-changer"):
        await _add(_FakeStore(None), _FakeRedis())


async def test_add_writes_nothing_when_validation_fails() -> None:
    store = _FakeStore(_thesis())
    redis = _FakeRedis()
    with pytest.raises(ObservationError, match="out of range"):
        await _add(store, redis, kill_criterion_index=9)

    assert store.rows == []
    assert redis.streams == []


async def test_kill_criteria_arriving_as_json_text_are_parsed() -> None:
    # asyncpg may return JSONB as str; a dangling index must still be caught.
    store = _FakeStore(_thesis(kill_criteria='["a", "b"]'))
    with pytest.raises(ObservationError, match="declares 2 criteria"):
        await _add(store, _FakeRedis(), kill_criterion_index=5)


async def test_list_renders_the_log() -> None:
    store = _FakeStore(_thesis())
    await _add(store, _FakeRedis(), kill_criterion_index=0, hard=True)
    out = await list_observations(store, WC_ID)  # type: ignore[arg-type]

    assert "Mass-manufactured fission" in out
    assert "bearing on a kill criterion: 1 of 1" in out
