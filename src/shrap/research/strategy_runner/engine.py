"""Pure engine for the paper-strategy runner.

Given the active paper-stage strategies, their trailing daily bars, and the
runner's stored per-``(strategy_id, ticker)`` target, decide which
``trading.strategy.signal`` events to emit for one session — one signal per
target *transition*:

    flat -> invested  =>  a ``buy`` signal
    invested -> flat  =>  a ``sell`` signal
    unchanged         =>  nothing

There is **no I/O here**. The service loop reads bars/state and publishes; this
module is the deterministic ``inputs -> (signals, state writes)`` core so the
whole trading-relevant decision is exhaustively testable with fabricated data.

Invariants enforced in this module (the service enforces the delivery ones):

- **PAPER ONLY.** This emits *signals*, never intents, never broker calls, never
  real money. The Decision Maker -> Pre-Trade Checker -> Execution chain owns
  everything downstream. The emitted ``confidence`` merely has to clear the
  Decision Maker threshold; the Pre-Trade Checker still caps the quantity.
- **Sized in dollars, not in shares.** Each entry converts the strategy's target
  weight into a share count against actual account equity
  (:mod:`shrap.research.strategy_runner.sizing`). Equity is a required input:
  there is no default and no fallback, so a pass with unknown account size emits
  nothing rather than emitting a size nobody evaluated.
- **Bounded firm-wide, not just per strategy.** Total intended exposure across
  every active strategy is capped at ``max_gross_exposure`` x equity (default
  1.0 — fully invested, unlevered). Each strategy sizes against an equal slice
  of that budget, so its weights are fractions of its own allocation. Sizing
  every strategy against the *whole* account is how a book silently levers: two
  strategies at full investment order 200% of equity, four order 400%.
- **Idempotent per session.** A strategy whose stored ``last_session_date``
  already equals this session's date is skipped, so a re-delivered / startup /
  catch-up market-phase event (or a restart) never produces a second pass. At
  most one action per ``(strategy_id, session_date)``.
- **Fail-safe.** Any per-strategy error (bad spec, factory error, missing or
  insufficient bars) skips *that* strategy with a recorded reason. It never
  raises out of :func:`plan_session` and never emits a partial signal.
- **Regime is informational only.** ``regime_label`` rides along in the payload
  but never gates emission — regime is a sizing modifier, not an entry/exit
  gate.

The record -> signal binding is the reused Strategy Evaluator
:data:`~shrap.research.strategy_evaluator.pipeline.StrategyFactory` seam, so the
deferred strategy-authoring card upgrades this runner for free.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from shrap.research.strategy_evaluator.strategy import BarSample, PanelWindow, PricePanel
from shrap.research.strategy_registry import StrategyRecord
from shrap.research.strategy_runner.sizing import SizingRefused, size_position

if TYPE_CHECKING:
    from shrap.research.strategy_evaluator.pipeline import StrategyFactory

# The stream and envelope identity. The payload schema below is byte-for-byte
# the Strategy Fixture's schema (its real successor): the Decision Maker and
# everything downstream must not be able to tell the two producers apart.
STREAM_STRATEGY_SIGNAL = "trading.strategy.signal"
PRODUCED_BY = "research/strategy-runner"
SCHEMA_VERSION = "1.0.0"

SIDE_BUY = "buy"
SIDE_SELL = "sell"
DEFAULT_URGENCY = "normal"
UNKNOWN_REGIME = "unknown"

# Default emit confidence. The Decision Maker skips signals whose confidence
# does not *strictly exceed* its threshold (default 0.7); 0.75 clears it with a
# small margin. This is a pipeline-wiring constant, not a market view.
DEFAULT_CONFIDENCE = 0.75

# Per-order share cap. This mirrors the Pre-Trade Checker's
# ``max_quantity_per_order`` and MUST be kept equal to it.
#
# The checker *clamps* rather than vetoes (``pre_trade.py`` takes
# ``min(requested, cap)``), so a runner that sized 20 shares against a cap of 1
# would have 1 share fill while recording an intent of 20 — and its later exit
# would try to sell 20 shares of a 1-share position. Sizing to the same cap keeps
# recorded intent equal to approved quantity, and the clamp is *reported* rather
# than silent. The two values are raised together or not at all.
#
# 100 is sized for a $10,000 book: a 10% slot is $1,000, or 100 shares at $10, so
# it binds only on cheap names. The real position limit is the target weight.
DEFAULT_MAX_QUANTITY = 100

# Exit size for a row written before sizing existed. Not a guess: the pre-sizing
# runner emitted a hardcoded 1 share, so 1 is exactly what any such position holds.
LEGACY_EXIT_QUANTITY = 1

# Total intended exposure across ALL strategies, as a multiple of account equity.
#
# 1.0 means fully invested and unlevered. This is a *firm-wide* budget, not a
# per-strategy one, because sizing each strategy against full equity is how a
# book silently becomes levered: two strategies at 100% target 200% of the
# account, four target 400%. Measured on this engine before the cap existed —
# it is arithmetic, not a hypothetical.
#
# Deliberately 1.0 rather than something aggressive, even though Mike's ruling
# is "it can be aggressive". Leverage is the mechanism by which accounts reach
# zero, and the firm currently has no drawdown limit, no per-strategy loss limit,
# and no model of FINRA's intraday margin deficit (ADR-0016). Raising this is a
# decision to make once those exist, and it is one config value away.
DEFAULT_MAX_GROSS_EXPOSURE = 1.0


@dataclass(frozen=True, slots=True)
class RunnerSignalConfig:
    """Signal-shaping knobs. Conservative defaults on the trading path."""

    max_quantity: int = DEFAULT_MAX_QUANTITY  # keep equal to the Pre-Trade cap
    confidence: float = DEFAULT_CONFIDENCE  # must clear the Decision Maker threshold
    urgency: str = DEFAULT_URGENCY
    max_gross_exposure: float = DEFAULT_MAX_GROSS_EXPOSURE  # firm-wide, not per strategy


@dataclass(frozen=True, slots=True)
class TargetState:
    """The runner's stored last intended target for one ``(strategy_id, ticker)``.

    ``last_target`` is the last computed target weight (long-only: 0 = flat,
    >0 = invested). ``last_session_date`` is the session the row was last
    stamped in — the per-strategy idempotency guard. A ``(strategy, ticker)``
    with no row is treated as :data:`FLAT_TARGET` (flat, never seen).

    ``last_quantity`` is the share count of the entry signal, carried so the exit
    can sell the position that was opened rather than re-sizing at a later price.
    Re-sizing an exit would leave a residual (price up) or oversell into a short
    (price down), so the quantity is remembered, not recomputed.

    It records *intent*, not fills. Reconciling intent against what the broker
    actually filled is KI-005's job, and this is why the runner's per-order cap
    must track the Pre-Trade Checker's.
    """

    last_target: float
    last_side: str | None
    last_session_date: date | None
    last_quantity: int = 0


FLAT_TARGET = TargetState(last_target=0.0, last_side=None, last_session_date=None, last_quantity=0)


@dataclass(frozen=True, slots=True)
class StrategyInput:
    """One active paper-stage strategy plus its trailing bars, per ticker.

    ``tickers`` is the ordered, de-duplicated ticker list (the same extraction
    the Evaluator uses). ``bars_by_ticker`` holds the trailing no-peek window
    read for each ticker, ending at (or before) the session date.
    """

    record: StrategyRecord
    tickers: list[str]
    bars_by_ticker: dict[str, list[BarSample]]


@dataclass(frozen=True, slots=True)
class PlannedSignal:
    """A signal the service will publish verbatim to ``trading.strategy.signal``."""

    strategy_id: str
    ticker: str
    side: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PlannedStateWrite:
    """A state-store upsert to persist after a strategy's signals are published."""

    strategy_id: str
    ticker: str
    last_target: float
    last_side: str | None
    last_session_date: date
    last_quantity: int = 0


