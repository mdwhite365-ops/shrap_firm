"""Regime Router — ADR-0010 §4 strategy activation gate.

A strategy's regime_fit and regime_kill lists determine whether it should be
active (emitting signals) or dormant (suppressed) in the current market regime.

This module is the Regime Router described in docs/regimes/README.md §How the
Regime Router uses profiles (lines 37-47). It is implemented as a library
function called from the Strategy Runner's per-strategy loop, not a standalone
service, to avoid adding another consumer/container for a pure, synchronous
decision.

Per docs/regimes/README.md:

1. Read the classifier's current regime label.
2. For each currently-active strategy, check whether the current regime appears
   in its regime_kill list. If yes, deactivate.
3. For each currently-dormant strategy, check whether the current regime appears
   in its regime_fit list. If yes (and it's not under research hold), activate.
4. For strategies that remain active, apply per-regime size modifiers.
5. Emit a regime.routing.updated event when strategy activation changes.

This module handles steps 2-3: the pure logic that determines active/dormant
status. The Strategy Runner applies it at the entry/exit decision level: a
dormant strategy suppresses new entries (does not force exits).

Reference: docs/decisions/0010-research-department-scope-correction.md,
docs/regimes/README.md, docs/status/known-issues.md (KI-012).
"""

from __future__ import annotations


def is_dormant(
    current_regime: str | None,
    regime_fit: list[str] | None,
    regime_kill: list[str] | None,
) -> bool:
    """Determine if a strategy should be dormant in the current regime.

    Per docs/regimes/README.md, a strategy is dormant if:
    - Its regime_kill list is non-empty AND the current regime is in it.
    - OR its regime_fit list is non-empty AND the current regime is NOT in it.
    - (kill takes precedence over fit if both are set — a regime explicitly in
       kill is unsafe regardless of fit.)

    If both regime_fit and regime_kill are None (or empty), the strategy has no
    opinion and is never dormant — it stays unconditionally active until Mike
    deliberately opts it in via CLI, informed by the strategy's regime card.

    If current_regime is None (no regime classified yet), returns False to fall
    back to today's behavior: no regime signal means informational-only, never
    gating entry. This is aligned with the Risk Officer's own fallback behavior
    (sets regime_multiplier to 0.25, the "unknown" band's low end, but does not
    stop trading).

    Args:
        current_regime: The Regime Classifier's current label (e.g.
            'late-cycle-melt-up', 'crisis-recovery', 'stagflation', 'wartime').
            None if no regime has been classified yet.
        regime_fit: List of regime labels the strategy is expected to perform
            well in. None or empty means "no opinion on fit."
        regime_kill: List of regime labels that should immediately deactivate
            the strategy. None or empty means "no opinion on kill."

    Returns:
        True if the strategy should be dormant in this regime, False otherwise.
    """

    # No regime metadata: unconditionally active (no opinion).
    if not regime_kill and not regime_fit:
        return False

    # No regime classified yet: informational-only fallback.
    if current_regime is None:
        return False

    # kill takes precedence: explicit safety-off in this regime.
    if regime_kill and current_regime in regime_kill:
        return True

    # fit gate: if fit is declared and current regime is not in it, dormant.
    if regime_fit and current_regime not in regime_fit:
        return True

    # All gates passed: strategy is active.
    return False
