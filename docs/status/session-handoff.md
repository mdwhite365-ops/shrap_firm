# Session handoff — 2026-08-04

**Read this first, then `docs/roadmap/implementation-timeline.md`.**

**Run `make doc-drift` before you trust this file** — and know what it does not
tell you. It compares PR numbers, not claims. On 2026-08-04 it reported every
status doc `ok` while this file said *"Orders: none yet"* on a day the firm had
filled six. A green drift check means the file is recent, not that it is true.

The **2026-07-28 rulings** are preserved below the second divider and remain in
force. Prior measured state is **replaced, not kept**, whenever its headline
claims go false; leaving them adjacent to current numbers is worse than losing
them, and `git log` has the history.

---

## The firm traded

**2026-08-04, 13:30 UTC: six orders, six fills, on `PA3KQN57WVXY`.** The first
time a Research strategy's signal reached a broker fill. Signal → intent → risk
→ order → fill, no human in the path.

It also produced the firm's first two trading defects, both found and fixed the
same day, both now verified in production rather than only in tests.

**KI-030 — the Runner sold stock the account did not own.** Three of the six
orders were exits, and the account had never held the positions being exited:
COIN −1, UUP −6, RIVN −12, short, on long-only strategies. The Runner decided
"am I invested" from its own record of *intent*, and intent had diverged from
position in two ways — Monday's 20 signals were vetoed (KI-029) but stamped as
held, and every order is scaled by the Risk Officer, so a recorded intent of 52
GME became a 9-share fill. Closing on 52 shorts 43. **#192** makes
`ops.position_snapshots` authoritative for both the flag and the exit quantity.
Mike flattened the three shorts by hand.

**KI-031 — the status loop had stalled a month on the firm's first order.**
Every order read `pending_new` in `trading.paper_order_events` while Alpaca
showed them filled. The Order Store was blameless; the Execution Agent had
stopped publishing. Two stacked bugs: an account filter that read *unstamped* as
*mine*, and a 404 classified as retryable. Each agent reached the firm's
first-ever order — stream id `1783203414014-0`, **2026-07-04 22:16:54** — claimed
it for want of a stamp, 404ed on a book it did not own, and jammed there. **#193**
fixed both; on deploy the backlog drained and `execution.order.filled` went
**47 → 53**, exactly the six.

Note what a stalled loop costs permanently: `status-updated` did not move at all,
because by the time the loop reached these orders they were already terminal.
**Intermediate states are not recoverable — only the final one is.**

## The research funnel (unchanged since 2026-08-03, still true)

**KI-009 is resolved, and it was an ingest defect.** The Tech Watcher had been
storing EDGAR's Atom *index entry* — a filed date, an accession number and a
file size — rather than the filing. 72% of the corpus was document metadata.
After #189 fetched the bodies and `--force` re-scored them, `sec-edgar` admitted
**46 items** where it had admitted **zero** in two months. Firm-wide: 2 fossils
to 49. On 2026-08-02 the funnel synthesized and proposed its first pipeline
candidate, `haleu-cost-curve` — ingest through proposal, six stages, no human in
the path.

The previous handoff's sentence *"The firm has never promoted a strategy, and
its research funnel has never admitted an item"* is half false and worth
dwelling on. Three independent rounds of evidence — a five-model shadow eval, a
three-bar archetype experiment, 2,472 v4 verdicts — all pointed at the taxonomy.
Every one was measured on a corpus that was mostly file sizes. **A denominator
made of metadata makes every rate a statement about the metadata.**

## The forward test

Two strategies sit at `paper` as deliberate systems tests, **not promotions** —
neither cleared the promote gate and the transition reasons say so. Both declare
**no cadence, so both are daily**: the Runner wakes every 60s while a session is
open, but a strategy with no declared cadence acts once per session and every
later tick is a no-op.

**The book is not flat.** `PA3KQN57WVXY` holds **SOFI 10, GME 9, PLTR 1** —
legitimate entries from 2026-08-04, left in place deliberately. Only the three
phantom shorts were closed. The next session is therefore the first real test of
the **exit** path under #192: any of those three the strategy rotates out of
should sell exactly the quantity the account holds, not the quantity the Runner
once intended.

## State of the firm, measured 2026-08-04

| | |
|---|---|
| EDGAR items with a document body | **3,696 / 3,696**, avg 5,834 chars (was 179) |
| Items ever judged relevant | **49** — 46 `sec-edgar`, 2 `usaspending`, 1 `federal-register` |
| World-changer candidates | 1 promoted (fission), **1 proposed by the pipeline** |
| Strategies | 12 killed, **2 at `paper` with accounts**, 0 promoted by verdict |
| Evaluations | 26 — 14 `hold-for-data`, 12 `kill` (see KI-027) |
| Orders | **6 submitted, 6 filled** 2026-08-04 — the first ever |
| Positions | `PA3KQN57WVXY`: SOFI 10, GME 9, PLTR 1. The other two accounts flat |
| Paper accounts | `PA3HEG2CLXLU`, `PA3KQN57WVXY` assigned; `PA3YPMG9AD4Z` idle |

## What is next, in order