@dataclass(frozen=True, slots=True)
class StrategyPlan:
    """The planned outcome for one strategy this session."""

    strategy_id: str
    skipped: bool
    skip_reason: str | None
    signals: tuple[PlannedSignal, ...]
    state_writes: tuple[PlannedStateWrite, ...]
    sizing_notes: tuple[str, ...] = ()
    """Per-ticker sizing outcomes worth a log line: clamps, and entries that
    could not be sized at all. Empty when every entry sized cleanly."""


def _invested(weight: float) -> bool:
    """Long-only invested test.

    ``> 0`` is invested (long). Zero is flat. A negative (short) weight is out
    of scope on the paper path and is treated as flat, so the runner can only
    ever exit to flat — it never opens a short.
    """

    return weight > 0.0


def _justification(
    *,
    strategy_name: str,
    strategy_id: str,
    ticker: str,
    prev_invested: bool,
    now_invested: bool,
    quantity: int,
    sizing: str,
) -> str:
    """Explain the transition *and* the size.

    The size belongs in the audit trail: a share count with no stated basis is
    unreviewable after the fact, and this is the field a human reads when asking
    why the book holds what it holds.
    """

    prev = "invested" if prev_invested else "flat"
    now = "invested" if now_invested else "flat"
    return (
        f"Strategy '{strategy_name}' ({strategy_id}) target for {ticker} changed "
        f"{prev} -> {now}; {quantity} share(s) {sizing}. "
        "Paper-stage strategy runner; not investment advice."
    )


