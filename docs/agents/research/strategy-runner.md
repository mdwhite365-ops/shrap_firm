# Strategy Runner

**Department:** Research
**LLM tier:** `no-llm`. The runner is a deterministic evaluator of already-promoted
strategies — target computation and the flat/invested transition rule are pure
functions of price data and stored state. No model output may ever influence
whether an order signal is emitted.
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** Draft
**Date:** 2026-07-26
**Author:** Mike White
**Version:** 0.1 (draft)

## Purpose

The Strategy Runner is the paper-trading loop's last structural piece and the
real successor to the Strategy Fixture. The fixture proved the pipeline can
carry an autonomous signal to a fill; it carries no market view. The runner
carries the firm's *actual* promoted strategies onto the trading path: on each
entry into market phase `open` it evaluates every active paper-stage strategy
and, when a strategy's intended target for a ticker changes, emits a
`trading.strategy.signal`.

Without it, promoting a strategy to `paper` in the registry does nothing — a
`paper`-stage strategy is a row with no way to act. The runner closes that gap
while keeping every downstream guardrail intact: it emits *signals only*, so the
Decision Maker (confidence gate, confluence, EXTREME-block), the Pre-Trade
Checker (quantity cap, rate guardrails), and the Execution Agent all still apply
exactly as they do for the fixture. It emits at most one action per
`(strategy, session)`, so a re-delivered or catch-up phase event cannot re-trade.

What this agent deliberately does **not** do:

- **No risk-officer integration.** It sizes each entry as a fraction of account
  equity (below), but regime-scaled sizing bands, portfolio-level exposure limits
  and correlation caps are still deferred.
- **No position reconciliation.** The runner owns only its per-strategy
  *intended target* and the share count it ordered. Actual fills and held
  positions remain the Reconciliation Agent's responsibility; the runner never
  reads broker state and never corrects drift between intent and holdings.
- **No intents, no broker calls, no real money. PAPER ONLY.**

## Sizing

An entry converts the strategy's target weight into shares against real account
equity:

    target_weight x equity = notional slot;  slot / price = shares, floored

Equity comes from `ops.account_snapshots`, which the Reconciliation Agent writes
every pass — ADR-0003 keeps broker credentials inside broker-facing containers,
and the runner is not one of them. Missing or stale equity (>30 min) **refuses
the whole pass**: nothing is published, no state is written, and the market-phase
event is left un-acked so the pass retries once a fresh snapshot lands. There is
no fixed-quantity fallback, because trading an unknown account size is worse than
trading late.

Four consequences worth knowing before reading a log:

- **Exits sell the recorded entry**, not a freshly sized position. `last_quantity`
  is stored per `(strategy, ticker)`; re-sizing at a later price would leave a
  residual or oversell into a short.
- **An entry that cannot be funded records *flat*.** A 10% slot on $10,000 is
  $1,000, so a $1,500 name cannot be held at all. Recording the intended weight
  anyway would make the next session read invested → flat and sell a position
  that was never opened. It is logged as `strategy_runner.sizing_note`.
- **`STRATEGY_RUNNER_MAX_QUANTITY` must equal `PRE_TRADE_MAX_QUANTITY_PER_ORDER`.**
  The Pre-Trade Checker *clamps* rather than vetoes, so a larger runner cap
  records an intent bigger than the fill — and the exit oversells. Raise both or
  neither. A test asserts the two defaults match.
- **Entries are sized at the previous close**, the newest price available without
  look-ahead at market open, and the same series the Evaluator measured. An
  overnight gap shifts the realized weight slightly; flooring means a gap up
  under-fills rather than breaching the slot.

## The emit-on-transition model

Each strategy exposes a target portfolio weight per ticker through the reused
Strategy Evaluator `StrategySignal` seam (`src/shrap/research/strategy_evaluator/`).
The reference rule is long-only, so per ticker the target is *flat* (weight 0) or
*invested* (weight > 0). The runner stores its last target per `(strategy, ticker)`
and turns changes into discrete orders:

