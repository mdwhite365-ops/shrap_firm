"""Wiring + seam-integration tests for the Strategy Runner service.

- Settings defaults are conservative and start at new events.
- pyproject script/extra, docker-compose service, and Dockerfile CMD are wired.
- The active-paper stage set excludes ``hypothesis`` (un-evaluated strategies
  must never reach the trading path).
- The reused reference factory seam actually produces a buy end to end, so the
  deferred authoring card upgrades the runner for free.
"""

from __future__ import annotations

import tomllib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from shrap.agents.research.strategy_runner.config import Settings
from shrap.agents.research.strategy_runner.runner import ACTIVE_PAPER_STAGES
from shrap.research.strategy_evaluator.pipeline import _default_strategy_factory, _extract_tickers
from shrap.research.strategy_evaluator.strategy import BarSample
from shrap.research.strategy_registry import STATUS_HYPOTHESIS, STATUS_PAPER, StrategyRecord
from shrap.research.strategy_runner.engine import (
    SIDE_BUY,
    RunnerSignalConfig,
    StrategyInput,
    plan_session,
)
from shrap.trading_floor.decision_maker_stub import DEFAULT_CONFIDENCE_THRESHOLD


def test_settings_defaults_are_conservative_and_start_new() -> None:
    settings = Settings()
    assert settings.service_name == "strategy-runner"
    assert settings.start_id == "$"  # only new phase events
    assert settings.max_quantity == 100  # must track the Pre-Trade cap; see engine.py
    assert settings.confidence > DEFAULT_CONFIDENCE_THRESHOLD
    assert settings.adjustment == "all"
    config = settings.signal_config()
    assert isinstance(config, RunnerSignalConfig)
    assert settings.redacted()["postgres_dsn"] == "***"


def test_active_paper_stages_exclude_hypothesis() -> None:
    assert STATUS_PAPER in ACTIVE_PAPER_STAGES
    assert STATUS_HYPOTHESIS not in ACTIVE_PAPER_STAGES  # never trade un-evaluated strategies


def test_pyproject_and_infra_are_wired() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    assert pyproject["project"]["scripts"]["shrap-strategy-runner"] == (
        "shrap.agents.research.strategy_runner.__main__:main"
    )
    assert "strategy-runner" in pyproject["project"]["optional-dependencies"]

    compose = Path("infra/docker-compose.yml").read_text()
    assert "strategy-runner:" in compose
    assert "container_name: shrap_strategy_runner" in compose
    assert "STRATEGY_RUNNER_POSTGRES_DSN" in compose
    assert "STRATEGY_RUNNER_MAX_QUANTITY" in compose
    assert "STRATEGY_RUNNER_ALPACA" not in compose  # PAPER ONLY: no broker creds

    dockerfile = Path("infra/strategy-runner.Dockerfile").read_text()
    assert 'CMD ["shrap-strategy-runner"]' in dockerfile


def test_reference_factory_seam_emits_a_buy_end_to_end() -> None:
    # Upward-trending closes: fast MA (2) > slow MA (3) => long-only target 1.0.
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    bars = [
        BarSample(
            session_date=date(2026, 1, 1) + timedelta(days=i),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=1000.0,
        )
        for i, c in enumerate(closes)
    ]
    record = StrategyRecord(
        strategy_id="01STRATREF",
        name="reference-ma-crossover",
        version=1,
        archetype="infra-graph-play",
        status=STATUS_PAPER,
        source="test",
        thesis="test",
        anchor=None,
        tickers={"long": ["NVDA"]},
        spec={"params": {"fast": 2, "slow": 3, "target_weight": 1.0}},
        spec_hash="hash-ref",
        regime_sizing_modifier=None,
        kill_criteria=["md>0.5"],
        code_ref=None,
        created_at=None,
        updated_at=None,
    )
    tickers = _extract_tickers(record.tickers)
    item = StrategyInput(record=record, tickers=tickers, bars_by_ticker={"NVDA": bars})

    plans = plan_session(
        session_date=date(2026, 7, 24),
        now=datetime(2026, 7, 24, 14, 30, tzinfo=UTC),
        strategies=[item],
        stored_state={},
        factory=_default_strategy_factory,
        config=RunnerSignalConfig(max_quantity=1_000_000),
        regime_label="risk-on",
        equity=10_000.0,
        account_id="PA3TESTACCT",
    )
    (plan,) = plans
    assert not plan.skipped
    assert [(s.side, s.ticker) for s in plan.signals] == [(SIDE_BUY, "NVDA")]
    # Sized against equity through the real factory seam: fully weighted into a
    # $5 close is 2,000 shares.
    assert plan.signals[0].payload["quantity"] == 2_000
    # The routing key rides on the signal: without it no Execution Agent claims
    # the resulting intent.
    assert plan.signals[0].payload["account_id"] == "PA3TESTACCT"
