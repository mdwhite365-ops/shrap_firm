# Session handoff — 2026-08-23

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

## Where the firm stands at sprint end

The 4-month sprint (May–Aug) is nearly over. Stated plainly, because the
temptation at this point is to describe activity rather than results:

**The firm trades autonomously, correctly, and to an effect nobody has measured
yet.** Ten sessions, 68 orders, 100% fill rate, no human in the path, and the
best account returned **+0.70%** over the fortnight.

**Against what, the firm cannot currently say.** There is no control account:
`PA3YPMG9AD4Z` looked like one — all cash, +0.66% — but its gain is Mike's own
AAPL/SPY smoke-test buys closing at a profit, so it measures a human's
discretionary trades, not a do-nothing baseline. A raw +0.70% over two weeks
means nothing without the benchmark, and the benchmark has to be computed from
`market_data.daily_bars`, not read off an account.

The two strategies running were staged as **systems tests, not promotions** —
they scored IR **0.306** against a benchmark scoring **0.876**, so a fortnight of
flat is exactly what the evaluation predicted. The forward test worked: it
confirmed a prediction the firm had already made about itself.

**The binding constraint is research throughput, not execution.** The 2026-07-28
arithmetic still holds: 35%/year needs roughly 11 uncorrelated strategies at the
promote floor, or ~3 at IR 1.0. The firm has **zero** above the floor. Every
trading defect below was worth fixing because it bought honest measurement — but
none of them would have made a bad strategy good, and fixing more of them will
not either.

## Measured 2026-08-19/20 (verify before reuse)

| | |
|---|---|
| Equity `PA3HEG2CLXLU` | **$9,990.85** (−0.09%) |
| Equity `PA3KQN57WVXY` | **$10,069.87** (+0.70%) |
| Equity `PA3YPMG9AD4Z` (**not a control** — see below) | **$10,066.19** (+0.66%) |
| Orders | 68 over 10 sessions, **100% filled** |
| Open positions | 12 and 27, for strategies that hold **ten** |
| `risk.decisions` | 155 rows, 84 approvals / 71 vetoes |

**`PA3YPMG9AD4Z` is not a control account, and must not be used as one.** It is
the original account from the smoke-test phase. Its +0.66% is Mike's manual
AAPL/SPY test purchases closing at a profit — a human's discretionary trades,
which is the one thing a control must not contain. It holds only cash now, which
is exactly what makes it look like a valid baseline at a glance.

**The firm therefore has no benchmark in its account data at all.** The correct
comparison is equal-weight buy-and-hold over the traded window, computed from
`market_data.daily_bars` — the same benchmark the Evaluator already uses for
information ratio. Until that number exists, +0.70% is a return with nothing to
judge it against, and *"the strategies are flat"* is an impression rather than a
finding.

## The trading path, fixed five times in ten days

Every one silent. None raised, none logged, all found by looking at data rather
than at alerts.

| PR | Defect |
|---|---|
| #192 | Runner sized exits from its own record of *intent*, not the broker's position (KI-030) |
| #193 | Status loop jammed a month on the firm's first order (KI-031) |
| #195 | **Two** floors compounding — 26 of 89 decisions vetoed `SIZED_TO_ZERO`, and the executed book was the cheap half of the intended one |
| #196 | Risk Officer *scaled* exits, and shorted what it could not sell |
| #198 | Exits stranded a residue because held shares were **derived**, not read |
| #199 | Audit trail **rounded** every fractional approval to a whole share |

**#196, #198 and #199 were introduced by the same session that fixed the others.**
#195 widened a type and #196 derived a value, and each broke something one layer
downstream that had already declared what it expected.

### The pattern, stated once

**A component reconstructed a fact that was already recorded, and the
reconstruction disagreed.**

