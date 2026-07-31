# Session handoff — 2026-07-31

**Read this first, then `docs/roadmap/implementation-timeline.md`.**

**Run `make doc-drift` before you trust this file.** It was 58 PRs behind on
2026-07-31 while `CLAUDE.md` named it ground truth — the third and worst
recurrence of that failure. `main` is at **#175**.

The prior handoff (2026-07-28) is preserved below the line: its rulings on
capital, the growth target and risk limits are still in force and were never
superseded. What changed is everything after them.

---

## State of the firm, measured 2026-07-31

Not inferred from documents. This is a full systems check against the running
Dell and the database.

| | |
|---|---|
| Always-on containers | **34**, all `running` |
| Unhealthy | `langfuse`, `qdrant` |
| Ingest | six legs, `sec-edgar` 8 minutes old; `doe-newsroom` **2 days** stale |
| Filter backlog | **0** — 5,177 of 5,177 items scored |
| Items ever judged relevant | **2**, both fossils (see KI-009) |
| Clusters ever synthesized | **0** — 16 rows, all `held-single-source` |
| Strategies | 12 killed, 2 hypothesis, **0 promoted, 0 live** |
| Evaluations | 26; the most common verdict is `hold-for-data` (13), not `kill` (9) |
| Orders | none since **2026-07-29 13:32**; all 141 rows have a blank `account_id` |
| Risk Officer decisions | **1**, ever |
| Market data | 50 tickers, **2 days stale**, no automated ingest |
| Box load | every agent at 0.00% CPU, ~40 MB, against 31 GB |

**The firm has never promoted a strategy, and its research funnel has never
admitted an item.** Everything else is plumbing that works.

## What is next, in order

