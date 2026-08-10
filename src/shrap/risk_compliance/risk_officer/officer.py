"""The Risk Officer as a service: assess one intent, and watch the book.

Two entry points.

:meth:`RiskOfficer.assess` runs on the order path, after the deterministic
policy check and the existing stateful gates. Like them it can only tighten, but
unlike them it can also *scale* — the spec's step 8 requires an over-limit intent
to be reduced rather than refused wherever a smaller order would fit.

:meth:`RiskOfficer.sweep` runs on the heartbeat and off the order path. It
recomputes the daily-loss and drawdown limits and sets switches automatically.

**Every read on the order path fails closed.** Switch state, positions, NAV,
price — if any of them cannot be established, the intent is vetoed with the
reason naming what was missing. The spec is explicit that a Risk Officer failing
open is "among the firm's worst failure modes", and that failing closed "halts
trading but is safe". There is no cached fallback and no default: an unknown
book is not an empty book.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import structlog

from shrap.risk_compliance.risk_officer.exposure import (
    ExposureUnavailable,
    build_book,
)
from shrap.risk_compliance.risk_officer.gate import PortfolioDecision, check_portfolio
from shrap.risk_compliance.risk_officer.limits import PortfolioLimits, regime_multiplier
from shrap.risk_compliance.risk_officer.monitor import (
    LimitObservation,
    check_daily_loss,
    check_strategy_drawdown,
)
from shrap.risk_compliance.risk_officer.sizing import SizingDecision, size_intent
from shrap.risk_compliance.risk_officer.store import RiskStore
from shrap.risk_compliance.risk_officer.switch_store import (
    RedisSwitchStore,
    SwitchStateUnavailable,
)
from shrap.risk_compliance.risk_officer.switches import (
    ACTOR_MONITOR,
    SWITCH_DAILY_LOSS,
    SwitchBoard,
    SwitchTransition,
    blocks_intent,
    reduces_position,
    strategy_switch,
)

log = structlog.get_logger(__name__)

REASON_RISK_STATE_UNAVAILABLE = "RISK_STATE_UNAVAILABLE"
REASON_NO_ACCOUNT = "STRATEGY_HAS_NO_ACCOUNT"
REASON_UNKNOWN_STRATEGY = "UNKNOWN_STRATEGY"
REASON_SELL_WITHOUT_POSITION = "SELL_WITHOUT_POSITION"

# How much price history the correlation clustering reads per name. Roughly a
# quarter of trading days — long enough for a 60-day correlation with slack,
# short enough that the relationship being measured is a current one.
PRICE_HISTORY_BARS = 90

# The drawdown limit is measured over the strategy's whole deployment, but the
# equity series has to start somewhere. A year covers the sprint several times.
DRAWDOWN_LOOKBACK = timedelta(days=365)


class StrategyLookup(Protocol):
    """The slice of the strategy registry this agent needs."""

    async def get(self, strategy_id: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """The portfolio layer's answer for one intent."""

    approved: bool
    approved_quantity: float
    reason_code: str
    notes: list[str] = field(default_factory=list)
    account_id: str | None = None
    binding_limit: str | None = None
    cluster: tuple[str, ...] | None = None
    sizing: SizingDecision | None = None
    regime_label: str | None = None
    regime_multiplier: float = 1.0

    def to_payload(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "approved_quantity": self.approved_quantity,
            "reason_code": self.reason_code,
            "notes": list(self.notes),
            "account_id": self.account_id,
            "binding_limit": self.binding_limit,
            "cluster": list(self.cluster) if self.cluster else None,
            "sizing": self.sizing.to_payload() if self.sizing else None,
            "regime": {
                "label": self.regime_label,
                "multiplier": self.regime_multiplier,
            },
        }

    @staticmethod
    def refused(reason_code: str, note: str, account_id: str | None = None) -> RiskAssessment:
        return RiskAssessment(
            approved=False,
            approved_quantity=0,
            reason_code=reason_code,
            notes=[note],
            account_id=account_id,
        )


@dataclass(frozen=True, slots=True)
class SweepResult:
    """What one monitoring pass observed and did."""

    observations: tuple[LimitObservation, ...] = ()
    transitions: tuple[SwitchTransition, ...] = ()

    @property
    def breaches(self) -> tuple[LimitObservation, ...]:
        return tuple(o for o in self.observations if o.is_breach)


