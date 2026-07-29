# ADR-0017: One account per strategy, scored on how fast it grows

**Status:** Proposed
**Date opened:** 2026-07-29
**Deciders:** Mike White

## Context

Multi-account paper trading has been on the roadmap since the $10k ruling, with
the reason recorded as structural: both the Execution Agent and the
Reconciliation Agent hold one `alpaca_api_key`, so a single account nets
positions across strategies. Two strategies wanting opposite sides of the same
name cancel at the broker, and per-strategy P&L cannot be attributed at all
(KI-005).

Mike can now create **three Alpaca paper accounts**, and made two rulings
(2026-07-29):

1. **Three accounts, $10,000 each, one strategy per account.** The horizon —
   long-term, swing, or intraday — is a property of whichever strategy lands in
   an account, not a fixed slot. $10,000 because *"that's where I can start an
   account"*: the paper accounts mirror the real starting condition, so results
   transfer rather than flattering.
2. **"Their rewards should also be how quickly they can grow those accounts."**

Futures and crypto will use a separate broker — Mike's stated intent is to add
**NinjaTrader's API for paper**. See the flags at the end; that choice interacts
with ADR-0003 and needs deciding with eyes open, not in three weeks.

## Decision

### 1. One strategy per account, and the account *is* the track record

A live-paper strategy is assigned exactly one broker account. Nothing else
trades in it.

The consequence is the point: **the account's equity curve is that strategy's
P&L — exactly, with no attribution model, no netting, and nothing to derive.**
A number the firm cannot fudge, produced by the broker rather than by us.

**This deletes KI-005 for live-paper strategies.** Position state stops needing
derivation because each account holds one strategy's positions and only those.
KI-005 survives only for anything sharing an account.

The cost, accepted: **at most three concurrent live-paper strategies.** A fourth
promotion waits for a free account or evicts the weakest. That is a real
constraint on throughput, and it argues for keeping the bar high rather than
lowering it — which is the direction the firm already leans.

### 2. Growth is the forward-test score. Risk-adjustment is how it avoids luck

Note Mike's word: rewards should *also* be growth. This adds a measure rather
than replacing the gates, and the two answer different questions at different
times:

| | Promotion gate (before deployment) | Forward-test score (while trading) |
|---|---|---|
| Data | backtest / walk-forward | the live account's equity curve |
| Question | *is there evidence of skill?* | *is it actually making money?* |
| Measure | information ratio vs equal-weight buy-and-hold, Sharpe floor, min trades | **account growth, drawdown-penalised** |
| Decision | promote / hold / kill | keep / kill / reallocate the account |

**The honest tension, stated because it is the whole risk of this ruling:**
rewarding raw growth rate selects for leverage and concentration. A strategy
that puts the account into one name and gets lucky "grows the account fastest"
right up until it doesn't. Optimising a firm on unadjusted growth is how the
account reaches zero — and the firm has *just* removed one accidental version of
this (promoting strategies silently levered the book 2x and 4x; see the gross
exposure cap).

**The resolution keeps growth in the numerator.** Score on realised growth
divided by the drawdown it took to get there — the MAR/Calmar shape:

    score = growth since deployment / max drawdown since deployment

That is literally "how fast did it grow the account", penalised by how much of
the account it risked. A strategy growing 5%/month with a 6% worst drawdown
beats one growing 15%/month with a 50% drawdown, which is the correct ranking
for a firm that intends to still exist next year.

**Do not annualise a short sample.** Annualising three weeks of returns produces
a number that is mostly noise wearing a CAGR's clothes. Report cumulative growth
since deployment and max drawdown since deployment; compute a rate only once the
window supports one. The minimum window is a calibration Mike owns.

**Kills stay autonomous** (ADR-0015), so a per-account drawdown limit can retire
a decaying strategy without waiting for review. That limit now has a natural
home: it is a property of the account, and each account holds one strategy.

### 3. Accounts compete, and that is the intended dynamic

Three accounts, three strategies, three equity curves starting from the same
$10,000 on the same day. Ranking them is trivial and honest. This is the
firm's first real leaderboard, and it replaces backtest metrics as the thing
Mike looks at.

## What this breaks in the code as it stands

Found by reading, and each would fail quietly rather than loudly:

1. **`ops.account_snapshots` has no account identity.** It carries `broker` but
   no account column, and the Runner reads equity with
   `... ORDER BY at DESC LIMIT 1`. With three accounts writing rows, that
   returns *whichever account reported most recently* — so every strategy would
   size against a random one of the three books. This is the blocking item.
2. **One credential per agent.** `EXECUTION_AGENT_ALPACA_API_KEY` and
   `RECONCILIATION_AGENT_ALPACA_API_KEY` are single values. Three accounts need
   either three instances of each agent or account-aware agents.
3. **No strategy → account assignment exists.** Nothing in `research.strategies`
   or elsewhere records which account a strategy trades in.
4. **`allocate_equity` splits a firm-wide budget across all strategies.** Under
   this ADR the budget is *per account*, and an account with one strategy gives
   it the whole slice. The function generalises — it needs an account dimension,
   not a rewrite.
5. **Signals carry no account.** `trading.strategy.signal` and the intent that
   follows have no account field, so the Execution Agent cannot route.

## Flags on the NinjaTrader path

Recorded now because the decision is cheap to revisit today and expensive later.
None of these is a veto; all need checking rather than assuming.

- **NinjaTrader is not a NautilusTrader adapter.** Nautilus supports Interactive
  Brokers, Databento, and a long list of crypto venues (Coinbase, Kraken,
  Binance, Bybit, OKX, Deribit, Hyperliquid); NinjaTrader is not among them. So
  ADR-0003's gate card — "verify NautilusTrader's Alpaca and IBKR adapter event
  coverage" — would not cover it. Either NinjaTrader stays a direct client the
  way Alpaca already is (which ADR-0003 permits), or someone writes an adapter.
- **For MES specifically, IBKR is already the planned path.** ADR-0003 names
  IBKR, `01-roadmap.md` has "IBKR Gateway adapter live" as a Month-3 item, and
  IB *is* a Nautilus adapter. If futures are the goal, IBKR is the lower-friction
  route.
- **For crypto, several Nautilus-native venues exist**, so crypto through one of
  them is cheaper than through a second desktop platform.
- **Deployment is an open question.** NinjaTrader is primarily a Windows desktop
  platform and the Dell runs TrueNAS SCALE with Docker. Whether its current API
  can be driven from Linux without a Windows host is **unverified and must be
  checked before any card depends on it.**

## Consequences

**Enables:** exact per-strategy P&L; a growth-based score that cannot be gamed
by choice of benchmark; a real leaderboard; per-account drawdown kills.

**Constrains:** three concurrent live-paper strategies. Account identity has to
thread through snapshots, credentials, signals, intents and fills — every one of
which is currently account-blind.

**Risk accepted:** rewarding growth pulls toward leverage and concentration. The
drawdown denominator is the guard, and it is only as good as the drawdown limit
that enforces it — which does not exist yet.