- `last_quantity` reconstructed the position from intent (#192).
- `market_value / latest_close` reconstructed a share count from two different
  prices, when `ops.position_snapshots.quantity` held it directly (#198).
- An `INTEGER` column reconstructed a fractional quantity as a whole one (#199).

The corollary that costs the most time: **when a card changes a type or inserts
a stage, the question is not "does the new code work" but "what did anything
downstream already declare about what reaches it."** Both #196 and #199 would
have been caught by asking it.

## The firm's first fills, 2026-08-04 (history)

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

**The books hold more names than the strategies do** — 12 and 27 against a
top-ten mandate, as of 2026-08-19. That is #198's residue: until it deployed, no
exit ever completed, so names accumulated instead of leaving. New exits complete;
**the existing dust does not clear itself**, and anything under ~$1 notional
cannot be sold through the API at all (Alpaca's fractional minimum). Those need
liquidating in the dashboard or they stay, and each one burns an order a session
while the Runner keeps trying to exit it.

## What is next, in order

**Nothing on this list is a trading-path fix, and that is deliberate.** Five in
ten days bought honest measurement; a sixth would not buy anything else.

1. **Compute the benchmark.** Equal-weight buy-and-hold over the 50-name
   universe for 2026-08-06 → 2026-08-19, from `market_data.daily_bars`. Without
   it +0.70% is uninterpretable, and it gates every other judgement here. If a
   clean control account is wanted afterwards it needs a *fresh* one —
   `PA3YPMG9AD4Z` is contaminated by hand-placed test trades and cannot be
   scrubbed back into a baseline.
2. **Clear the sub-$1 dust** in the Alpaca dashboard. Ops, not code.
3. **Kill and re-propose `true-autonomy-implementation`.** Its falsifiers are
   inverted (see below) and `amend-criteria` is append-only, so they cannot be
   fixed in place. The kill reason should record that the *falsifiers* were
   inverted, not that the *thesis* was wrong — the graveyard's denominator
   depends on that distinction.
4. **Research throughput.** The constraint, per the 2026-07-28 arithmetic. The
   literature corpus is exhausted (6 of 111 q-fin papers accepted, all
   consumed), so this means new sources or new archetypes, not tuning.
5. **Render kill criteria for promoted candidates** on the review page. The
   promoted fission thesis — five criteria as of 2026-08-02 — cannot be reviewed
   on the review surface at all.
6. **KI-027** — `hold-for-data` cannot resolve, and 14 evaluations sit in it.
   A rename or an expiry, not a calibration change.
7. **Intraday bar *reading*.** Still the remaining piece of day trading: #185
   ingests 1-min bars and #186 lets the Runner act on a cadence, but nothing
   connects them — `BarSample.session_date` is a `date`, and that type runs
   through `PanelWindow`, `PricePanel`, the Evaluator and every strategy.
   Declaring an intraday cadence today would only re-run a strategy against a
   panel that still changes once a day. Its precondition is position staleness:
   `ops.position_snapshots` refreshes every **300s**, free at daily cadence and
   wrong at a five-minute one.

## Kill criteria were being written backwards

Two of three proposed world-changer candidates had falsifiers that fire when the
thesis **succeeds** — `"HALEU production capacity >200 t/yr by 2030"`,
`"Waymo daily miles > 1,000,000 by FY27"`. Those are milestones. A candidate
written that way cannot be killed by evidence: it dies exactly when it is right
and survives forever when it is wrong.

It also inverts the evidence log, since observations are filed against a
`kill_criterion_index` — logging "Waymo hit 1M miles" against criterion 0 records
progress toward a kill when it is confirmation.

**#200** states the rule in the synthesis prompt and contrasts a good criterion
with an inverted one *in the same metric*, because that is the failure mode: the
two read almost identically. The third candidate got it right unaided, so the
generator was unconstrained rather than consistently wrong.

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
- *"Sizing in dollars instead of shares fixes `SIZED_TO_ZERO`."* It does not.
  `(N x s)/p` and `(N/p) x s` are the same number; **the floor was doing all the
  damage**, not the order of operations. Reordering the arithmetic would have
  shipped a PR that fixed nothing measurable. The real fix was fractional
  quantities.
- *"`risk.decisions` stopped recording when #195 deployed."* 155 rows said no.
  Then *"only vetoes are recording"* — 84 approvals said no. The actual defect
  was an `INTEGER` column silently rounding, which is the plainest reading of the
  schema and needed no theory at all. **Two wrong guesses about behaviour,
  reached by reasoning, when the answer was a declaration available by looking.**
- *"The shorts are flat"* / *"the book is now flat."* Said twice, wrong twice, in
  both cases because a two-part action was reported done after one part.

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
- **#198, #199 and #200 in production.** All merged 2026-08-23, none observed
  live. #198's effect is visible as *position count falling toward ten*; #199's
  as *fractional quantities appearing in `risk.decisions`*; #200's only on the
  next synthesised candidate.
- **Whether any exit now completes cleanly.** The thing five PRs were aimed at,
  and it has never been seen working.

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
