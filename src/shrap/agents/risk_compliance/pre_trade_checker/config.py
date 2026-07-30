"""Pre-Trade Checker service settings."""

from __future__ import annotations

import socket
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shrap.research.universe_curator.launch_list import LAUNCH_LIST
from shrap.risk_compliance.pre_trade import RiskPolicy
from shrap.risk_compliance.rate_limit import RateLimitConfig
from shrap.risk_compliance.risk_officer.limits import PortfolioLimits

_DEFAULT_REDIS_URL = "redis" + "://" + "redis" + ":6379/0"

# The static allowlist is the *interim* universe gate. ADR-0012 makes Tier 3
# (``research.universe_tiers``) authoritative, and flipping
# ``tier3_enforcement`` disables this list entirely (see ``couple_universe_gate``).
# Until that flip — which requires the Curator's ``load-launch-list`` to have
# populated the table, since Tier 3 fails closed on an empty one — this list is
# *imported* from the single launch-list definition rather than copied into an
# env var. A copy would be free to drift from the universe the Evaluator scores
# strategies against; an import cannot.
_DEFAULT_ALLOWED_UNIVERSE = ",".join(name.ticker for name in LAUNCH_LIST)


def _default_postgres_dsn() -> str:
    return "postgresql://shrap:shrap@postgres:5432/shrap"


