# Risk policy

**Status:** v0.1 — first cuts, pending Mike's ruling
**Date:** 2026-07-29
**Owner:** Mike White
**Enforced by:** Risk Officer (`src/shrap/risk_compliance/risk_officer/`)

This file is the authoritative limit policy. `docs/agents/risk-compliance/risk-officer.md`
names it as such: the Risk Officer reads its limits from here, and changing a limit is a
PR against this document. No agent may change a number in this file.

Every limit below is a **first cut**. Four of them are the spec's own open questions with
Mike listed as owner; the rest are new and are marked as such. They are implemented as
defaults so the firm has enforcement today, on the same precedent as the Regime
Classifier's v0.1 calibrations. Merging the Risk Officer card means accepting these
numbers as the operating limits until Mike rules otherwise.

## Why these numbers exist at all

On 2026-07-29 the firm's first strategy evaluation reported a **53.88% maximum drawdown**
across a six-fold walk-forward. Nothing in the running system would have noticed. The
only automatic control on the order path was a 100-share per-order cap, and the only kill
switch was an environment variable a human had to set.

The limits here are sized against that observation: they are meant to bind well before a
drawdown of that size, without binding during normal operation of an equal-weight
ten-name strategy on a $10,000 account (ADR-0017).

## Account model (ADR-0017)

One strategy per broker account, three accounts, $10,000 each. This shapes every limit:

- **Portfolio limits are per-account**, because an account holds exactly one strategy's
  positions and nothing else. "Gross exposure" means that strategy's gross exposure.
- **Cross-account concentration is a separate, real risk.** Three strategies independently
  long the same mega-cap is one trade wearing three hats. That is the firm-level cluster
  cap below.
- NAV per account is the newest `ops.account_snapshots` row for that `account_id`.

## Limits

### Per-ticker exposure cap

| Limit | Value |
|---|---|
| `max_ticker_weight` | **20%** of account NAV |

*New in this card.* A ten-name equal-weight strategy targets 10% per name, so a cap at
10% would veto normal rebalancing. 20% is twice the intended slot: it permits drift and
concentration the strategy actually asked for, and blocks a single name becoming a fifth
of the book through a sizing bug or a runaway loop.

### Gross and net exposure

| Limit | Value |
|---|---|
| `max_gross_exposure` | **100%** of NAV |
| `max_net_exposure` | **100%** of NAV |

*New in this card.* No leverage on paper. Gross is `sum(abs(notional))`, net is
`sum(notional)`. A dollar-neutral long-short book runs gross 100% / net 0%; a long-only
book runs gross == net. Both are permitted; neither may exceed NAV.

Note this pair is what makes the long-short work from PR #145 safe to evaluate: a
long-short strategy that somehow reached the order path cannot quietly run 150% gross.

### Cluster cap (correlation)

| Limit | Value |
|---|---|
| `max_cluster_weight` | **15%** of NAV — *spec open question, first cut as written* |
| `correlation_threshold` | **0.80** realized 60-day |
| `min_cluster_history` | **40** bars |

The spec calls this "the single most-important defense against the 'everything is one
trade' disaster." Positions are clustered by realized correlation above the threshold and
the cluster's summed exposure is capped.

**The 15% first cut conflicts with the 20% per-ticker cap**, deliberately: a single name
above 15% is its own cluster and will bind on the cluster rule first. Mike should rule on
whether that is intended — the alternative is a cluster cap above the ticker cap, which
makes the cluster rule non-binding for concentrated books.

**When correlation cannot be computed** (too little history, missing bars), the pair is
treated as **correlated**. Unknown correlation is not zero correlation; assuming
independence is the exact error the rule exists to prevent.

### Daily loss limit

| Limit | Value |
|---|---|
| `max_daily_loss` | **2%** of NAV — *spec open question, first cut as written* |

Measured as the account's equity change since the first snapshot on the current session
date. On breach the Risk Officer sets `daily_loss` automatically and halts **new intents
firm-wide**. Existing positions are not force-closed — each strategy's own exit logic
runs. The switch clears at the next session boundary.

### Strategy drawdown limit

| Limit | Value |
|---|---|
| `max_strategy_drawdown` | **25%** peak-to-trough on account equity |

*New in this card.* The number that would have caught the 53.88% observation. It is set
well inside that figure and well outside ordinary equity-strategy noise. On breach the
Risk Officer sets `strategy:<id>` and that strategy alone stops opening positions.

