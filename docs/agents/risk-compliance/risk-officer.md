# Risk Officer

**Department:** Risk and Compliance
**LLM tier:** `no-llm` for the deterministic core — the Risk Officer is the firm's circuit breaker;
its decisions must be reproducible, auditable, and free of LLM unreliability. A
`cloud-default`-tier call MAY be used downstream of an already-made veto/escalation decision
to draft human-readable escalation language (Slack/email body for Mike); that drafting
cannot influence the decision itself. See `docs/infrastructure/llm-routing.md` and
`docs/infrastructure/llm-registry.md`.
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** Draft
**Date:** 2026-05-29
**Author:** Mike White
**Version:** 0.1 (draft)

## Purpose

The Risk Officer is the firm's single point of veto. Every order intent from the Decision
Maker passes through it. Every position, every aggregate exposure, every correlation
cluster, every regime-conditional limit is enforced here. **The Risk Officer has VETO
power over all other departments — including the Decision Maker, including a Mike-issued
intent, with the sole exception that Mike can override the Risk Officer by toggling a
documented manual flag that is itself audited and rate-limited.**

This is the most important guardrail in the firm. It exists because every disaster
narrative in retail trading ends the same way: a sizing failure, a correlation failure,
or a missing kill switch. The Risk Officer's job is to make those failures impossible by
construction, not to merely make them unlikely.

**Real-money execution is out of scope for the entire sprint.** The Risk Officer enforces
this by hard-coding a check: any intent whose `mode` is not `paper` is rejected with
veto reason `REAL_MONEY_FORBIDDEN_DURING_SPRINT`. Enabling real-money mode requires a
specific code change, a post-sprint ADR, and Mike's signed approval. The constant lives
in the repo, not in a config file an agent could change. The Implementation Agent is
explicitly forbidden from touching this code path (see `development/implementation-agent.md`).

What this agent cannot do:
- It cannot detect every form of market manipulation against the firm. It can only
  enforce documented limits. A novel manipulation pattern outside its rules will not
  trigger it.
- It cannot prevent losses — only contain them. A strategy with negative edge will still
  lose money at the sized rate until the Strategy Evaluator or Bayesian Updater retires
  it.
- It cannot itself decide whether a strategy *should* be live; promotion stage gates that.
  It only decides how much size that stage permits.

## Trigger

- **Schedule:** Continuous. Plus a 5-minute heartbeat that re-checks all open positions
  against current limits — limits may tighten when regime changes.
- **Event:** Subscribes to `trading.decision.intent` (pre-trade check), `execution.fill`
  (post-fill exposure update), `research.regime.changed` (re-evaluate caps),
  `bayesian.posterior.updated` (re-evaluate Kelly fraction), `ops.health.degraded`
  (data integrity issues force precautionary halts).
- **On-demand:** Mike-issued `risk.kill_switch.set` / `risk.kill_switch.clear`.

## Cross-references

**Depends on:** Decision Maker (input), Bayesian Updater (posterior per strategy),
Regime Classifier (regime label and confidence), Execution Agent (fill confirmations),
Reconciliation Agent (position truth).
**Depended on by:** Decision Maker, Execution Agent, Daily Briefing, Alert Agent, every
other agent indirectly (kill switch state).
**Related ADRs:** ADR-0006 (envelope); forthcoming no-real-money ADR; forthcoming
sizing-policy ADR.
**Related architecture sections:** `docs/02-architecture.md` §Risk and Compliance,
§Sizing model, §Kill switches.

## Inputs

| Source | Type | Description |
|---|---|---|
| Redis: `trading.decision.intent` | Event | Order intents requiring approval |
| Redis: `execution.fill` | Event | Fill confirmations updating live exposure |
| Redis: `research.regime.tick` | Event | Current regime label and confidence |
| Redis: `bayesian.posterior.updated` | Event | Posterior edge estimate per strategy |
| Redis: `ops.health.*` | Event | Data freshness, exchange connectivity, reconciliation breaks |
| PostgreSQL: `trading.positions`, `trading.fills` | Query | Position truth |
| Repo: `docs/risk/policy.md` | File read | Authoritative limit policy — versioned, requires Mike-approved PR to change |

## Processing

### Pre-trade check (per intent)

1. **Hard invariants.** Reject if `mode != paper` (sprint). Reject if kill switch is
   active. Reject if `ops.health` for required data sources is `degraded` beyond
   tolerance.
2. **Ticker eligibility.** Reject if ticker not in current universe, if there is an open
   halt, or if the EXTREME-news block is set.
3. **Per-strategy size.** Compute target size = Kelly fraction × posterior edge ×
   regime-fit multiplier. Kelly fraction is **25% by default, capped at 50%** of full
   Kelly; the fraction is set per promotion stage (paper = 25%, small-size-paper = 25%,
   live-paper = up to 50% pending Mike approval). Kelly inputs come from the Bayesian
   Updater's posterior, not from raw backtest Sharpe.
4. **Per-ticker cap.** Total exposure to one ticker across all strategies is capped at a
   regime-dependent fraction of NAV (defaults documented in `docs/risk/policy.md`).
