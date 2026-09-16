"""Unit tests for the Regime Router gate (ADR-0010 §4).

:func:`shrap.research.strategy_runner.regime_router.is_dormant` is a pure
function, so the whole truth table is cheap to pin down. The cases that matter
most are the *absent* ones: a strategy with no regime metadata must behave
exactly as it did before the Router existed, because every strategy in the
registry today carries ``None``/``None`` and none of them may change behavior
on merge.
"""

from __future__ import annotations

import pytest

from shrap.research.strategy_runner.regime_router import is_dormant

MELT_UP = "late-cycle-melt-up"
RECOVERY = "crisis-recovery"
STAGFLATION = "stagflation"
WARTIME = "wartime"


# --- no opinion: today's behavior, unchanged ----------------------------------


@pytest.mark.parametrize("regime", [MELT_UP, WARTIME, None])
def test_no_regime_metadata_is_never_dormant(regime: str | None) -> None:
    """Both lists unset means "no opinion" — the pre-Router behavior."""
    assert is_dormant(regime, None, None) is False


@pytest.mark.parametrize("regime", [MELT_UP, WARTIME, None])
def test_empty_lists_are_treated_as_no_opinion(regime: str | None) -> None:
    """Empty is not "fits nothing" — it is the same as unset.

    Otherwise a strategy written with ``regime_fit=[]`` would be dormant in
    every regime forever, which is a silent kill rather than a declaration.
    """
    assert is_dormant(regime, [], []) is False


# --- kill list ----------------------------------------------------------------


def test_kill_match_is_dormant() -> None:
    assert is_dormant(WARTIME, None, [WARTIME]) is True


def test_kill_miss_is_active() -> None:
    """Kill-only metadata gates nothing outside the named regimes."""
    assert is_dormant(MELT_UP, None, [WARTIME]) is False


# --- fit list -----------------------------------------------------------------


def test_fit_match_is_active() -> None:
    assert is_dormant(MELT_UP, [MELT_UP, RECOVERY], None) is False


def test_fit_miss_is_dormant() -> None:
    """A declared fit list is exhaustive: a regime not named is not fit."""
    assert is_dormant(STAGFLATION, [MELT_UP, RECOVERY], None) is True


# --- both set -----------------------------------------------------------------


def test_kill_takes_precedence_over_fit() -> None:
    """Contradictory metadata resolves to the safe side.

    A label appearing in both lists should not happen, but if it does the
    explicit "unsafe here" wins over the "works here".
    """
    assert is_dormant(WARTIME, [WARTIME], [WARTIME]) is True


def test_both_set_and_regime_in_neither_is_dormant() -> None:
    """Fit is the binding constraint once declared, kill or no kill."""
    assert is_dormant(STAGFLATION, [MELT_UP], [WARTIME]) is True


def test_both_set_and_regime_only_in_fit_is_active() -> None:
    assert is_dormant(MELT_UP, [MELT_UP], [WARTIME]) is False


# --- no regime classified -----------------------------------------------------


@pytest.mark.parametrize(
    ("fit", "kill"),
    [
        ([MELT_UP], None),
        (None, [WARTIME]),
        ([MELT_UP], [WARTIME]),
    ],
)
def test_unknown_regime_never_gates(fit: list[str] | None, kill: list[str] | None) -> None:
    """No label means no gate, even for a strategy that declared one.

    The Router cannot decide dormancy without knowing the regime, and failing
    closed would silently halt every regime-tagged strategy the moment the
    classifier is unavailable. The Risk Officer already handles absent regime
    by sizing down to the ``unknown`` band rather than refusing to trade; this
    matches that posture.
    """
    assert is_dormant(None, fit, kill) is False