This is the limit most likely to be wrong, because it is the one with the least evidence
behind it. It is a judgement call about how much of a $10,000 account may be lost before
the firm stops and looks, not a derived quantity.

### Velocity

| Limit | Value |
|---|---|
| `max_orders_per_day` | existing `RateLimitConfig` |
| `symbol_cooldown_seconds` | existing `RateLimitConfig` |

Already enforced by the Redis rate guardrails shipped with the Pre-Trade Checker. Listed
here for completeness; the Risk Officer did not change them.

### Regime scaling

Exposure caps are multiplied by the **low end** of the current regime's sizing band, from
`intel.regime.sizing-modifier`:

| Regime | Band | Cap multiplier |
|---|---|---|
| `late-cycle-melt-up` | 0.75 – 1.00 | **0.75** |
| `crisis-recovery` | 0.75 – 1.25 | **0.75** |
| `stagflation` | 0.50 – 0.75 | **0.50** |
| `wartime` | 0.25 – 0.75 | **0.25** |
| `unknown` / no event | 0.25 – 0.50 | **0.25** |

**The low end, not the midpoint or the high end.** A risk limit takes the conservative end
of a range by construction. The band's upper half describes how much a strategy *may* want
to size up; that is the Decision Maker's argument to make, not the veto authority's.

Note the consequence: with no regime event at all, caps run at 25%. That is intentional —
missing regime state is not a licence to run full size — but it means the Regime
Classifier being down materially tightens the firm. That is a real operational coupling
and Mike should know it exists.

The `crisis-recovery` band tops out at 1.25, above full size. The Risk Officer never
scales caps *above* 100% of the base limit regardless of band, because these are limits,
not targets.

## Sizing

The spec calls for Kelly-fractional sizing: `Kelly fraction × posterior edge × regime fit`,
with the fraction at 25% by default and capped at 50%.

**The posterior input does not exist.** There is no Bayesian Updater in the firm — no
spec implementation, no service, no table. Kelly sizing cannot be computed from a
posterior that is never produced, and inventing one from backtest Sharpe is the exact
substitution the spec forbids ("Kelly inputs come from the Bayesian Updater's posterior,
not from raw backtest Sharpe").

So this card implements the spec's own documented fallback — open question 4, "Kelly
inputs when posterior is thin: fall back to flat fraction, currently yes, at the lowest
tier":

| Stage | Fraction of the strategy's requested size |
|---|---|
| `paper` | **25%** |
| `small-size-paper` | **25%** |
| `live-paper` | **50%** — requires Mike's approval per the spec |
| anything else | **25%** |

The Kelly slot is present in the code and explicitly unpopulated. When a Bayesian Updater
exists, it fills that slot; nothing else needs to change.

## Manual override

Mike can clear any switch with a manual flag. The spec's open question — whether the
override should require a second factor such as a signed token — is left as **flag-only**,
its documented current state.

Every set and clear is written to `risk.kill_switches` append-only with its actor and
reason, so a flag-only override is still a fully audited one.

## What this policy does not do

- It does not prevent losses. It contains them. A strategy with negative edge loses money
  at the sized rate until the Evaluator retires it.
- It does not check margin or borrow for shorts. The Strategy Runner cannot open a short
  (`_invested` treats a negative weight as flat), so no short reaches this gate. If the
  Runner ever becomes short-capable, this policy needs a borrow section before that ships.
- It does not net risk across accounts for sizing — only for the firm-level cluster cap.
- It does not stress-test. That runs offline in the Evaluator.

## Open questions for Mike

1. **Cluster cap at 15% vs ticker cap at 20%** — the cluster rule binds first for any
   single concentrated name. Intended, or should the cluster cap sit above the ticker cap?
2. **Daily loss at 2% of NAV** — on $10,000 that is $200, which an ordinary session can
   reach on a ten-name equity book. Too tight?
3. **Strategy drawdown at 25%** — the weakest-evidence number in this file.
4. **Regime multiplier taking the band's low end** — correct for a veto authority, but it
   means an absent Regime Classifier runs the firm at quarter size.
5. **Flag-only override** — unchanged from the spec, but now that every switch is audited,
   is a second factor still wanted?

## Version history

- **v0.1** (2026-07-29) — first cuts. Created alongside the Risk Officer implementation;
  the document the spec had assumed existed since 2026-05-29.