class RiskOfficer:
    """Portfolio risk on the order path and on the heartbeat."""

    def __init__(
        self,
        store: RiskStore,
        switch_store: RedisSwitchStore,
        registry: StrategyLookup,
        limits: PortfolioLimits | None = None,
        *,
        price_history_bars: int = PRICE_HISTORY_BARS,
    ) -> None:
        self._store = store
        self._switches = switch_store
        self._registry = registry
        self._limits = limits or PortfolioLimits()
        self._history_bars = price_history_bars

    @property
    def limits(self) -> PortfolioLimits:
        return self._limits

    @property
    def store(self) -> RiskStore:
        return self._store

    # --- order path -----------------------------------------------------------

    async def assess(
        self,
        *,
        ticker: str,
        side: str,
        quantity: float,
        strategy_ids: Sequence[str],
        regime_label: str | None = None,
        regime_band: tuple[float, float] | None = None,
        now: datetime | None = None,
    ) -> RiskAssessment:
        """Judge one intent against the book. Never raises; refuses instead."""

        moment = now or datetime.now(UTC)
        multiplier = regime_multiplier(regime_label, regime_band)
        limits = self._limits.scaled_for_regime(multiplier)

        if not strategy_ids:
            return RiskAssessment.refused(
                REASON_UNKNOWN_STRATEGY,
                "intent carries no strategy_ids, so it cannot be attributed to an "
                "account and its exposure cannot be measured",
            )

        strategy_id = str(strategy_ids[0])
        try:
            record = await self._registry.get(strategy_id)
        except Exception:
            log.error("risk_officer.registry_unavailable", strategy_id=strategy_id, exc_info=True)
            return RiskAssessment.refused(
                REASON_RISK_STATE_UNAVAILABLE,
                f"strategy registry unreachable, so {strategy_id} cannot be resolved to an account",
            )
        if record is None:
            return RiskAssessment.refused(
                REASON_UNKNOWN_STRATEGY,
                f"{strategy_id} is not in research.strategies",
            )
        account_id = str(getattr(record, "account_id", "") or "")
        if not account_id:
            return RiskAssessment.refused(
                REASON_NO_ACCOUNT,
                f"{strategy_id} has no account_id (ADR-0017), so there is no book to "
                "measure this order against",
            )
        stage = str(getattr(record, "status", "") or "")

        try:
            board = await self._switches.load()
        except SwitchStateUnavailable as exc:
            return RiskAssessment.refused(REASON_RISK_STATE_UNAVAILABLE, str(exc), account_id)

        try:
            positions, observed_at = await self._store.latest_positions(account_id)
            equity_points = await self._store.equity_series(
                account_id, (moment - DRAWDOWN_LOOKBACK).date()
            )
        except Exception:
            log.error("risk_officer.book_unavailable", account_id=account_id, exc_info=True)
            return RiskAssessment.refused(
                REASON_RISK_STATE_UNAVAILABLE,
                f"cannot read the book for {account_id}",
                account_id,
            )
        if not equity_points:
            return RiskAssessment.refused(
                REASON_RISK_STATE_UNAVAILABLE,
                f"no account equity for {account_id} in ops.account_snapshots",
                account_id,
            )
        nav = equity_points[-1].equity

        try:
            book = build_book(nav, positions, observed_at, moment)
        except ExposureUnavailable as exc:
            return RiskAssessment.refused(REASON_RISK_STATE_UNAVAILABLE, str(exc), account_id)

        symbol = ticker.strip().upper()
        held_value = book.by_ticker.get(symbol, 0.0)
        try:
            price = await self._store.latest_close(symbol)
        except Exception:
            log.error("risk_officer.price_unavailable", ticker=symbol, exc_info=True)
            price = None

        delta = (quantity * (price or 0.0)) * (-1.0 if side.strip().lower() == "sell" else 1.0)
        blocking = blocks_intent(
            board,
            strategy_ids=strategy_ids,
            current_market_value=held_value,
            delta_market_value=delta,
        )
        if blocking is not None:
            return RiskAssessment(
                approved=False,
                approved_quantity=0,
                reason_code="KILL_SWITCH_ACTIVE",
                notes=[f"{blocking} is active"],
                account_id=account_id,
                binding_limit=blocking,
                regime_label=regime_label,
                regime_multiplier=multiplier,
            )

        # Sizing scales how much risk is *taken*, so it must not scale a trade
        # that reduces risk. Scaling an exit would sell a quarter of the
        # position, then a quarter of the remainder, and eventually round to
        # zero — leaving a position the strategy has asked to close and cannot.
        # Same failure as the regime-tightening case in `gate.py`: a control
        # meant to contain risk trapping the firm in it.
        # `reduces_position` answers False in two cases that are not "this adds
        # risk", and both fell through to size_intent — which then SCALED AN
        # EXIT, the exact thing the paragraph above forbids:
        #
        #   1. The book has no row for the ticker. `by_ticker.get(symbol, 0.0)`
        #      defaults to zero and `reduces_position` treats zero as "no
        #      position to reduce".
        #   2. The sell is larger than the position, which it reads as crossing
        #      through zero into a short.
        #
        # Measured live 2026-08-07: a 6-share UUP sell was approved at 1 and
        # stranded 5, while RIOT and RIVN sells in the same second were exempt
        # because their tickers happened to be in the book. Same predicate, two
        # answers, decided by whether a snapshot was current.
        #
        # The severe form is worse than stranding. Scaling a sell the account
        # cannot cover does not reduce anything — it OPENS A SHORT of the scaled
        # size. That is KI-030 arriving through the Risk Officer instead of the
        # Runner, and no veto is needed for it to happen.
        #
        # So sells are handled on their own terms: capped at the position, never
        # scaled below it, and refused outright when there is no position to
        # sell. The Officer's book and the Runner's read are up to a
        # reconciliation interval apart (KI-005), so "the book disagrees" is a
        # normal condition and must fail safe rather than fail small.
        px = price if price is not None and price > 0.0 else None
        is_sell = side.strip().lower() == "sell"
        held_shares = (held_value / px) if px is not None else 0.0

        if is_sell and px is not None and held_shares > 0.0:
            sizing = SizingDecision(
                requested_quantity=quantity,
                approved_quantity=min(quantity, held_shares),
                stage=stage or "unknown",
                stage_fraction=1.0,
                regime_multiplier=1.0,
                reference_price=px,
            )
        elif is_sell and px is not None:
            return RiskAssessment.refused(
                REASON_SELL_WITHOUT_POSITION,
                f"{symbol} sell of {quantity:g} against a book holding "
                f"{held_shares:g} — scaling this would open a short rather than "
                "reduce anything, so it is refused. Either the position snapshot "
                "is stale or the strategy is exiting something it never held.",
                account_id,
            )
        else:
            reducing = reduces_position(held_value, delta)
            sizing = (
                SizingDecision(
                    requested_quantity=quantity,
                    approved_quantity=quantity,
                    stage=stage or "unknown",
                    stage_fraction=1.0,
                    regime_multiplier=1.0,
                    reference_price=price,
                )
                if reducing
                else size_intent(
                    requested_quantity=quantity,
                    stage=stage,
                    regime_multiplier=multiplier,
                    reference_price=price,
                )
            )
        if sizing.approved_quantity <= 0:
            return RiskAssessment(
                approved=False,
                approved_quantity=0,
                reason_code="SIZED_TO_ZERO",
                notes=[
                    f"stage {sizing.stage} at {sizing.stage_fraction:.2f} and regime "
                    f"{multiplier:.2f} reduce {quantity} to nothing"
                ],
                account_id=account_id,
                sizing=sizing,
                regime_label=regime_label,
                regime_multiplier=multiplier,
            )

        history_tickers = sorted({symbol, *book.by_ticker})
        try:
            history = await self._store.price_history(history_tickers, self._history_bars)
        except Exception:
            log.error("risk_officer.history_unavailable", exc_info=True)
            # An empty history is not a licence to skip clustering: the
            # clusterer treats absent history as correlated, so this degrades
            # to one conservative cluster rather than to no cluster rule.
            history = {}

        decision: PortfolioDecision = check_portfolio(
            book=book,
            ticker=symbol,
            side=side,
            quantity=sizing.approved_quantity,
            price=price,
            limits=limits,
            price_history=history,
        )
        return RiskAssessment(
            approved=decision.approved,
            approved_quantity=decision.approved_quantity,
            reason_code=decision.reason_code,
            notes=list(decision.notes),
            account_id=account_id,
            binding_limit=decision.binding_limit,
            cluster=decision.cluster,
            sizing=sizing,
            regime_label=regime_label,
            regime_multiplier=multiplier,
        )

    # --- heartbeat ------------------------------------------------------------

    async def sweep(
        self,
        assignments: Sequence[tuple[str, str]],
        *,
        now: datetime | None = None,
        session: date | None = None,
    ) -> SweepResult:
        """Recompute the continuous limits for each ``(strategy_id, account_id)``.

        Breaches set switches automatically. Recovery does **not** clear a
        strategy switch: a drawdown limit that un-breached because the account
        bounced is not evidence the strategy is sound, and auto-clearing would
        let a book oscillate across the limit unattended. Only the daily-loss
        switch clears on its own, at the session boundary — see
        :meth:`clear_daily_loss`.
        """

        moment = now or datetime.now(UTC)
        today = session or moment.date()
        observations: list[LimitObservation] = []
        transitions: list[SwitchTransition] = []

        try:
            board = await self._switches.load()
        except SwitchStateUnavailable:
            log.error("risk_officer.sweep_switches_unavailable", exc_info=True)
            return SweepResult()

        for strategy_id, account_id in assignments:
            try:
                points = await self._store.equity_series(
                    account_id, (moment - DRAWDOWN_LOOKBACK).date()
                )
            except Exception:
                log.error("risk_officer.sweep_equity_failed", account_id=account_id, exc_info=True)
                continue
            if not points:
                continue

            daily = check_daily_loss(points, today, self._limits, account_id=account_id)
            if daily is not None:
                observations.append(daily)
                if daily.is_breach:
                    transition = board.set(
                        SWITCH_DAILY_LOSS,
                        actor=ACTOR_MONITOR,
                        reason=(
                            f"{account_id} down {daily.observed:.2%} on the session "
                            f"against a {daily.threshold:.2%} limit"
                        ),
                        at=moment,
                    )
                    if transition is not None:
                        transitions.append(transition)

            drawdown = check_strategy_drawdown(
                points, self._limits, strategy_id=strategy_id, account_id=account_id
            )
            if drawdown is not None:
                observations.append(drawdown)
                if drawdown.is_breach:
                    transition = board.set(
                        strategy_switch(strategy_id),
                        actor=ACTOR_MONITOR,
                        reason=(
                            f"{strategy_id} in a {drawdown.observed:.2%} drawdown "
                            f"against a {drawdown.threshold:.2%} limit"
                        ),
                        at=moment,
                    )
                    if transition is not None:
                        transitions.append(transition)

        await self._persist(transitions)
        return SweepResult(observations=tuple(observations), transitions=tuple(transitions))

    async def clear_daily_loss(self, *, actor: str, reason: str, now: datetime) -> bool:
        """Clear the daily-loss switch at a session boundary. True if it changed."""

        try:
            board = await self._switches.load()
        except SwitchStateUnavailable:
            return False
        transition = board.clear(SWITCH_DAILY_LOSS, actor=actor, reason=reason, at=now)
        if transition is None:
            return False
        await self._persist([transition])
        return True

    async def set_switch(
        self, name: str, *, actor: str, reason: str, now: datetime
    ) -> SwitchTransition | None:
        board = await self._switches.load()
        transition = board.set(name, actor=actor, reason=reason, at=now)
        if transition is not None:
            await self._persist([transition])
        return transition

    async def clear_switch(
        self, name: str, *, actor: str, reason: str, now: datetime
    ) -> SwitchTransition | None:
        board = await self._switches.load()
        transition = board.clear(name, actor=actor, reason=reason, at=now)
        if transition is not None:
            await self._persist([transition])
        return transition

    async def rebuild_switches(self) -> SwitchBoard:
        """Restore the Redis hash from the Postgres log."""

        states = await self._store.load_switch_states()
        await self._switches.rebuild(states)
        return SwitchBoard(states)

    async def _persist(self, transitions: Sequence[SwitchTransition]) -> None:
        """Postgres first, then Redis.

        Order matters. Postgres is the authority and Redis the cache, so a crash
        between the two leaves a switch recorded but not yet enforced — which
        the next `rebuild_switches` corrects. The reverse order would leave a
        switch enforced with no audit row, and an unexplained halt is worse to
        debug than a late one.
        """

        for transition in transitions:
            await self._store.record_switch(transition.state)
            await self._switches.save(transition.state)
            log.warning(
                "risk_officer.switch_transition",
                switch=transition.state.name,
                active=transition.state.active,
                actor=transition.state.actor,
                reason=transition.state.reason,
            )


__all__ = [
    "DRAWDOWN_LOOKBACK",
    "PRICE_HISTORY_BARS",
    "REASON_NO_ACCOUNT",
    "REASON_RISK_STATE_UNAVAILABLE",
    "REASON_UNKNOWN_STRATEGY",
    "RiskAssessment",
    "RiskOfficer",
    "StrategyLookup",
    "SweepResult",
]
