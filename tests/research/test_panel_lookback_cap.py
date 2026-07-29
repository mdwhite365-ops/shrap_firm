"""``window_years`` is a cap, not a default (Mike's ruling 2026-07-29).

`_build_panel` requested `window_years * 365` days and nothing older, so a
deeper backfill was silently unread. The momentum runbook instructs
`--since 2018-01-01` and justifies it as buying folds the 127-bar warmup would
otherwise eat — and then the evaluator asked for five years. The doc promised
something the code did not do.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from shrap.research.strategy_evaluator.engine import DEFAULT_WINDOW_YEARS, EvalConfig
from shrap.research.strategy_evaluator.pipeline import (
    DATA_FLOOR,
    EvaluationPipeline,
    _panel_start,
)
from shrap.research.strategy_evaluator.strategy import BarSample

_TODAY = date(2026, 7, 29)


class RecordingReader:
    """Records the window it was asked for, and serves 8 years of bars."""

    def __init__(self, first_bar: date = date(2018, 1, 1)) -> None:
        self.requested: list[tuple[date, date]] = []
        self._first_bar = first_bar

    async def read_bars(
        self, ticker: str, start: date, end: date, adjustment: str
    ) -> list[BarSample]:
        self.requested.append((start, end))
        bars, d = [], max(start, self._first_bar)
        while d <= end:
            if d.weekday() < 5:
                bars.append(BarSample(d, 10.0, 10.0, 10.0, 10.0, 1.0e6))
            d += timedelta(days=1)
        return bars


def _pipeline(config: EvalConfig) -> tuple[EvaluationPipeline, RecordingReader]:
    reader = RecordingReader()
    dummy = object()
    pipeline = EvaluationPipeline(
        registry=dummy,  # type: ignore[arg-type]
        reader=reader,  # type: ignore[arg-type]
        store=dummy,  # type: ignore[arg-type]
        publisher=dummy,  # type: ignore[arg-type]
        config=config,
    )
    return pipeline, reader


# --- the start-date rule -----------------------------------------------------


def test_no_cap_requests_everything_the_store_holds() -> None:
    assert _panel_start(None, _TODAY) == DATA_FLOOR


def test_a_cap_still_limits_the_lookback() -> None:
    """The knob still works — it is what to pass to deliberately restrict a run
    to a recent window, e.g. to compare against an earlier five-year result."""

    assert _panel_start(5, _TODAY) == _TODAY - timedelta(days=5 * 365)
    assert _panel_start(1, _TODAY) == _TODAY - timedelta(days=365)


def test_the_floor_is_readable_rather_than_date_min() -> None:
    """`date.min` is year 1. In a query plan or a log line that reads as a bug;
    a 1970 floor reads as a floor."""

    assert DATA_FLOOR == date(1970, 1, 1)
    assert DATA_FLOOR < date(2018, 1, 1)


def test_the_default_config_sets_no_cap() -> None:
    """This is the ruling. A default of 5 is what discarded the backfill."""

    assert EvalConfig().window_years is None


# --- what the pipeline actually asks the store for ---------------------------


async def test_the_deeper_backfill_is_finally_read() -> None:
    """The runbook's `--since 2018-01-01` buys ~8.5 years; the old default read
    five of them and left the rest in the table."""

    pipeline, reader = _pipeline(EvalConfig())

    panel, _ = await pipeline._build_panel(["SPY", "QQQ"], _TODAY)

    assert reader.requested[0] == (DATA_FLOOR, _TODAY)
    assert panel.dates[0] == date(2018, 1, 1)
    # Comfortably past the ~1,258 bars a five-year window allowed.
    assert panel.n_bars > 2000


async def test_an_explicit_cap_is_honoured_end_to_end() -> None:
    pipeline, reader = _pipeline(EvalConfig(window_years=DEFAULT_WINDOW_YEARS))

    panel, _ = await pipeline._build_panel(["SPY"], _TODAY)

    assert reader.requested[0] == (_TODAY - timedelta(days=5 * 365), _TODAY)
    assert panel.dates[0] >= _TODAY - timedelta(days=5 * 365)


async def test_every_ticker_is_asked_for_the_same_window() -> None:
    """A per-ticker difference would make the panel's raggedness an artefact of
    the fetch rather than of when the names listed."""

    pipeline, reader = _pipeline(EvalConfig())

    await pipeline._build_panel(["SPY", "QQQ", "NVDA"], _TODAY)

    assert len(reader.requested) == 3
    assert len(set(reader.requested)) == 1


# --- the config record -------------------------------------------------------


def test_the_absent_cap_is_recorded_on_the_evaluation() -> None:
    """`config` is persisted with every evaluation so a verdict is reproducible.
    'No cap' has to survive that round trip as a distinct value from any number."""

    assert EvalConfig().as_dict()["window_years"] is None
    assert EvalConfig(window_years=5).as_dict()["window_years"] == 5


@pytest.mark.parametrize("years", [1, 5, 20])
def test_a_cap_never_reaches_further_back_than_no_cap(years: int) -> None:
    assert _panel_start(years, _TODAY) > _panel_start(None, _TODAY)