def _build_payload(
    *,
    strategy_id: str,
    ticker: str,
    side: str,
    quantity: int,
    account_id: str,
    config: RunnerSignalConfig,
    regime_label: str | None,
    justification: str,
) -> dict[str, Any]:
    """Build the signal payload: the Strategy Fixture schema plus the account.

    ``account_id`` is the routing key. Three Execution Agents each watch the one
    ``risk.intent.approved`` stream with their own consumer group and act only on
    their own account, so a signal that does not name one cannot be routed —
    every agent would skip it, or, if they defaulted to acting, all three would
    submit the same order.
    """

    return {
        "strategy_id": strategy_id,
        "account_id": account_id,
        "ticker": ticker.upper(),
        "side": side,
        "size_hint": quantity,
        "quantity": quantity,
        "confidence": config.confidence,
        "urgency": config.urgency,
        "regime_label": regime_label or UNKNOWN_REGIME,
        "justification_text": justification,
    }


def allocate_equity(equity: float, n_strategies: int, max_gross_exposure: float) -> float:
    """Split the firm-wide exposure budget into one equal slice per strategy.

    A strategy's target weights are *its own* fractions — "100%" means all of
    what that strategy was given, not all of the account. Before this, every
    strategy sized against the whole account independently, so N strategies at
    full investment ordered N times the equity. That was measured on this engine,
    not theorised.

    Equal slices rather than proportional scaling, deliberately. Scaling every
    strategy's weights to make the total fit would change the relative sizing
    *within* a strategy as unrelated strategies came and went, so a strategy
    would trade differently depending on what else happened to be running. A
    fixed slice keeps each strategy trading the shape the Evaluator measured; only
    its scale changes.

    Divides by the count of *active* strategies rather than the count that
    actually emit today. A slice that changed daily with who happened to trade
    would resize the meaning of "100%" between an entry and its exit.

    **Honest limit.** This bounds *intended* exposure while the slice is stable.
    Promoting or killing a strategy changes the divisor, so positions opened
    under an older, larger slice stay larger than the new one until they exit —
    bounded by the old cap, converging as they close. Making this exact requires
    live position values, which the Runner does not have (KI-005); that is the
    Risk Officer's job, not this function's.
    """

    if n_strategies <= 0:
        return 0.0
    if max_gross_exposure <= 0.0:
        raise SizingRefused(
            f"max_gross_exposure is {max_gross_exposure}, so no capital may be "
            "deployed. Set it above zero or stop the runner — refusing to size."
        )
    return equity * max_gross_exposure / n_strategies


