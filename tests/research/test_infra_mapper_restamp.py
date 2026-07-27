"""The seed-evidence repair corrects load-time stamps, touches nothing else,
and is safe to re-run."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from shrap.research.infra_mapper.first_graph import SEED_GRAPH_ID, SEED_NODES
from shrap.research.infra_mapper.restamp import (
    plan_corrections,
    restamp_seed_evidence,
)

# What card 2's loader actually wrote on the Dell: load time, not observation time.
LOAD_TIME = datetime(2026, 7, 27, 18, 30, tzinfo=UTC)


def _row(node: Any, observed_at: datetime, evidence_id: str = "ev-1") -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "graph_id": SEED_GRAPH_ID,
        "ticker": node.ticker,
        "layer_role": node.layer_role,
        "evidence_ref": node.evidence_ref,
        "source_class": node.evidence_source_class,
        "observed_at": observed_at,
    }


def _load_time_rows() -> list[dict[str, Any]]:
    return [_row(n, LOAD_TIME, f"ev-{i}") for i, n in enumerate(SEED_NODES)]


# --- planning -----------------------------------------------------------------


def test_load_time_rows_are_all_corrected_backwards() -> None:
    corrections = plan_corrections(_load_time_rows())

    assert len(corrections) == len(SEED_NODES)
    for correction in corrections:
        assert correction.was == LOAD_TIME
        assert correction.now.year == 2024
        # The repair can only ever make evidence look older.
        assert correction.now < correction.was
        assert correction.days_moved > 0


def test_already_correct_rows_need_no_repair() -> None:
    rows = [_row(n, n.evidence_observed_at, f"ev-{i}") for i, n in enumerate(SEED_NODES)]

    assert plan_corrections(rows) == ()


def test_non_seed_evidence_is_never_touched() -> None:
    # A later card's appended evidence has a different ref and is left alone,
    # so re-running the repair after real evidence lands cannot clobber it.
    node = SEED_NODES[0]
    rows = [
        {
            "evidence_id": "ev-new",
            "graph_id": SEED_GRAPH_ID,
            "ticker": node.ticker,
            "layer_role": node.layer_role,
            "evidence_ref": "Some 2026 announcement recorded by a later card",
            "source_class": "issuer",
            "observed_at": datetime(2026, 7, 5, tzinfo=UTC),
        }
    ]

    assert plan_corrections(rows) == ()


def test_unknown_ticker_is_ignored() -> None:
    rows = [
        {
            "evidence_id": "ev-x",
            "graph_id": SEED_GRAPH_ID,
            "ticker": "CCJ",
            "layer_role": "raw-inputs",
            "evidence_ref": "whatever",
            "source_class": "issuer",
            "observed_at": LOAD_TIME,
        }
    ]

    assert plan_corrections(rows) == ()


# --- applying -----------------------------------------------------------------


class _FakeStore:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.updates: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []

    async def evidence_for_graph(self, graph_id: str) -> list[dict[str, Any]]:
        return self._rows

    async def correct_evidence_observed_at(self, **kwargs: Any) -> bool:
        self.updates.append(kwargs)
        return True

    async def record_history(self, **kwargs: Any) -> None:
        self.history.append(kwargs)


async def test_repair_updates_every_row_and_records_history() -> None:
    store = _FakeStore(_load_time_rows())
    report = await restamp_seed_evidence(store)  # type: ignore[arg-type]

    assert len(store.updates) == len(SEED_NODES)
    # Principle 8: the repair is itself auditable, not a silent rewrite.
    assert len(store.history) == len(SEED_NODES)
    assert all(h["to_status"] == "evidence-date-corrected" for h in store.history)
    assert "load time" in store.history[0]["reason"]
    assert str(len(SEED_NODES)) in report.render()


async def test_dry_run_reports_without_writing() -> None:
    store = _FakeStore(_load_time_rows())
    report = await restamp_seed_evidence(store, dry_run=True)  # type: ignore[arg-type]

    assert len(report.corrections) == len(SEED_NODES)
    assert store.updates == []
    assert store.history == []
    assert report.render().startswith("[dry-run]")


async def test_rerun_after_repair_is_a_no_op() -> None:
    rows = [_row(n, n.evidence_observed_at, f"ev-{i}") for i, n in enumerate(SEED_NODES)]
    store = _FakeStore(rows)
    report = await restamp_seed_evidence(store)  # type: ignore[arg-type]

    assert store.updates == []
    assert store.history == []
    assert "nothing to repair" in report.render()


async def test_repaired_dates_make_the_staleness_pass_see_2024() -> None:
    # The point of the repair: after it, MAX(observed_at) is the true 2024 date,
    # so the staleness pass stops reporting the graph as fresh.
    store = _FakeStore(_load_time_rows())
    await restamp_seed_evidence(store)  # type: ignore[arg-type]

    assert all(u["observed_at"].year == 2024 for u in store.updates)
