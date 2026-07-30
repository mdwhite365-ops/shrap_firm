"""The loop that makes the proposer an agent instead of a command.

#156 shipped the generator as a tools-profile CLI. Tech Watcher filled
``research.literature_items`` hourly and nothing read them, because nothing
invoked the CLI — a funnel whose last stage needs a person typing is a funnel
with a person in it.
"""

from __future__ import annotations

from typing import Any

from shrap.agents.research.hypothesis_generator.config import Settings
from shrap.research.hypothesis_generator.literature import LiteratureItem
from shrap.research.hypothesis_generator.trigger_service import (
    DEFAULT_MAX_ITEMS,
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    sweep,
)


class _FakeLiterature:
    def __init__(self, batches: list[list[LiteratureItem]]) -> None:
        self.batches = batches
        self.limits: list[int] = []

    async def pending(self, limit: int) -> list[LiteratureItem]:
        self.limits.append(limit)
        return self.batches.pop(0) if self.batches else []


class _FakeReport:
    def __init__(self, n: int) -> None:
        self.outcomes = [_FakeOutcome() for _ in range(n)]
        self.proposed: list[object] = []
        self.dry_run = False


class _FakeOutcome:
    gap = None


class _FakeGenerator:
    def __init__(self) -> None:
        self.runs: list[list[LiteratureItem]] = []

    async def run(self, items: Any) -> _FakeReport:
        self.runs.append(list(items))
        return _FakeReport(len(items))


def _item(item_id: str) -> LiteratureItem:
    return LiteratureItem(item_id=item_id, source="arxiv-qfin", title="t", abstract="a")


async def test_a_sweep_runs_the_generator_over_pending_literature() -> None:
    literature = _FakeLiterature([[_item("a"), _item("b")]])
    generator = _FakeGenerator()

    await sweep(generator, literature, 25)  # type: ignore[arg-type]

    assert [i.item_id for i in generator.runs[0]] == ["a", "b"]
    assert literature.limits == [25]


async def test_an_empty_sweep_does_not_call_the_model() -> None:
    """Most days there is no new literature. Reading zero rows and stopping is
    the normal case, and it must not spend a model call to discover that."""

    generator = _FakeGenerator()

    result = await sweep(generator, _FakeLiterature([[]]), 25)  # type: ignore[arg-type]

    assert result is None
    assert generator.runs == []


async def test_the_limit_is_passed_through_so_a_backlog_is_worked_in_batches() -> None:
    literature = _FakeLiterature([[_item("a")]])

    await sweep(_FakeGenerator(), literature, 5)  # type: ignore[arg-type]

    assert literature.limits == [5]


# --- configuration ------------------------------------------------------------


def test_the_service_is_armed_by_default() -> None:
    """Safe because the bound is structural: `hypothesis_key` caps the proposer
    at one lineage root per implemented effect, ever, so registry pollution is
    limited by the size of the scorer library rather than by uptime. What it
    writes is `hypothesis`, which trades nothing."""

    assert Settings(_env_file=None).dry_run is False


def test_the_kill_switch_is_one_env_var() -> None:
    assert Settings(_env_file=None, dry_run=True).dry_run is True


def test_the_defaults_match_the_service() -> None:
    settings = Settings(_env_file=None)

    assert settings.sweep_interval_seconds == DEFAULT_SWEEP_INTERVAL_SECONDS
    assert settings.max_items == DEFAULT_MAX_ITEMS


def test_no_setting_can_widen_what_the_proposer_may_say() -> None:
    """The Evaluator trigger's rule, applied here. A deployment knob that
    relaxed the citation requirement, the identity key or the parameter bounds
    would make "propose until something passes" an env-var change on the
    production box."""

    fields = set(Settings.model_fields)
    forbidden = {
        "require_prior",
        "allow_duplicates",
        "identity_key",
        "lookback_bounds",
        "min_kill_criteria",
        "ir_floor",
        "sharpe_floor",
    }

    assert fields & forbidden == set()


def test_the_dsn_is_not_exposed_in_the_log_snapshot() -> None:
    settings = Settings(_env_file=None)

    assert settings.redacted()["postgres_dsn"] == "***"