1. **The archetype bar experiment** (timeline 1.4, spec in
   `docs/research/archetype-bar-experiment.md`, PR #173). Prompt v4 has admitted
   **nothing** across 2,472 verdicts, and the first shadow eval proved that is
   not a model problem — five models across four usage tiers and four families
   returned 0% relevant with zero disagreements. The remaining explanation is
   the taxonomy, and this card produces the evidence Mike rules on. **Step 1
   (the spec) is merged; step 2 is the harness, not yet built.**
2. **Forward-test scoring.** Nothing evaluates a strategy *after* promotion.
   More urgent under ADR-0016, not less.
3. **The runtime gaps below** — cheap, and two of them are silently corrupting
   results.
4. **Intraday bars (2.8) → Runner firing intraday (2.9) → intraday equities
   (2.10).**

Risk Officer limits (2.7) are **done** (#146) and removed from this ordering.

## Runtime gaps found 2026-07-31 — see KI-022 to KI-025

- **Alerts reach nobody.** `discord_webhook_url: null`, `ntfy_url: ""`, and
  `ops.alert-delivery-failed` holds 8 events. #167's freshness alarms fire into
  nothing. Fix is one `.env` line and a recreate. **Mike's, not an agent's.**
- **Nothing auto-ingests price bars.** `market-data` is `--profile tools`, so
  bars only advance when a human runs the backfill. Every evaluation since
  2026-07-29 ran on stale prices — which may explain some of the 13
  `hold-for-data` verdicts.
- **11,096 reconciliation discrepancies vs 9,161 clean passes.** Nobody has ever
  read them.
- **Streams grow unbounded.** `ops.health-tick` is at 80,509 and nothing trims.

## The three findings that mattered this session

1. **The taxonomy rejects everything, and it is not the model's fault.** Two
   flagship tiers, four families, 0% relevant. KI-009 is a taxonomy problem with
   a number behind it now, not an inference.
2. **The instrument was lying twice before it told the truth.** The shadow eval
   counted unparsed answers as agreement (#171), then scored fence-wrapped JSON
   as an unparseable verdict (#172) — a defect in *production* code that made
   `glm-5.2` look 25% competent. Both were caught by running the thing against
   real models, not by the test suite.
3. **Neither compute nor inference budget is a constraint.** The Ollama Pro
   allowance ran at 1.2% of weekly for 3,320 requests, and the Dell is idle.
   Two design hedges made on cost this session were wrong.

## Standing constraints a new session must not rediscover

- **Paper only.** Credentials live in gitignored `infra/.env`; never printed,
  committed or pasted. Check presence and length only.
- **The Dell is pull-only for git.** No write token on a production deploy box.
  Never `sudo git` in the repo — it creates root-owned objects that break pulls.
- **One card per PR, branched off `main`.** Never stack (KI-001).
- **Do not append to the tail of a file another open PR touches** (KI-016). Two
  correctly independent PRs doing that merged into a `SyntaxError` and left the
  whole suite uncollectable on `main`. A note in a PR body is not a check — if
  two cards interact, decouple the test rather than sequencing the merges.
- **`docker compose run` never rebuilds.** Build first, and rebuild *every*
  service whose source a change touched — `strategy-evaluator` and
  `strategy-evaluator-trigger` share one Dockerfile.
- **Restarting a stream consumer does not replay acked events.** `start_id`
  applies at consumer-group creation only, so a logging or handling fix cannot be
  verified against old events.
- **The Risk Officer is a library, not a service.** No container to check; it
  lives and dies with `pre-trade-checker`.
- **`shraptasmaner` and `ib-gateway` are not Shrap's.** The Dell is not
  dedicated to this project. `shraptasmaner` is Mike's earlier
  convergence/divergence prototype and has been crash-looping on a missing
  entrypoint for ~13 days; ignore it in any Shrap health reading.

## Still unverified live

- **#100's Librarian INFO fix** and **#103's Evaluator trigger** — both
  unit-tested, neither observed in production.
- **The three-account split.** Every `paper_order_events` row has a blank
  `account_id` despite #124–#128. Either attribution is unwired or nothing has
  flowed through it since.

---

# Prior handoff — 2026-07-28

**Rulings below remain in force.** Only the "what is next" ordering has been
superseded, by the section above.

## Mike's rulings, 2026-07-28

### 1. Capital and risk appetite

**$10,000 per paper account. It may be aggressive.** Multiple paper accounts are
sanctioned — Mike will create them — split by horizon so long- and short-term
strategies are tested in parallel without netting against each other.

Multiple accounts are not a convenience. Both the Execution Agent and the
Reconciliation Agent hold one `alpaca_api_key`, so a single account nets
positions across strategies: two strategies wanting opposite sides of the same
name cancel at the broker, and per-strategy P&L cannot be attributed at all.
Per-account books **delete KI-005 (position-state derivation) rather than
solving it** — per-strategy P&L becomes that account's equity curve.

### 2. The growth target: **35% a year**

**Mike's ruling (2026-07-28): the target is 35% annually.** He raised 1%/day
first, then set it aside himself as too ambitious. Both numbers are recorded here
because the difference between them is the most useful thing in this document.

| | 1%/day (set aside) | **35%/year (the target)** |
|---|---|---|
| Compounded | 12.3x/year (+1,130%) | **1.35x** |
| Per trading day | 1.00% | **0.119%** |
| Per month | ~21% | **2.5%** |
| Implied Sharpe at 15% book vol | ≈ 10.6 | **≈ 2.1** |
| Implied Sharpe at 25% book vol | — | **≈ 1.2** |
| Sharpe run by elite quant funds | 2–4 | 2–4 |

**This is the difference between impossible and hard.** 1%/day sat an order of
magnitude beyond anything documented. 35%/year at moderate volatility implies a
Sharpe of roughly 1.2–2.1 — inside the range real funds actually run, at the good
end of it. It is a target the firm can be honestly measured against, which the
previous one was not.

#### What 35% requires, stated as a number

The promote gate is the information ratio against equal-weight buy-and-hold. If
the benchmark returns ~10% and the book runs 10–15% tracking error, reaching 35%
needs a **portfolio** information ratio of roughly **1.7–2.5**.

The promote floor is **0.5**. That is a floor for admitting one strategy, not a
target — and uncorrelated strategies add in quadrature (portfolio IR ≈ IR × √k):

| Per-strategy IR | Uncorrelated strategies needed for portfolio IR 1.7 | for 2.5 |
|---|---|---|
| 0.5 (bare promote floor) | ~11 | ~25 |
| 1.0 | ~3 | ~6 |
| 1.5 | ~1–2 | ~3 |

**So the binding constraint on 35% is the number of genuinely uncorrelated
strategies the firm can find and keep, not the quality of any single one.** That
is a direct ranking of the roadmap: research throughput — the Hypothesis
Generator, the Sweep Detector, more archetypes — matters more than tuning
anything already built. One strategy at the promote floor does not get there and
never will.

The caveat that makes the table honest: "uncorrelated" is doing heavy lifting.
Fifty US equities in a drawdown move together, and so do most long-only equity
strategies over them. Real diversification likely requires different *horizons*
and eventually different *instruments* — which is the same argument the fast
layer, intraday data, and eventually options and futures were already making.

**What the target must never become:** a reason to lower `DEFAULT_MIN_TRADES`,
the Sharpe floor, or the information-ratio floor. A firm that hits 35% by
relaxing its gates has not hit 35%; it has stopped measuring. Every gate in
`docs/research/eval-protocol.md` exists because a specific failure was caught in
the act, and three of them were caught in the session before this one.

### 3. Risk limits — **applied**

Raised in the risk-limits card, once sizing (#117) made it meaningful. Before
that, raising `MAX_QUANTITY_PER_ORDER` would have sent *N shares of everything* —
as disconnected from a strategy's weights as one share was, just larger. That
ordering was the whole point of splitting #115.

| Setting | Was | Now |
|---|---|---|
| `MAX_QUANTITY_PER_ORDER` | 1 | **100** — a $1,000 slot buys 100 shares at $10; binds only on cheap names |
| `STRATEGY_RUNNER_MAX_QUANTITY` | 1 | **100** — reads the *same* compose variable, so the two cannot diverge |
| `MAX_ORDERS_PER_DAY` | 10 | **80** — covers entering all 50 names from flat, with headroom |
| `ALLOWED_UNIVERSE` | 6 smoke names | **the 50-name launch list**, imported from `launch_list.py` so it cannot drift |
| `SYMBOL_COOLDOWN_SECONDS` | 300 | unchanged — the guard against a signal loop hammering one name |
| `KILL_SWITCH_ACTIVE` | false | unchanged — it exists and works |
| `TIER3_ENFORCEMENT` | false | unchanged — see below |

**Tier 3 is still the intended end state**, and it stays off until
`research.universe_tiers` is populated: enforcement fails closed, so flipping it
against an empty table vetoes every order. The ordered procedure is
`docs/runbooks/enabling-the-50-name-universe.md`. Trading 50 names does not
depend on it — the static allowlist already covers them.

**The share cap is a weak backstop, and should be read as one.** 100 shares of a
$700 name is $70,000, seven times the account. What actually bounds position size
is the Runner sizing to a target weight, plus the broker rejecting orders beyond
buying power. A notional cap would be the real control; it needs a price on the
intent, which market orders do not carry. Its own card.

**A constraint at this account size:** a 10% slot on $10k is $1,000, so any name
above $1,000/share cannot be held at a full weight. The sizer reports this rather
than silently holding zero. Fractional shares would fix it and Alpaca supports
them — that is its own card.

---