def _latest_close(window: PanelWindow, ticker: str) -> float:
    """The most recent completed close — the price the entry is sized against.

    The runner plans at market *open*, so the last completed session's close is
    the newest price that exists without look-ahead, and it is the same series
    the Evaluator measured the strategy on. An overnight gap therefore shifts the
    realized weight slightly; flooring means a gap up under-fills the slot rather
    than breaching it.
    """

    closes = window.closes(ticker)
    if not closes:
        raise ValueError(f"no closes available for {ticker} — cannot size an entry")
    return float(closes[-1])


def _already_ran(
    strategy_id: str,
    tickers: Sequence[str],
    stored_state: Mapping[tuple[str, str], TargetState],
    session_date: date,
) -> bool:
    """True if this strategy already ran this session (idempotency guard).

    A strategy is considered done for the session as soon as *any* of its
    ``(strategy_id, ticker)`` rows carries this ``session_date``. After a
    completed pass every processed ticker is stamped, so this is exact for the
    single-ticker reference rule; for a partially-stamped multi-ticker strategy
    it errs toward not re-running (never double-emitting), which is the safe
    choice on the trading path.
    """

    for ticker in tickers:
        state = stored_state.get((strategy_id, ticker))
        if state is not None and state.last_session_date == session_date:
            return True
    return False


def _skip(strategy_id: str, reason: str) -> StrategyPlan:
    return StrategyPlan(
        strategy_id=strategy_id,
        skipped=True,
        skip_reason=reason,
        signals=(),
        state_writes=(),
    )


def _plan_strategy(
    *,
    session_date: date,
    item: StrategyInput,
    stored_state: Mapping[tuple[str, str], TargetState],
    factory: StrategyFactory,
    config: RunnerSignalConfig,
    regime_label: str | None,
    equity: float,
    account_id: str,
) -> StrategyPlan:
    strategy_id = item.record.strategy_id
    try:
        if _already_ran(strategy_id, item.tickers, stored_state, session_date):
            return _skip(strategy_id, "already ran this session")
        if not item.tickers:
            return _skip(strategy_id, "no tickers declared")

        # Build the strategy signal from the record via the reused factory seam.
        signal = factory(item.record, item.tickers)
        warmup = max(int(signal.warmup), 1)

        # Never emit on a partial panel: any missing ticker series -> skip.
        if any(not item.bars_by_ticker.get(ticker) for ticker in item.tickers):
            return _skip(strategy_id, "missing bars for one or more tickers")

        panel = PricePanel.from_bars(
            {ticker: item.bars_by_ticker[ticker] for ticker in item.tickers}
        )
        if panel.n_bars < warmup:
            return _skip(
                strategy_id,
                f"insufficient bars: {panel.n_bars} aligned < warmup {warmup}",
            )

        # As-of the latest available session (no-peek: the window cannot reach
        # a later bar). At market open the store holds only completed sessions.
        window = panel.window(panel.n_bars - 1)
        targets = signal.target_weights(window)

        signals: list[PlannedSignal] = []
        writes: list[PlannedStateWrite] = []
        notes: list[str] = []
        for ticker in item.tickers:
            weight = float(targets.get(ticker, 0.0))
            now_inv = _invested(weight)
            prev = stored_state.get((strategy_id, ticker), FLAT_TARGET)
            prev_inv = _invested(prev.last_target)

            side: str | None = None
            emit_quantity = 0
            sizing_basis = ""
            # An untouched ticker keeps its stored position; only a transition
            # changes what we believe we hold.
            stored_quantity = prev.last_quantity
            stored_target = weight

            if now_inv and not prev_inv:
                price = _latest_close(window, ticker)
                sized = size_position(
                    target_weight=weight,
                    equity=equity,
                    price=price,
                    max_quantity=config.max_quantity,
                )
                if sized.reason:
                    notes.append(f"{ticker}: {sized.reason}")
                if sized.is_tradeable:
                    side = SIDE_BUY
                    emit_quantity = sized.quantity
                    stored_quantity = sized.quantity
                    sizing_basis = (
                        f"= {weight:.4f} of ${equity:,.2f} equity "
                        f"(${sized.notional_slot:,.2f}) at ${price:,.2f}"
                    )
                else:
                    # An entry that could not be sized did not happen. Record it
                    # as flat: storing the invested weight would make next
                    # session read this as an exit and emit a sell for a
                    # position the firm never opened.
                    stored_target = 0.0
                    stored_quantity = 0
            elif prev_inv and not now_inv:
                side = SIDE_SELL
                # Sell the position we opened, not a freshly sized one.
                emit_quantity = prev.last_quantity or LEGACY_EXIT_QUANTITY
                stored_quantity = 0
                sizing_basis = "closing the recorded entry"

            if side is not None:
                payload = _build_payload(
                    strategy_id=strategy_id,
                    ticker=ticker,
                    side=side,
                    quantity=emit_quantity,
                    account_id=account_id,
                    config=config,
                    regime_label=regime_label,
                    justification=_justification(
                        strategy_name=item.record.name,
                        strategy_id=strategy_id,
                        ticker=ticker,
                        prev_invested=prev_inv,
                        now_invested=now_inv,
                        quantity=emit_quantity,
                        sizing=sizing_basis,
                    ),
                )
                signals.append(
                    PlannedSignal(
                        strategy_id=strategy_id,
                        ticker=ticker.upper(),
                        side=side,
                        payload=payload,
                    )
                )

            # Stamp every processed ticker (emitting or not) so the session is
            # marked done for the whole strategy — the idempotency guard.
            writes.append(
                PlannedStateWrite(
                    strategy_id=strategy_id,
                    ticker=ticker,
                    last_target=stored_target,
                    last_side=side or prev.last_side,
                    last_session_date=session_date,
                    last_quantity=stored_quantity,
                )
            )

        return StrategyPlan(
            strategy_id=strategy_id,
            skipped=False,
            skip_reason=None,
            signals=tuple(signals),
            state_writes=tuple(writes),
            sizing_notes=tuple(notes),
        )
    except Exception as exc:  # fail-safe: one bad strategy never breaks the pass
        return _skip(strategy_id, f"error: {exc!r}")