| Stored target | Today's target | Emitted |
|---|---|---|
| flat (or first-ever) | invested | `buy` |
| invested | flat | `sell` |
| unchanged | unchanged | nothing |

A negative (short) weight is out of scope on the paper path and is treated as
flat, so the runner can only ever exit a long to flat — it never opens a short.
The signal payload is byte-for-byte the fixture's schema (`strategy_id`,
`ticker`, `side`, `size_hint`, `quantity`, `confidence`, `urgency`,
`regime_label`, `justification_text`), so the Decision Maker cannot tell the two
producers apart. `confidence` is a wiring constant chosen to clear the Decision
Maker threshold, not a market view.

## Trigger

- **Event:** Subscribes to `operations.market-phase` through the `strategy-runner`
  consumer group (KI-006). A run fires only on entry into phase `open`
  (`phase == "open"`); every other phase is acknowledged and ignored. The pass
  does **not** gate on the event's `reason` — a `startup`/catch-up `open` event
  triggers a pass just like a `transition`, and the per-session dedupe prevents
  duplicate work.

## Cross-references

**Depends on:** Strategy Librarian / registry (which strategies are active
paper-stage), Market Data store (`market_data.daily_bars`), Market Phase
Scheduler (`operations.market-phase`), Regime Classifier (informational label
only). Strategy Evaluator seam for the record → signal binding.
**Depended on by:** Decision Maker (consumes `trading.strategy.signal`), and the
Pre-Trade → Execution chain transitively.
**Related ADRs:** ADR-0003 (paper-only execution), ADR-0006 (envelope), ADR-0007
(Research funnel).
**Related architecture sections:** `docs/02-architecture.md` §Research
Department, §Trading Floor signal path.

## Inputs

| Source | Type | Description |
|---|---|---|
| Redis: `operations.market-phase` | Event | Phase transitions; the runner acts on entry into `open` |
| Redis: `intel.regime.sizing-modifier` | Query (xrevrange) | Latest regime label, informational only — never a gate |
| PostgreSQL: `research.strategies` | Query | Active paper-stage strategies (`paper`, `small-size-paper`, `live-paper`) |
| PostgreSQL: `market_data.daily_bars` | Query | Trailing daily OHLCV window per ticker (adjustment `all`) |
| PostgreSQL: `research.strategy_runner_state` | Query | The runner's last intended target per `(strategy, ticker)` |

`hypothesis` is deliberately excluded from the active set: an un-evaluated
strategy must never reach the trading path.

## Processing

1. On an `open` phase event, parse the session date from the payload (a
   malformed date is a poison message: acked and skipped).
2. List active paper-stage strategies from the registry and read all stored
   target state.
