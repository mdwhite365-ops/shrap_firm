"""The Risk Officer — the firm's single point of veto.

Spec: ``docs/agents/risk-compliance/risk-officer.md``.
Limits: ``docs/risk/policy.md`` (authoritative; a limit change is a PR there).

This package is the portfolio-risk layer that graduates the Pre-Trade Checker
into the agent its own spec called a "Month 1 wire-only Risk Officer stub". The
deterministic per-order rules the stub already enforced — paper-only, universe
and Tier-3 eligibility, velocity guardrails — are unchanged and still live in
``risk_compliance/pre_trade.py``, ``tier3_membership.py`` and ``rate_limit.py``.
What is added here is everything that needs to know about the *book*:

``limits``       the numbers, and regime scaling of them
``exposure``     the current book and the projected one
``clusters``     correlation grouping — "everything is one trade"
``gate``         per-ticker, gross/net and cluster caps on the order path
``sizing``       per-stage sizing, with the absent Kelly posterior marked absent
``switches``     kill-switch decision logic
``switch_store`` that state in Redis, failing closed
``monitor``      daily loss and strategy drawdown, off the order path
``store``        ``risk.decisions`` and ``risk.kill_switches``
"""

from __future__ import annotations

from shrap.risk_compliance.risk_officer.exposure import BookExposure, ExposureUnavailable, Position
from shrap.risk_compliance.risk_officer.gate import PortfolioDecision, check_portfolio
from shrap.risk_compliance.risk_officer.limits import PortfolioLimits, regime_multiplier
from shrap.risk_compliance.risk_officer.monitor import (
    LimitObservation,
    check_daily_loss,
    check_strategy_drawdown,
)
from shrap.risk_compliance.risk_officer.sizing import SizingDecision, size_intent
from shrap.risk_compliance.risk_officer.switches import SwitchBoard, SwitchState

__all__ = [
    "BookExposure",
    "ExposureUnavailable",
    "LimitObservation",
    "PortfolioDecision",
    "PortfolioLimits",
    "Position",
    "SizingDecision",
    "SwitchBoard",
    "SwitchState",
    "check_daily_loss",
    "check_portfolio",
    "check_strategy_drawdown",
    "regime_multiplier",
    "size_intent",
]