def plan_session(
    *,
    session_date: date,
    strategies: Sequence[StrategyInput],
    stored_state: Mapping[tuple[str, str], TargetState],
    factory: StrategyFactory,
    config: RunnerSignalConfig,
    regime_label: str | None,
    equity: float,
    account_id: str,
) -> list[StrategyPlan]:
    """Plan one session: fabricated strategies + bars + state -> signals to emit.

    Pure and total: every input strategy yields exactly one :class:`StrategyPlan`
    (emitting, no-op, or skipped-with-reason); nothing here performs I/O or
    raises.

    ``equity`` has no default on purpose. The caller must have established a
    usable account size (``sizing.assert_equity_usable``) before planning, and a
    default here would be a silent fiction the whole book was sized against.

    ``equity`` is the *whole account*. Each strategy is then sized against its
    own slice of the firm-wide exposure budget (:func:`allocate_equity`), so a
    strategy's "100%" is 100% of what it was allocated, never 100% of the book.
    """

    budget = allocate_equity(equity, len(strategies), config.max_gross_exposure)
    return [
        _plan_strategy(
            session_date=session_date,
            item=item,
            stored_state=stored_state,
            factory=factory,
            config=config,
            regime_label=regime_label,
            equity=budget,
            account_id=account_id,
        )
        for item in strategies
    ]


__all__ = [
    "DEFAULT_CONFIDENCE",
    "DEFAULT_MAX_GROSS_EXPOSURE",
    "DEFAULT_MAX_QUANTITY",
    "DEFAULT_URGENCY",
    "FLAT_TARGET",
    "LEGACY_EXIT_QUANTITY",
    "PRODUCED_BY",
    "SCHEMA_VERSION",
    "SIDE_BUY",
    "SIDE_SELL",
    "STREAM_STRATEGY_SIGNAL",
    "UNKNOWN_REGIME",
    "PlannedSignal",
    "PlannedStateWrite",
    "RunnerSignalConfig",
    "StrategyInput",
    "StrategyPlan",
    "TargetState",
    "allocate_equity",
    "plan_session",
]
