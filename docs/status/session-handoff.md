# Session handoff — 2026-07-28

**Read this first, then `docs/roadmap/implementation-timeline.md`.**

`main` is green (`739 passed` with #117), CI runs on every push and PR. Cards
#102–#116 merged; **#117 (notional sizing wired) is open**.

---

## Mike's rulings this session

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

## What is next, in order

1. ~~**Wire notional sizing into the signal path.**~~ **Done — #117.** Entries are
   `target_weight × equity / price`, floored, with equity read from
   `ops.account_snapshots`.
2. ~~**Apply the risk limits.**~~ **Done — the risk-limits card** (table above).
   Together with #117 this is the point where the book starts expressing real
   weights across 50 names rather than one share of six.
3. **Forward-test scoring.** Nothing evaluates a strategy *after* promotion — the
   Evaluator spec's Sunday re-evaluation was never built, so a promoted strategy
   can decay silently. This is the missing half of "test them forward and back",
   and with kills already autonomous under ADR-0015 a decayed strategy can be
   retired without waiting on review. **ADR-0017 supplies the metric:** realised
   account growth since deployment over max drawdown since deployment. It needs
   item 4 first — the score is an account's equity curve, so the firm has to know
   which account it is looking at. Do not annualise a three-week sample.
4. ~~**Account identity.**~~ **Done — the account-identity card.**
   `ops.account_snapshots` now carries `account_id` (the broker's own
   `account_number`, so identity comes from the venue rather than a config value
   someone could mistype), and the Runner reads equity scoped to
   `STRATEGY_RUNNER_ACCOUNT_ID`.

   **Operator step, required before the Runner will trade:** set
   `STRATEGY_RUNNER_ACCOUNT_ID` in `infra/.env` to the Alpaca account number.
   Unset refuses every pass — deliberately, since the alternative is sizing
   against whichever account reported last.

5. **Per-strategy account routing** (ADR-0017). One runner currently serves one
   account. Three accounts, one strategy each, needs a strategy → account
   assignment and either three runner instances or an account-aware one.
   Execution and Reconciliation still hold a single `alpaca_api_key` apiece.
6. Then, stocks first (ADR-0016 sequence, Mike 2026-07-29 — futures and crypto
   later): intraday bars, a Runner that fires more than once a session, and the
   intraday equities path. Futures/crypto cards stay parked.

**On the Dell, after these merge:** rebuild both `pre-trade-checker` and
`strategy-runner` (`docker compose run` never rebuilds), then confirm the new
caps in the checker's startup log. Nothing needs to be loaded or migrated by
hand — the `last_quantity` column migrates itself at startup.

---

## The three findings that mattered this session

Each was found by measuring rather than reasoning, and each would have produced
a strategy that looked fine and wasn't.

1. **The promote gate could not tell skill from market exposure** (#112). Naive
   buy-and-hold with no timing rule scored Sharpe 1.03–1.16 through the engine on
   drifting data, clearing the 1.0 floor purely by being invested. Fixed with
   benchmark-relative evaluation: the gate is now the information ratio against
   equal-weight buy-and-hold of the strategy's own universe.
2. **Strategies could only trade one ticker** (#110). The engine was always
   cross-sectional; one line in the factory discarded every ticker but the first.
   This invalidated a claim I had repeated four times — that a daily-bar rule
   *cannot* clear the 150-trade gate. True per instrument, false across a
   universe: 89 trades on one ticker, 28,139 on fifty.
3. **The Runner never sized positions** (#115). It emitted one share and never
   read account equity, so a strategy evaluated as equal-weight would trade at
   7.5% of book for a $750 name and 0.5% for a $50 one.

---

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

## Still unverified live

- **#100's Librarian INFO fix** and **#103's Evaluator trigger** — both
  unit-tested, neither observed in production. Both need a fresh
  `research.strategy.verdict`, which the momentum seed (#114) will produce on its
  first evaluation.
- **KI-014** — the Librarian and Runner were started but never confirmed running.
