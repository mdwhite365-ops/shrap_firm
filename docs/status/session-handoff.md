# Session handoff — 2026-07-28

**Read this first, then `docs/roadmap/implementation-timeline.md`.**

`main` is green (`723 passed`), CI runs on every push and PR, and **no PRs are
open**. Fourteen cards merged this session: #102–#115.

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

### 2. The growth target, and what it is for

**Mike's stated goal: grow the account 1% per day, "at least if it can."**

Recorded with the arithmetic, because the vision asks for honest probability
framing and this number deserves it:

| | |
|---|---|
| 1%/day compounded | `1.01^252` ≈ **12.3x/year (+1,130%)** |
| Best long-running fund on record | roughly **40–66%/year** |
| Sharpe implied at ~1.5% daily book vol | ≈ **10.6** |
| Sharpe run by elite quant funds | **2–4** |

So the target is roughly an order of magnitude beyond anything documented. That
is not a reason to discard it — it is a reason to be precise about what it is:
**a direction-setter that ranks the roadmap, not a threshold to tune gates
toward.**

What it correctly implies, and this is genuinely useful:

- **Daily-bar equities cannot plausibly deliver it.** A rule holding positions
  for weeks compounds at weekly frequency, not daily.
- It is therefore an argument for the **fast layer**, for **intraday data**, and
  eventually for **options and futures** — exactly the direction Mike already
  stated independently. The target and the plan agree.

**What it must never become:** a reason to lower `DEFAULT_MIN_TRADES`, the Sharpe
floor, or the information-ratio floor. A firm that hits 1%/day by relaxing its
gates has not hit 1%/day; it has stopped measuring. Every gate in
`docs/research/eval-protocol.md` exists because a specific failure was caught in
the act, and three of them were caught this session.

### 3. Risk limits — recorded, NOT yet applied

The Pre-Trade Checker still runs smoke-test values: 6 hardcoded names, **1 share
per order**, 10 orders/day.

**These must not be raised until notional sizing is wired into the signal path**
(the card immediately below). Raising `MAX_QUANTITY_PER_ORDER` today would send
*N shares of everything* — as disconnected from the strategy's weights as one
share was, just larger. Sizing first, limits second. That ordering is the whole
point of splitting #115.

Proposed for an aggressive $10k book once sizing lands:

| Setting | Now | Proposed |
|---|---|---|
| `MAX_QUANTITY_PER_ORDER` | 1 | **100** — a $1,000 slot buys 100 shares at $10; binds only on cheap names |
| `MAX_ORDERS_PER_DAY` | 10 | **80** — 10-name entry plus rebalancing headroom |
| `ALLOWED_UNIVERSE` | 6 hardcoded | **the Tier-3 table** (requires `load-launch-list`, then flipping `TIER3_ENFORCEMENT`) |
| `SYMBOL_COOLDOWN_SECONDS` | 300 | unchanged — the guard against a signal loop hammering one name |
| `KILL_SWITCH_ACTIVE` | false | unchanged — it exists and works |

**A constraint at this account size:** a 10% slot on $10k is $1,000, so any name
above $1,000/share cannot be held at a full weight. The sizer reports this rather
than silently holding zero. Fractional shares would fix it and Alpaca supports
them — that is its own card.

---

## What is next, in order

1. **Wire notional sizing into the signal path.** #115 shipped the arithmetic and
   the equity source; the Runner still emits a fixed 1 share. Until this lands
   the live book cannot match the evaluated book, and the risk limits above must
   stay where they are.
2. **Apply the risk limits** (table above) and load the launch list.
3. **Forward-test scoring.** Nothing evaluates a strategy *after* promotion — the
   Evaluator spec's Sunday re-evaluation was never built, so a promoted strategy
   can decay silently. This is the missing half of "test them forward and back."
4. **Multi-account routing**, once Mike creates the accounts.
5. Then Phase 2/3 of the timeline: intraday data decision, Sweep Detector,
   Hypothesis Generator, Langfuse instrumentation.

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
