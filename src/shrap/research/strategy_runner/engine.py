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

from shrap.research.strategy_evaluator.strategy import BarSample, PricePanel
from shrap.research.strategy_registry import StrategyRecord

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

# Default order size. Small on purpose: the runner owns only its intended
# flat/invested *target*, not sizing. The Pre-Trade Checker caps the quantity
# downstream regardless of what we put here, so 1 is a safe, honest placeholder.
DEFAULT_QUANTITY = 1


@dataclass(frozen=True, slots=True)
class RunnerSignalConfig:
    """Signal-shaping knobs. Conservative defaults on the trading path."""

    quantity: int = DEFAULT_QUANTITY  # size_hint == quantity; Pre-Trade caps it anyway
    confidence: float = DEFAULT_CONFIDENCE  # must clear the Decision Maker threshold
    urgency: str = DEFAULT_URGENCY


@dataclass(frozen=True, slots=True)
class TargetState:
    """The runner's stored last intended target for one ``(strategy_id, ticker)``.

    ``last_target`` is the last computed target weight (long-only: 0 = flat,
    >0 = invested). ``last_session_date`` is the session the row was last
    stamped in — the per-strategy idempotency guard. A ``(strategy, ticker)``
    with no row is treated as :data:`FLAT_TARGET` (flat, never seen).
    """

    last_target: float
    last_side: str | None
    last_session_date: date | None


FLAT_TARGET = TargetState(last_target=0.0, last_side=None, last_session_date=None)


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


@dataclass(frozen=True, slots=True)
class StrategyPlan:
    """The planned outcome for one strategy this session."""

    strategy_id: str
    skipped: bool
    skip_reason: str | None
    signals: tuple[PlannedSignal, ...]
    state_writes: tuple[PlannedStateWrite, ...]


def _invested(weight: float) -> bool:
    """Long-only invested test.

    ``> 0`` is invested (long). Zero is flat. A negative (short) weight is out
    of scope on the paper path and is treated as flat, so the runner can only
    ever exit to flat — it never opens a short.
    """

    return weight > 0.0


def _justification(
    *, strategy_name: str, strategy_id: str, ticker: str, prev_invested: bool, now_invested: bool
) -> str:
    prev = "invested" if prev_invested else "flat"
    now = "invested" if now_invested else "flat"
    return (
        f"Strategy '{strategy_name}' ({strategy_id}) moving-average crossover target for "
        f"{ticker} changed {prev} -> {now}. "
        "Paper-stage strategy runner; not investment advice."
    )


def _build_payload(
    *,
    strategy_id: str,
    ticker: str,
    side: str,
    config: RunnerSignalConfig,
    regime_label: str | None,
    justification: str,
) -> dict[str, Any]:
    """Build the exact Strategy Fixture signal payload schema."""

    return {
        "strategy_id": strategy_id,
        "ticker": ticker.upper(),
        "side": side,
        "size_hint": config.quantity,
        "quantity": config.quantity,
        "confidence": config.confidence,
        "urgency": config.urgency,
        "regime_label": regime_label or UNKNOWN_REGIME,
        "justification_text": justification,
    }


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
        for ticker in item.tickers:
            weight = float(targets.get(ticker, 0.0))
            now_inv = _invested(weight)
            prev = stored_state.get((strategy_id, ticker), FLAT_TARGET)
            prev_inv = _invested(prev.last_target)

            side: str | None = None
            if now_inv and not prev_inv:
                side = SIDE_BUY
            elif prev_inv and not now_inv:
                side = SIDE_SELL

            if side is not None:
                payload = _build_payload(
                    strategy_id=strategy_id,
                    ticker=ticker,
                    side=side,
                    config=config,
                    regime_label=regime_label,
                    justification=_justification(
                        strategy_name=item.record.name,
                        strategy_id=strategy_id,
                        ticker=ticker,
                        prev_invested=prev_inv,
                        now_invested=now_inv,
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
                    last_target=weight,
                    last_side=side or prev.last_side,
                    last_session_date=session_date,
                )
            )

        return StrategyPlan(
            strategy_id=strategy_id,
            skipped=False,
            skip_reason=None,
            signals=tuple(signals),
            state_writes=tuple(writes),
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
) -> list[StrategyPlan]:
    """Plan one session: fabricated strategies + bars + state -> signals to emit.

    Pure and total: every input strategy yields exactly one :class:`StrategyPlan`
    (emitting, no-op, or skipped-with-reason); nothing here performs I/O or
    raises.
    """

    return [
        _plan_strategy(
            session_date=session_date,
            item=item,
            stored_state=stored_state,
            factory=factory,
            config=config,
            regime_label=regime_label,
        )
        for item in strategies
    ]


__all__ = [
    "DEFAULT_CONFIDENCE",
    "DEFAULT_QUANTITY",
    "DEFAULT_URGENCY",
    "FLAT_TARGET",
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
    "plan_session",
]