5. **Correlation-adjusted portfolio cap.** Cluster open positions by recent realized
   correlation. Sum exposure within each cluster. Cap per cluster, not per name. This is
   the single most-important defense against the "everything is one trade" disaster.
6. **Concentration and gross/net.** Hard caps on gross exposure, net exposure, sector
   concentration, and overnight inventory by regime.
7. **Velocity.** Cap orders/min and orders/day per strategy and per ticker to prevent
   runaway loops.
8. **Decision.** `approve(size=X)` or `veto(reason=…)`. If approved at less than the
   requested size, the intent is **scaled down**, not rejected.
9. **Emit.** `risk.intent.approved` or `risk.intent.vetoed`, with full reason payload.

### Continuous monitoring (heartbeat + on fills)

1. Recompute exposure, correlation clusters, P&L vs daily-loss limit.
2. If any limit breaches: emit `risk.alert` at severity = `warn` or `breach`. On
   `breach`, set the relevant kill switch automatically per policy (e.g. daily-loss
   breach = `kill_switch.daily_loss` set, halting new intents firm-wide; existing
   positions follow each strategy's exit logic).
3. Per-strategy auto-pause: if a strategy hits its strategy-level drawdown limit, set
   `kill_switch.strategy.<id>` and notify.

### Regime-change response

On `research.regime.changed`: re-evaluate all open positions. Strategies whose new regime
is in their `regime_kill` list are flagged for orderly exit. The Risk Officer does not
itself send exits — it tightens sizing on those strategies to zero new entries and
emits an alert that the Decision Maker / strategy logic should close out.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis stream: `risk.intent.approved` | Event | Approved intent with sized quantity |
| Redis stream: `risk.intent.vetoed` | Event | Vetoed intent with structured reason code |
| Redis stream: `risk.alert` | Event | Severity ∈ {info, warn, breach}, payload describes the limit |
| Redis stream: `risk.kill_switch.set` / `risk.kill_switch.clear` | Event | State transitions, auditable |
| PostgreSQL: `risk.decisions` | Append-only insert | Every approval, every veto, every alert |
| PostgreSQL: `risk.kill_switches` | Append-only | State history of every switch |

## LangGraph structure

Not used. Implemented as a deterministic Python service. LangGraph would add latency to a
critical path without benefit.

## State

| What | Store | Notes |
|---|---|---|
| Current kill-switch state | Redis hash `risk:switches` | Mirrored to Postgres on every change |
| Live exposure snapshot | Redis hash `risk:exposure` | Recomputed on every fill and heartbeat |
| Correlation matrix (recent) | Redis (object ref) | Refreshed every 5 minutes |
| All decisions and alerts | PostgreSQL `risk.*` | Append-only, forensic substrate |

## Failure behavior

1. **Containment.** A bug in the Risk Officer that fails *open* (approves something it
   shouldn't) is among the firm's worst failure modes. Defenses: (a) the real-money
   invariant is enforced as a compile-time/code-level constant, not a config; (b) the
   Execution Agent independently re-checks `mode == paper` before submitting any order;
   (c) the Reconciliation Agent will scream if positions diverge from expected. A bug
   that fails *closed* (rejects everything) halts trading but is safe.
2. **Replay safety.** Fully safe. The Risk Officer is a pure function of policy + state.
   Replays from the decisions log are deterministic. Kill-switch state is rebuildable
   from its append-only log.
3. **Degraded operation.** The firm **cannot trade** without the Risk Officer. If the
   service is down, the Execution Agent rejects all intents. This is the correct
   behavior. There is no acceptable degraded mode — uptime is the Operations
   Department's responsibility.

## Sprint scope

- Month 1: Hard invariants, kill-switch infrastructure, the real-money block, per-ticker
  caps, daily-loss limit. No Kelly sizing yet — flat per-stage size limits.
- Month 2: Kelly-fractional sizing using Bayesian posteriors. Strategy-level drawdown
  switches.
- Month 3: Correlation clustering and cluster-level caps. Regime-aware caps.
- Month 4: Refined limit tuning based on live-paper observation. No real-money in scope.

## Deferred

- Real-money execution (post-sprint ADR, signed approval).
- Pre-trade margin/borrow checks for shorts (sprint may exclude shorts entirely).
- Cross-account or multi-portfolio risk netting (single portfolio in sprint).
- Stress testing as part of pre-trade — runs offline.

## Open questions

- **Per-cluster cap as % of NAV:** First cut 15%. Blocks: meaningful enforcement. Owner:
  Mike.
- **Daily-loss limit:** First cut −2% of NAV equivalent on paper. Blocks: kill-switch
  trigger. Owner: Mike.
- **Should Mike's override of a Risk Officer veto require a second factor (e.g. a
  signed token), or just a manual flag?** Currently flag-only. Blocks: production
  governance. Owner: Mike.
- **Kelly inputs when posterior is thin (< N trades):** Fall back to flat fraction?
  Currently yes, at the lowest tier. Blocks: first paper sizing. Owner: Mike.
