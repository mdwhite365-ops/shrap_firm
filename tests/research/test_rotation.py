"""Account rotation recommends from the paired shadow record, and never moves anything."""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from shrap.research.rotation import (
    Recommendation,
    Slot,
    commands,
    paired_edge,
    recommend,
    render,
)

D0 = date(2026, 10, 1)


def _series(active: list[float], bench: float = 0.001) -> list[tuple[date, float, float]]:
    return [(D0 + timedelta(days=i), bench + a, bench) for i, a in enumerate(active)]


def _noise(n: int, mean: float, sd: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(mean, sd) for _ in range(n)]


def test_the_pair_uses_only_sessions_both_settled() -> None:
    a = _series([0.01, 0.02, 0.03, 0.04])
    b = _series([0.0, 0.0, 0.0, 0.0])[1:3]  # settled on days 1 and 2 only

    edge = paired_edge(a, b)

    assert edge.sessions == 2


def test_a_shared_market_move_cancels_in_the_pair() -> None:
    # Two strategies with the same large common noise and a small steady gap:
    # each alone is indistinguishable from zero, the pair is not.
    common = _noise(120, 0.0, 0.02, seed=1)
    a = _series([c + 0.001 for c in common])
    b = _series(common)

    alone = paired_edge(a, None)
    pair = paired_edge(a, b)

    assert alone.z is not None and alone.z < 1.0
    assert pair.z is None or pair.z > 10.0  # zero-variance gap -> no SE, or huge


def test_a_challenger_below_min_sessions_is_not_ranked() -> None:
    strong = _series(_noise(30, 0.01, 0.001, seed=2))

    recs = recommend([Slot("ACC", None)], {"C": strong}, ["C"], min_sessions=60)

    assert not recs[0].swap


def test_an_empty_slot_is_filled_only_above_zero_by_z() -> None:
    good = _series(_noise(80, 0.004, 0.01, seed=3))
    flat = _series(_noise(80, 0.0, 0.01, seed=4))

    recs = recommend([Slot("ACC", None)], {"G": good, "F": flat}, ["F", "G"], z=1.0)

    assert recs[0].challenger_id == "G"


def test_an_incumbent_is_replaced_only_by_a_paired_edge() -> None:
    inc = _series(_noise(80, 0.002, 0.01, seed=5))
    # Better on its own, but not reliably better than the incumbent.
    close = _series([x + 0.0002 for x in _noise(80, 0.002, 0.01, seed=6)])

    keep = recommend([Slot("ACC", "I")], {"I": inc, "C": close}, ["C"], z=1.0)

    assert not keep[0].swap


def test_one_challenger_takes_at_most_one_slot() -> None:
    good = _series(_noise(80, 0.004, 0.01, seed=7))

    recs = recommend([Slot("A1", None), Slot("A2", None)], {"G": good}, ["G"])

    assert [r.challenger_id for r in recs] == ["G", None]


def test_an_incumbent_is_never_its_own_challenger() -> None:
    good = _series(_noise(80, 0.004, 0.01, seed=8))

    recs = recommend([Slot("A1", "G"), Slot("A2", None)], {"G": good}, ["G"])

    assert not any(r.swap for r in recs)


@pytest.mark.parametrize("incumbent", ["INC", None])
def test_the_commands_park_the_incumbent_where_the_ledger_still_sees_it(
    incumbent: str | None,
) -> None:
    rec = Recommendation("ACC", incumbent, "NEW", None, 'paired IR +1.20 "quoted"')

    lines = commands(rec, {"NEW": "New"})

    if incumbent:
        assert "move INC --to kill-review" in lines[0]  # not retired: that leaves the ledger
        assert "assign-account INC --clear" in lines[1]
    assert "assign-account NEW --account-id ACC" in lines[-2]
    assert "move NEW --to paper" in lines[-1]
    assert all(line.count('"') % 2 == 0 for line in lines)


def test_the_report_names_orphaned_positions() -> None:
    rec = Recommendation("ACC", "INC", "NEW", None, "paired IR +1.20")

    text = render([rec], {"INC": "Old", "NEW": "New"}, orphaned={"ACC": ["GLD", "TLT"]})

    assert "SWAP IN New" in text
    assert "GLD, TLT" in text
    assert "Nothing is moved" in text


def test_a_killed_winner_is_reported_as_a_ruling_not_a_command() -> None:
    rec = Recommendation("ACC", None, "DEAD", None, "paired IR +1.50")

    text = render([rec], {"DEAD": "Gross profitability"}, statuses={"DEAD": "killed"})

    assert "SWAP IN Gross profitability" in text
    assert "protocol ruling" in text
    assert "shrap-strategy-stage" not in text
