"""The daily path must not need an exchange-calendar library to import.

#217 added `is_regular_hours` to `operations.market_phase` and had the
Evaluator's store import it at module scope. `market_phase` imported
pandas-market-calendars at module scope in turn, and the `strategy-evaluator`
extra does not ship that package — so on the Dell:

    ModuleNotFoundError: No module named 'pandas_market_calendars'

raised at import of `shrap-strategy-evaluate`, which took down **daily**
evaluation as well. A module-level import in a file the daily path merely passes
through was enough to break a path that never reads a calendar.

The fix is to import the library inside the two functions that read a calendar.
These tests pin that, by importing with the package made unavailable — which is
the only way to catch it in an environment where the package happens to be
installed.
"""

from __future__ import annotations

import builtins
import importlib
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest


@pytest.fixture
def without_calendar_library() -> Iterator[None]:
    """Make ``import pandas_market_calendars`` raise, as it does in that image."""

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pandas_market_calendars":
            raise ModuleNotFoundError("No module named 'pandas_market_calendars'")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = fake_import
    try:
        yield
    finally:
        builtins.__import__ = real_import


def test_market_phase_imports_without_the_calendar_library(
    without_calendar_library: None,
) -> None:
    """The module's phase model and constants are pure Python."""

    module = importlib.reload(importlib.import_module("shrap.operations.market_phase"))

    assert module.DEFAULT_CALENDAR == "XNYS"
    assert module.Phase.OPEN == "open"


def test_the_evaluator_store_imports_without_the_calendar_library(
    without_calendar_library: None,
) -> None:
    """The exact import chain that failed on the Dell.

    store.py -> operations.market_phase -> pandas_market_calendars
    """

    module = importlib.reload(importlib.import_module("shrap.research.strategy_evaluator.store"))

    assert hasattr(module, "PostgresEvaluatorReader")
    assert hasattr(module, "SELECT_DAILY_BARS_SQL")


def test_the_evaluator_cli_imports_without_the_calendar_library(
    without_calendar_library: None,
) -> None:
    """`shrap-strategy-evaluate --help` must work in an image without it."""

    module = importlib.reload(importlib.import_module("shrap.research.strategy_evaluator.cli"))

    args = module._build_parser().parse_args(["--strategy-id", "01STRAT"])
    assert args.timeframe == module.TIMEFRAME_DAILY


def test_pure_helpers_still_work_without_the_calendar_library(
    without_calendar_library: None,
) -> None:
    """`is_regular_hours` takes bounds as an argument and reads no calendar."""

    from shrap.operations.market_phase import is_regular_hours

    bounds: dict[date, tuple[Any, Any]] = {}
    from datetime import UTC, datetime

    assert not is_regular_hours(datetime(2026, 9, 15, 14, 0, tzinfo=UTC), bounds)


def test_reading_a_calendar_still_needs_the_library(
    without_calendar_library: None,
) -> None:
    """Deferred, not removed — the failure moves to the call that actually needs it.

    Asserted so nobody "fixes" a future ImportError by deleting the dependency:
    intraday evaluation genuinely requires it, which is why it was added to the
    strategy-evaluator extra rather than only deferred.
    """

    from shrap.operations.market_phase import regular_session_bounds

    with pytest.raises(ModuleNotFoundError, match="pandas_market_calendars"):
        regular_session_bounds(date(2026, 9, 15), date(2026, 9, 15))


def test_modules_are_restored_for_the_rest_of_the_suite() -> None:
    """The reloads above must not leave a half-imported module behind."""

    from shrap.operations.market_phase import regular_session_bounds

    bounds = regular_session_bounds(date(2026, 9, 15), date(2026, 9, 15))
    assert date(2026, 9, 15) in bounds