3. For each strategy: if its stored `last_session_date` already equals this
   session's date, skip it (idempotency guard). Otherwise instantiate its signal
   through the reused `StrategyFactory` (default: the reference MA-crossover rule
   from the record's params) and read a trailing daily-bar window long enough for
   the strategy's warmup, ending at the session date.
4. If bars are missing or insufficient (fewer aligned bars than the warmup),
   skip the strategy with a logged warning — never emit on a partial panel.
5. Compute today's target from the final no-peek window. Compare the
   flat/invested state to the stored target: emit `buy` on flat → invested,
   `sell` on invested → flat, nothing when unchanged. A first-ever `(strategy,
   ticker)` with no row is treated as flat, so an initial invested target emits
   the first buy.
6. Publish each emitted signal to `trading.strategy.signal`, then stamp the
   strategy's state rows (every processed ticker, emitting or not) with today's
   target, side, and session date.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis: `trading.strategy.signal` | Event | One signal per target transition, fixture payload schema |
| PostgreSQL: `research.strategy_runner_state` | Upsert | Last target per `(strategy, ticker)`, keyed by the PK; idempotent |

## LangGraph structure

Not used. The runner is a deterministic phase-event → signal translator; the
decision logic is a pure `inputs → (signals, state writes)` function
(`src/shrap/research/strategy_runner/engine.py`).

## State

| What | Store | Notes |
|---|---|---|
| Last intended target | PostgreSQL `research.strategy_runner_state` | One row per `(strategy_id, ticker)`; `last_session_date` is the per-session dedupe guard; `last_quantity` is the share count ordered, so the exit closes what was opened |
| Consumer offset | Redis consumer group `strategy-runner` | Offsets persist across restarts (KI-006) |
| Account equity | PostgreSQL `ops.account_snapshots` | **Read-only**, owned by the Reconciliation Agent. Never created or written here |

The runner stores only its *intended* target and ordered quantity, never
positions or fills. `last_quantity` is intent: if the Pre-Trade Checker clamps an
order, the recorded quantity exceeds what filled, and the exit oversells.
Reconciling the two is KI-005, and keeping the two caps equal is the interim
guard.

## Failure behavior

1. **Containment.** A wrong or spurious signal is contained by the same
   downstream guardrails that bound the fixture: the Decision Maker confidence
   gate, the Pre-Trade Checker's quantity cap and firm-wide rate guardrails, and
   paper-only execution (ADR-0003). The runner moves no money and holds no broker
   credentials. A single bad strategy (missing bars, bad spec, factory error) is
   skipped with a logged reason and never crashes the pass or emits a partial
   signal (fail-safe).
2. **Replay safety.** Safe to restart and reprocess. Offsets live in the
   consumer group, and the pass is idempotent on `(strategy_id, session_date)`:
   a re-delivered `open` event, a `startup`/catch-up event, or a restart
   mid-session re-runs only the strategies not yet stamped for the session, so no
   action is double-emitted. The narrow window is a crash between publishing a
   strategy's signals and stamping its state; downstream rate guardrails absorb
   the rare duplicate.
3. **Degraded operation.** The firm runs indefinitely without the runner —
   promoted strategies simply do not trade while it is down, which fails closed.
   On restart with `start_id="$"` it resumes from the next `open` event; it does
   not retroactively trade a session whose `open` it missed.
4. **Equity unavailable.** If the Reconciliation Agent has not written a snapshot
   in 30 minutes, the pass refuses: nothing published, no state written, phase
   event left un-acked so the session resumes on its own once a snapshot lands.
   This makes the Reconciliation Agent a hard dependency of trading — deliberate,
   since it is also the only source of the account size every position is a
   fraction of.

## Sprint scope

- Phase 1 (this card): deployable service consuming `operations.market-phase`,
  the pure per-session planner, the `research.strategy_runner_state` store, the
  emit-on-transition rule over the reused reference strategy seam, consumer-group
  discipline, and the `strategy-runner` compose service.

## Deferred

- **Continuous weight reconciliation.** The runner emits discrete orders on the
  flat/invested boundary only; it does not trade toward a changed target
  *magnitude*, nor reconcile intended target against actual held position.
- **Risk-Officer integration.** Notional sizing landed; regime-scaled sizing
  bands, portfolio exposure limits and correlation caps are a later card.
- **Fractional shares.** Slots below one share are skipped with a reason. Alpaca
  supports fractional quantities, which would remove the "a $1,500 name cannot be
  held on a $10,000 account" limit entirely. Its own card.
- **Intraday cadence.** One pass per session, on `open`. No rebalancing at other
  phases or intraday bars.
- **Strategy-authoring upgrade.** The record → signal binding is the Evaluator's
  `StrategyFactory` seam; when the authoring DSL lands it replaces
  `_default_strategy_factory` and the runner upgrades for free.

## Open questions

- **Multi-ticker strategies.** The reference rule is single-ticker. The dedupe
  guard treats a strategy as done once *any* of its tickers is stamped for the
  session, which is exact for single-ticker strategies but would under-process a
  partially-stamped multi-ticker strategy after a mid-pass crash. Blocks: nothing
  yet (reference rule is single-ticker). Owner: Mike, when a multi-ticker
  archetype is promoted.
- **Missed-session behavior.** With `start_id="$"` the runner does not trade a
  session whose `open` it missed (e.g. deployed after 09:30 ET). Confirm this
  fail-closed default versus reading the current phase on startup. Blocks:
  nothing. Owner: Mike.