class Settings(BaseSettings):
    """Configuration loaded from PRE_TRADE_CHECKER_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="PRE_TRADE_CHECKER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "pre-trade-checker"
    instance_id: str = Field(default_factory=socket.gethostname)
    redis_url: str = _DEFAULT_REDIS_URL
    allowed_universe: str | list[str] = _DEFAULT_ALLOWED_UNIVERSE

    # Per-order share cap, sized for a $10,000 aggressive paper book. A 10% slot
    # is $1,000, which buys 100 shares at $10 — so this binds only on names under
    # roughly $10 and is not the effective position limit. Notional sizing (the
    # Runner) is; this is the backstop against a sizing bug becoming a huge order.
    #
    # MUST equal STRATEGY_RUNNER_MAX_QUANTITY. This checker *clamps* rather than
    # vetoes, so a Runner sized above this cap would record an intent larger than
    # the fill and its later exit would oversell.
    # ``test_the_production_default_matches_the_pre_trade_cap`` asserts the pair.
    max_quantity_per_order: int = 100
    kill_switch_active: bool = False

    # Firm-wide daily approvals. A 50-name universe entered from flat is 50
    # orders; 80 leaves headroom for same-day rotation without leaving a runaway
    # signal loop unbounded.
    max_orders_per_day: int = 80
    symbol_cooldown_seconds: int = 300
    # Tier 3 membership enforcement (ADR-0012). Default off: nothing populates
    # research.universe_tiers until the Universe Curator's first card lands and
    # DQ-004 locks the launch list — enforcing against an empty table would
    # reject every order, including the live smoke path. Flipping this on is an
    # explicit human decision; once on, unavailable tier state fails closed.
    tier3_enforcement: bool = False
    postgres_dsn: SecretStr = Field(default_factory=lambda: SecretStr(_default_postgres_dsn()))
    tier3_cache_ttl_seconds: float = 30.0
    start_id: str = "0-0"
    count: int = 100
    block_ms: int = 5000
    retry_delay_seconds: float = 1.0
    log_level: str = "INFO"

    # --- Risk Officer portfolio layer ----------------------------------------
    #
    # Default OFF, on the Tier-3 precedent above. The portfolio gate reads
    # `ops.position_snapshots`, which nothing wrote before this card — enabling
    # it against an unpopulated table fails closed and vetoes every order,
    # including the live smoke path. Turning it on is an explicit human decision
    # taken once the Reconciliation Agent has run at least one pass with the new
    # positions fetch.
    portfolio_limits_enforcement: bool = False
    monitor_interval_seconds: float = 300.0

    # Limits. Defaults mirror `docs/risk/policy.md` v0.1, which is authoritative
    # — these exist so an operator can tighten one without a deploy, not so the
    # numbers can drift. `test_config_defaults_match_the_policy_doc` pins them.
    max_ticker_weight: float = 0.20
    max_gross_exposure: float = 1.00
    max_net_exposure: float = 1.00
    max_cluster_weight: float = 0.15
    max_daily_loss: float = 0.02
    max_strategy_drawdown: float = 0.25
    correlation_threshold: float = 0.80
    min_cluster_history: int = 40

    def portfolio_limits(self) -> PortfolioLimits:
        """Build the portfolio limits from config."""

        return PortfolioLimits(
            max_ticker_weight=self.max_ticker_weight,
            max_gross_exposure=self.max_gross_exposure,
            max_net_exposure=self.max_net_exposure,
            max_cluster_weight=self.max_cluster_weight,
            max_daily_loss=self.max_daily_loss,
            max_strategy_drawdown=self.max_strategy_drawdown,
            correlation_threshold=self.correlation_threshold,
            min_cluster_history=self.min_cluster_history,
        )

    @field_validator("allowed_universe", mode="before")
    @classmethod
    def _normalize_allowed_universe(cls, value: Any) -> str | list[str]:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return [str(item).strip().upper() for item in value if str(item).strip()]
        raise TypeError("allowed_universe must be a comma-separated string or list")

    def allowed_universe_set(self) -> set[str]:
        """Return the configured ticker universe as normalized symbols."""

        if isinstance(self.allowed_universe, str):
            raw = self.allowed_universe.split(",")
        else:
            raw = self.allowed_universe
        return {ticker.strip().upper() for ticker in raw if ticker.strip()}

    def policy(self) -> RiskPolicy:
        """Build the deterministic Month 1 risk policy."""

        return RiskPolicy(
            allowed_universe=self.allowed_universe_set(),
            max_quantity_per_order=self.max_quantity_per_order,
            kill_switch_active=self.kill_switch_active,
        )

    def rate_limit_config(self) -> RateLimitConfig:
        """Build the Redis-backed order-rate guardrail config."""

        return RateLimitConfig(
            max_orders_per_day=self.max_orders_per_day,
            symbol_cooldown_seconds=self.symbol_cooldown_seconds,
        )

    def postgres_dsn_value(self) -> str:
        """Return the DB DSN for connection setup without exposing it in repr/logs."""

        return self.postgres_dsn.get_secret_value()

    def redacted(self) -> dict[str, object]:
        """Return a log-safe settings snapshot."""

        return {
            "service_name": self.service_name,
            "instance_id": self.instance_id,
            "redis_url": self.redis_url,
            "allowed_universe": sorted(self.allowed_universe_set()),
            "max_quantity_per_order": self.max_quantity_per_order,
            "kill_switch_active": self.kill_switch_active,
            "max_orders_per_day": self.max_orders_per_day,
            "symbol_cooldown_seconds": self.symbol_cooldown_seconds,
            "tier3_enforcement": self.tier3_enforcement,
            "postgres_dsn": "***",
            "tier3_cache_ttl_seconds": self.tier3_cache_ttl_seconds,
            "start_id": self.start_id,
            "count": self.count,
            "block_ms": self.block_ms,
            "retry_delay_seconds": self.retry_delay_seconds,
            "log_level": self.log_level,
            "portfolio_limits_enforcement": self.portfolio_limits_enforcement,
            "monitor_interval_seconds": self.monitor_interval_seconds,
            "portfolio_limits": {
                "max_ticker_weight": self.max_ticker_weight,
                "max_gross_exposure": self.max_gross_exposure,
                "max_net_exposure": self.max_net_exposure,
                "max_cluster_weight": self.max_cluster_weight,
                "max_daily_loss": self.max_daily_loss,
                "max_strategy_drawdown": self.max_strategy_drawdown,
                "correlation_threshold": self.correlation_threshold,
                "min_cluster_history": self.min_cluster_history,
            },
        }