1. **Watch the next open — the exit path this time.** The entry path is proven.
   What has never run is a sell against a real holding. Read `strategy-runner`
   logs for the sizing basis before the order table.
2. **Rule on `haleu-cost-curve`.** `shrap-tech-watcher-review` renders it. The
   question is whether it is a distinct thesis or a rung of the promoted fission
   one; a duplicate kill is a legitimate and useful outcome.
3. **Intraday bar *reading* — the remaining piece of day trading.** #185 ingests
   1-min bars and #186 lets the Runner act on a cadence, but nothing connects
   them: `BarSample.session_date` is a `date`, and that type runs through
   `PanelWindow`, `PricePanel`, the Evaluator and every strategy. **Declaring an
   intraday cadence today would only re-run a strategy against a panel that
   still changes once a day.** This is a type change through the core of the
   strategy layer, not a config flag.
4. **Position staleness at intraday grain (new, unfiled).** The Runner reads
   positions from `ops.position_snapshots`, which the Reconciliation Agent
   refreshes every **300s**. Free at daily cadence; wrong at a 5-minute one,
   where a pass can plan against a book that already moved. A precondition for
   (3), not a separate feature.
5. **KI-027** — `hold-for-data` cannot resolve, and 14 evaluations sit in it.
   A rename or an expiry, not a calibration change.

## Rulings made 2026-08-01/04

- **Intraday feed: Alpaca IEX 1-min.** Free, reuses the existing client. The
  documented IEX volume bias is survivable at daily grain and materially worse
  at 1-min — a strategy that looks good on it must be re-checked against SIP
  before it means anything.
- **Both tracks in parallel:** stage the two strategies for a forward test *and*
  run the search. The search half turned out to be blocked upstream, not at the
  Generator: 6 of 111 q-fin papers were accepted and all 6 already consumed.
- **IR floor stays at 0.5.** See KI-027 for why lowering it would promote
  strategies a leverage dial beats.
- **A short on a long-only strategy is a human's problem, not the Runner's**
  (#192). It is skipped and reported, never sold — selling would deepen it. The
  firm stops and says so rather than acting on a book it did not choose.
- **Operator corrections leave no event.** The three shorts were closed in the
  Alpaca dashboard, so `ops.position_snapshots` shows the result and the event
  log shows six orders that filled and no record of anything closing them. That
  is correct, not a defect: the broker knows the book, the event log knows what
  the *firm* did, and only one of those includes Mike. Do not reconcile them.

## Things that were believed and turned out false

Recorded because each cost real time and the shape recurs.

- *"The literature filter is the bottleneck, like the world-changer one."* It is
  not. It accepted 5% of 111 papers and the Generator consumed all of them — the
  corpus is exhausted, not the filter. The two funnels have different prompts
  and different corpora and keep not behaving alike.
- *"qwen3.5:397b will rescue rejected q-fin papers."* It dropped 3 of the 6
  previously accepted and rescued none. Stricter, not more permissive — the
  opposite of its behaviour on the world-changer corpus.
- *"Two of the four unused papers justify the EDGAR card."* Both were among the
  three qwen then dropped. KI-026's own justification stood on its own; the
  extra argument did not.
- *"A dry run that reports zero changes measured something."* Twice (#183, #187).
  Both printed counts derived from an empty tuple in the shape of a result.
- *"The shorts are flat."* They were not. The 2026-08-04 mitigation had two
  halves — reset the Runner's phantom state rows, and close the positions — and
  only the first ran. The state reset was reported as though both had. **A
  mitigation with two steps is not done when one of them is.**
- *"The Order Store is not persisting the fill events."* It was. Its row counts
  match the Redis streams exactly; the conclusion came from a two-day query
  window on a table with a month of history. The producer had stopped, not the
  consumer. **Bound the window to the question, not to the recent past.**
- *"ruff is clean."* `ruff check` was; `ruff format --check` was not, and CI runs
  both. `make lint` is the gate — running half of it and reporting the whole is
  how #192 arrived red.

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
- **The exit path.** #192 is deployed but no sell against a real holding has run.
  Three positions are waiting for it.

**Now verified, previously listed here:** the three-account split. All six
2026-08-04 order rows carry `account_id = PA3KQN57WVXY`; #124–#128 work and
nothing had flowed through them since. *"Either it is unwired or nothing has
used it"* held for six days and resolved to the second — worth remembering the
next time a table looks broken and has simply been idle.

## Reading the trading path when it goes quiet

Ordered by how often each was the answer, learned 2026-08-03/04:

1. **`pre-trade-checker` logs.** A veto with a stated reason is a working
   system. All 20 of 2026-08-03's signals died here (KI-029).
2. **`trading.paper_order_events` grouped by `event_topic`, over all history.**
   Counts and `max(occurred_at)` per topic localise a break to a stage in one
   query. A window shorter than the table's history will mislead you.
3. **Redis `XLEN` on the three `execution.order.*` streams.** Compared against
   those DB counts, this bisects producer from consumer in one step. Equal
   counts exonerate the store.
4. **The agent's own logs.** They name the exception. Reach for them before
   inferring a cause from behaviour — on 2026-08-04 two confident inferences
   were wrong before the logs settled it in one line.

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
