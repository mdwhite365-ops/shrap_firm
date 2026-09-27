"""The first spec batches: accounting features, the equities universe, and the files.

Two faults this card exists to prevent. A strategy over filed figures must see a
figure only from the day it was filed, and must be measured against the names it
could actually hold: registered over the launch list, a company-only strategy was
benchmarked against bond, gold, dollar and crypto funds too, and scored about 0.3
of IR higher for it (2026-09-24 dry run).
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from shrap.market_data.fundamentals_backfill import fund_tickers
from shrap.research import shadow_ledger_cli
from shrap.research.strategy_evaluator.signals import SignalSpecError, evaluate, parse
from shrap.research.strategy_evaluator.strategy import BarSample, PricePanel
from shrap.research.strategy_seed.spec_strategies import (
    SpecDocumentError,
    load_documents,
    spec_record,
    universe_tickers,
)

START = date(2025, 1, 1)
SPECS = Path(__file__).resolve().parents[2] / "docs" / "strategies" / "specs"
THESIS = (
    "High book-to-market stocks earn higher average returns than low ones (Fama & "
    "French 1992); book equity is the latest filed stockholders' equity."
)


def _bars(n: int, close: float = 10.0) -> list[BarSample]:
    return [
        BarSample(
            session_date=START + timedelta(days=i),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1_000.0,
        )
        for i in range(n)
    ]


def _panel() -> PricePanel:
    # A files day 3 (FY ending day -300) and again day 6; B files day 6 only.
    fundamentals = {
        "A": {
            "total_assets": [
                (START + timedelta(days=3), START - timedelta(days=300), 100.0),
                (START + timedelta(days=6), START + timedelta(days=65), 150.0),
            ]
        },
        "B": {"total_assets": [(START + timedelta(days=6), START + timedelta(days=65), 80.0)]},
    }
    return PricePanel.from_bars({"A": _bars(10), "B": _bars(10)}, None, fundamentals)


def test_a_filed_figure_is_invisible_before_its_filing_date() -> None:
    node = parse({"feature": "fundamental", "metric": "total_assets"})
    panel = _panel()

    assert evaluate(node, panel.window(2), ["A", "B"]) == {"A": None, "B": None}
    assert evaluate(node, panel.window(3), ["A", "B"]) == {"A": 100.0, "B": None}
    assert evaluate(node, panel.window(9), ["A", "B"]) == {"A": 150.0, "B": 80.0}


def test_growth_compares_with_the_year_before_as_visible_then() -> None:
    node = parse({"feature": "fundamental_growth", "metric": "total_assets"})
    scores = evaluate(node, _panel().window(9), ["A", "B"])

    assert scores["A"] == pytest.approx(0.5)
    assert scores["B"] is None  # no prior year on file


@pytest.mark.parametrize(
    "raw",
    [
        {"feature": "fundamental", "metric": "ebitda_adjusted"},
        {"feature": "fundamental", "metric": "revenue", "lookback": 5},
    ],
)
def test_an_accounting_feature_takes_a_known_metric_and_nothing_else(raw: Any) -> None:
    with pytest.raises(SignalSpecError):
        parse(raw)


def test_the_equities_universe_is_the_launch_list_without_the_funds() -> None:
    launch, equities = universe_tickers("launch"), universe_tickers("equities")

    assert set(equities) == set(launch) - fund_tickers()
    assert not set(equities) & {"GLD", "UUP", "IBIT", "ETHA"}


def _doc(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "name": "Book-to-market",
        "thesis": THESIS,
        "kill_criteria": ["the edge is confined to one fold"],
        "params": {
            "signal": {
                "op": "div",
                "args": [
                    {"feature": "fundamental", "metric": "stockholders_equity"},
                    {"feature": "market_cap"},
                ],
            },
            "select": "top",
            "top_n": 10,
            "rebalance": "monthly",
        },
    }
    doc.update(overrides)
    return doc


def test_the_universe_is_registered_and_changes_the_hash() -> None:
    launch = spec_record(_doc())
    equities = spec_record(_doc(universe="equities"))

    assert equities.tickers["long"] == universe_tickers("equities")
    assert launch.tickers["long"] == universe_tickers("launch")
    # Different benchmark, different hypothesis: loading both writes two rows.
    assert equities.spec_hash != launch.spec_hash


def test_an_unknown_universe_is_refused() -> None:
    with pytest.raises(SpecDocumentError, match="unknown universe"):
        spec_record(_doc(universe="sp500"))


@pytest.mark.parametrize("path", sorted(SPECS.glob("batch-*.json")), ids=lambda p: p.name)
def test_every_committed_batch_validates(path: Path) -> None:
    docs = load_documents(path.read_text())
    records = [spec_record(d) for d in docs]

    assert records
    assert len({r.spec_hash for r in records}) == len(records)


def test_every_spec_over_company_data_is_registered_over_companies() -> None:
    # market_cap is nan for the funds and filed figures are absent, so such a
    # spec can never hold one — it must not be benchmarked against them either.
    for path in sorted(SPECS.glob("batch-*.json")):
        for doc in load_documents(path.read_text()):
            text = str(doc["params"]["signal"])
            if "fundamental" in text or "market_cap" in text:
                assert doc.get("universe") == "equities", doc["name"]


class _Reader:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def read_bars(self, ticker: str, start: date, end: date, adj: str) -> list[BarSample]:
        return _bars(5)

    async def read_shares(self, tickers: list[str]) -> dict[str, Any]:
        self.asked.append("shares")
        return {}

    async def read_fundamentals(self, tickers: list[str]) -> dict[str, Any]:
        self.asked.append("fundamentals")
        return {"A": {"revenue": [(START, START - timedelta(days=90), 7.0)]}}


def test_the_shadow_ledger_panel_carries_filed_figures() -> None:
    reader = _Reader()
    panel = asyncio.run(
        shadow_ledger_cli._panel(reader, ["A", "B"], START + timedelta(days=4))  # type: ignore[arg-type]
    )

    assert reader.asked == ["shares", "fundamentals"]
    assert panel.window(4).fundamental("A", "revenue") == 7.0
