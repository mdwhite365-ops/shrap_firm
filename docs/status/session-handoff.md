# Session handoff — 2026-09-20 (`main` at #263)

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

## Pick up here (reconciled at #263, deployed 2026-09-20)

### Changelog entries moved (#263)

**Do not append to `recent-changes.md`.** It is frozen. Write your card's entry
as a new file in `docs/status/changes/<pr>-<slug>.md`; `make changelog` reads
them in order. Appending to one shared file meant every card landed at the same
anchor and any two open PRs conflicted — four resolution passes in one evening.
See that directory's README.

### The Ollama quota has two windows, and the session one binds (#261)

**Every cost estimate this project made was priced in the weekly window.** A
599-item experiment was refused after 192 items with `weekly.usage` at **0.277**
and `session.usage` at **1.0**. `https://ollama.com/api/usage` returns both to
the firm's own key and nobody had read it — `src/shrap/llm/ollama_usage.py` now
does.

**The quota is account-wide, so a batch job starves the agents.** Twenty minutes
after that run, the Tech Watcher's hourly literature pass aborted with
`scored: 0` on five consecutive 429s. Batch CLIs now hold a **10% reserve** and
re-check it every 50 items, not only at start. `--resume-run` finishes a stopped
run by re-scoring only its errored items into the same run id, and the conflict
clause carries `WHERE error IS NOT NULL` so a recorded verdict is never
overwritten.

**Outstanding:** run `01M2YHEGZ5KSBAADGHYK96QGAY` still has errored rows — an
`A-incumbent` replay of the July item set under `kimi-k3`, 407 of 599 unscored.
Resume with:

```
docker compose exec -T tech-watcher shrap-bar-experiment \
  --resume-run 01M2YHEGZ5KSBAADGHYK96QGAY --bars A-incumbent
```

It refuses (exit 1) while the session window is spent. **Check the meter before
planning any batch of completions.**

### All three archetype bars already ran, in July (#260)

`bar_experiment_results` holds a genuine three-bar comparison from 2026-07-31 on
~599 items under `qwen3.5:397b`: hard-leg admits **A 2/454, B 2/453, C 1/454**,
and every admit across all three is one of two USASpending DOE awards. **The
hypothesis predicts B and especially C admit substantially more. They do not** —
the outcome the spec names as falsifying. The expensive full-corpus three-bar
run is probably not worth funding; only the model question remains open.

#255 claimed B and C had never run. That was wrong, and so was the first
correction. Cause: `select * from research.bar_experiment_runs limit 5 |
head -14` returned four rows and displayed one, because `report_markdown` is a
multi-line `TEXT` column. **A pipe is part of the query.**

### Market cap is a readable series (#258)

`AVAILABLE_SERIES` went from `{close, volume}` to `{close, volume, market cap}` —
its first addition ever. `volatility-rank-forecast` reclassifies `missing-data`
→ `missing-scorer`. Caps are derived from the panel's own closes, never from
`SELECT_MARKET_CAP_SQL`, which would re-read `daily_bars` on its own feed.

### The corpus index has a consumer (#259), and Langfuse stays hand-rolled (#262)

The Hypothesis Generator now retrieves prior work from the corpus index, off
unless `HYPOTHESIS_GENERATOR_RETRIEVAL` is set. Separately, Langfuse Cloud does
support SDK v4 and OTel — the #254 constraint is genuinely gone — but the
hand-rolled client stays because it records **inline** while the SDK and every
OTel exporter batch in the background, and queued spans lost on restart are
exactly the unrecoverable sample KI-018 exists to capture.

---

## Earlier: reconciled at #251, deployed 2026-09-19

**Everything merged through #251 is deployed and verified by image ID and
database, not by build log.** 37 containers up, 0 restarting.

### Full-flow sweep, 2026-09-19 — measured, not read

Flowing: order path (submitted == filled every session 9/14–9/18, 100%), daily
and intraday bars, EDGAR ingest, Filing Processor, Qdrant corpus index (107,384
points), node-exporter (`up=1`, real values), Health Monitor 8 ok / 0 degraded.
Zero orders on 9/19 is correct — it was a Saturday.

Two silent faults found and fixed, both of which had been true for days:

- **arXiv dead 48 hours** (#251). 46/46 hourly passes returned HTTP 406 from
  2026-09-17 14:17. Nothing alarmed, because the freshness check read
  `max(fetched_at)` over the whole table and EDGAR kept it fresh. Now checked
  **per source** on `research.ingest_cursors`. See
  `docs/runbooks/a-dead-ingest-source.md`.
- **The backup cron had never once fired** (#249). Zero `cronjob.run` entries in
  an unrotated `/var/log/cron.log` going back to 2026-07-17; both existing
  backups were hand-run. The script itself is proven — a run on 2026-09-19
  exited 0 and wrote all five archives. **The first genuinely scheduled run is
  02:30 the morning after the cron was registered; check `cron.log`, not
  `/mnt/backups`.**

**Still open, and it is a real gap:** the literature filter records only
acceptances (9 of 287 q-fin papers), so its *rejections* cannot be audited from
stored data. KI-009 is a question about the taxonomy, and the evidence needed to
answer it is not being kept.

| capability | state |
|---|---|
| Tech Watcher filter → `kimi-k3` | **live** — but see the binding constraint below |
| Position unrealized P&L capture | **live** — 24 positions carry it |
| Exit rules | **ARMED**: stop −10%, intraday take-profit +7% |
| Exit rules (other two thresholds) | deliberately unarmed — see below |
| Posterior sizing (`posterior_sizing`) | **off** — arming it cuts exposure to ~0.11 |
| Intraday *trading* (Runner reads intraday bars) | **live** (#234, #236, #241) — but no intraday strategy is at `paper`, and #242 measured why |
| Host metrics (CPU/memory/disk) | **live since #244** — had never been collected before |
| Container health / crashloop alerts | **live since #239** |
| Two market-data feeds side by side | **live since #245** — 74,247 IEX + 91,051 SIP daily rows |
| Filing coverage | **42 of 50 names** (#246), up from 4 |
| `ib-gateway` | **stopped and removed.** Compose restored at `infra/ibgateway/` (#243); needs a `.env` to revive. ADR-0003 gates IBKR on live capital. |

### The next three cards, in priority order

1. **The archetype bar (KI-009) — Mike-owned, and it is the only one that
   touches the binding constraint.** `kimi-k3` has scored **172 items and
   admitted 0**. Five model families now return the same answer.
   `research.literature_items` holds **9 rows**, and the Hypothesis Generator
   logs `sweep_empty` hourly. The spec is written and waiting:
   `docs/research/archetype-bar-experiment.md`, still "Proposed — spec only."
2. **Market cap** — the cheapest `capability-gap` row
   (`volatility-rank-forecast` needs only market capitalisation) and it is
   half-built: `market_data.shares_outstanding` holds 1,594 rows across 35 of
   50 names, and `SELECT_MARKET_CAP_SQL` is written and exported with **zero
   callers**. Finish the shares for 15 names, wire a factor into
   `FACTOR_SCORERS`.
3. **Smaller debts**, in order: the Risk Officer's price queries pin `source`
   but still not `adjustment` (deliberately left out of #245); Redis streams are
   unbounded (`ops.health-tick` 25,083, `operations.reconciliation-completed`
   25,027, `intel.regime.tick` 21,213); the intraday trigger has no 429 backoff;
   27 orphaned pending stream entries.

**Verified before the open: zero exits fire on the current book.** Worst
position is `U` at −9.14%, which sits 0.86% from the stop. If it trips
tomorrow that is the mechanism working, not a surprise.

### The three rulings Mike still owns

1. **Exposure.** The accounts are **84% cash** (stage 0.25 x regime 0.75 =
   0.1875). Holding selection constant, **IR is `-Sharpe(benchmark)` at every
   exposure below 1.0** — −1.152 here, independent of the level. The deadlock:
   exposure is low because edge is unproven, and a $70 return cannot prove
   edge. Raising it is one line and is not a tuning decision.
2. **Whether to cap simultaneous exits.** Nothing limits how many positions may
   exit in one pass. A market-wide drop that breaches the stop everywhere
   liquidates the book at once. That is arguably what a stop is for, and it is
   also how a stop realises a loss at the bottom. No cap was invented.
3. **The §(e) verdict line** for the filter promotion is blank by protocol, with
   one disagreement to adjudicate (a Summit Therapeutics 8-K).

### Why only two of four exit thresholds are armed

The **since-entry take-profit is unarmed on purpose.** It would sell a position
the 126-day momentum signal is actively long, and because the name likely stays
top-N the next re-rank buys it back — two-way costs to end up where you started.

The **intraday take-profit is armed** because it does not conflict: momentum
runs `skip=21` and deliberately ignores the last 21 days, since short-horizon
reversal runs *opposite* to momentum. A one-day spike lives in exactly the
window the strategy excludes.

The **intraday stop is unarmed** to keep night one to two moving parts; the
since-entry stop already covers the loss case, and a 7% intraday stop would
whipsaw the volatile names (RIOT, COIN, AMC).

### The binding constraint has not moved

KI-035 said the funnel has produced **one** strategy ever (IR −0.006) against 14
Mike-seeded factors. KI-036 added that the firm cannot reliably *tell* whether a
signal is there: the promote gate is decided by 0.11 standard errors of noise
and **~1,800 years** of history would be needed to settle it. Both point the
same way — **stop measuring harder and feed the funnel.**

Everything shipped on 2026-09-16/18 is apparatus for measuring and allocating.
None of it changes whether a strategy makes money. The remaining
`capability-gap` rows are the build list, and two of them (news text, 10-K full
text) are closable against data the firm already ingests.

**Measured again at #247, and it has got sharper.** The filter promoted in #231
has now scored **172 items and admitted none**:

| model | scored (14d) | relevant |
|---|---|---|
| `kimi-k3` | 172 | **0** |
| `qwen3.5:397b` | 2,917 | 31 (1.1%) |

Every rejection is the same shape — an arXiv paper failing a *"compute-substrate
bar requiring real-world adoption economics."* The filter is applying the
taxonomy correctly; the taxonomy admits nothing. That is KI-009 unchanged
across five model families, and no further model change can reach it.

### Known traps re-confirmed this session

- **`docker compose up -d --build` can leave the old container running.** The
  build produced a correct image; `docker inspect` showed the container on a
  different SHA and the new code absent. Only `--force-recreate` fixed it. See
  KI-039 — **always check the image ID, never the build log.**
- **arXiv returned HTTP 406** on one ingest pass. Reproduced and it returns 200,
  so transient rate-limiting; handled and logged, pass continues. Watch it — it
  is the literature leg.

---

## Measured 2026-09-17: the funnel's lifetime output is one strategy

Run against the Dell, not inferred. **KI-035** has the full working.

| source | strategies |
|---|---|
| `mike-seed` | 14 |
| `hypothesis-generator` | **1** |

That one proposal scored IR **−0.006**. The Hypothesis Generator is not broken —
it logs `sweep_empty` hourly because `research.literature_items` has **nine rows
in total** and all nine are processed. Lifetime funnel yield: ~111 papers → 9
items → 1 strategy.

Everything else the firm has ever tested is a textbook factor (momentum,
reversal, low-vol, 52-week-high, volume premium), and the evaluations correctly
find them dead. Best IR ever recorded: **0.415** against a 0.50 floor. The gate
is not too tight — no strategy has ever cleared it on the honest metric, and the
multiple-testing correction is per lineage, so unrelated experiments do not
inflate each other's bar.

**The seven `capability-gap` rows are a prioritised build list**, and the
cheapest item on it is **market capitalisation** — one field, absent from every
table, available free from EDGAR XBRL against CIKs the Filing Processor already
maps. Full table in KI-035.

**Read this before building another measurement card.** The Regime Router
(#215), intraday panel (#217/#219) and Kelly posterior (#218) are all apparatus
for measuring and allocating; none changes whether a strategy makes money. The
intraday breadth experiment ran end to end on 2026-09-17 and was inconclusive *by
construction* — `IR = IC x sqrt(breadth)` multiplies skill by breadth, the test
strategy had none at any grain, and multiplying zero by 19 teaches nothing. The
instrument works. The firm cannot yet produce the quantity it measures.

## Merged since this file was last reconciled (#209–#214)

None of it changes the headline below: the constraint is still research
throughput, and no strategy is above the promote floor.

- **#209** — Audited #208's Langfuse tracing against Langfuse's own published
  guidance. The finding was not about the code: **the deployed server is end of
  life.** `langfuse/langfuse:2` is OSS v2, and the compatibility matrix rules
  out every current client against it.
- **#210** — One `CompletionClient`, not eight. Eight modules had each declared
  the protocol themselves; structural typing made that legal until the copies
  had to agree, and two consecutive PRs each meant editing all eight.
- **#211** — **KI-033:** no position under one share could ever be closed. The
  Pre-Trade Checker vetoed fractional quantities as malformed, which was correct
  until entries themselves became fractional.
- **#212–#214** — **KI-034: the firm had never had a backup.** Not a wrong path
  — `crontab -l` returned *no crontab*, and `/mnt/backups` did not exist. Then
  the script could not reach Docker (`truenas_admin` is not in the `docker`
  group), and then its dump would not have *restored*, because `shrap` is a
  TimescaleDB database being dumped as plain Postgres.

**Three PRs to get one working backup, and each failure was only visible on the
next real run.** None were found by reading the script. The same shape as the
five trading-path fixes: the defect is not in the code you are looking at, it is
in what the code assumed about the thing it talks to.

## Open, not merged: the Regime Router (ADR-0010 §4)

Branch `phase1/regime-router-ki-012` implements the strategy-activation gate
ADR-0010 accepted on 2026-05-31 and nothing ever built — **KI-012 §4**.
Strategies gain `regime_fit`/`regime_kill`; a dormant strategy's *entries* are
suppressed while its *exits* are never blocked.

**Nothing changes on merge** — every strategy in the registry carries
`None`/`None`, so opting one in is a deliberate CLI act. It is the plumbing for
the two cards that actually address research throughput, and both depend on this
schema rather than stacking on it (KI-001):

- **Card #2 — Hypothesis Generator regime anchoring.** Tag `regime_fit`/
  `regime_kill` on every new proposal. This is the card that answers "generate
  strategies for different regime scenarios."
- **Card #3 — Strategy Evaluator.** Require both tags to promote; compute IR
  within the target regime's periods.

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

## The firm can now measure itself, and the answer is "not yet"

`shrap-live-benchmark` (#203–#206) compares a live account to an
**exposure-matched** benchmark and reports the same information ratio the
promote gate uses, computed with the same function. Run over 2026-08-06→19:

| Account | Exposure | Excess | IR | t-stat |
|---|---|---|---|---|
| `PA3KQN57WVXY` | 17.9% | +0.069% | **+0.84** | **+0.16** |
| `PA3HEG2CLXLU` | 14.7% | −0.043% | −0.45 | −0.09 |
| `PA3YPMG9AD4Z` | 0% | — | n/a | never invested |

**+0.84 is above the 0.50 promote floor and must not be used.** Nine sessions
give it a t-statistic of 0.16 against the ~2 you would need; the tool prints it
flagged `NOT MEANINGFUL` for exactly this reason. It is not evidence the
strategy works, and it is not evidence against its backtest IR of 0.306 either.

**Why the naive comparison is not the one to quote.** Account return against a
fully invested benchmark said both strategies lost. Exposure-matched, one beat
and one lost. Same data, opposite signs — and the tool prints both and says
`DISAGREE` out loud rather than picking one.

### The stage fraction: the tension this file claimed was not real

At the paper stage the Risk Officer scales every order by
`stage_fraction x regime_multiplier` = **0.1875**, so a strategy runs at roughly
a fifth of its intended size. This file previously said:

> ~~The scaling that protects an unproven strategy also prevents it from ever
> proving itself.~~ **False. Checked 2026-09-16.**

**Position scaling cannot affect the information ratio, because IR is a ratio of
the return series to its own dispersion.** Scale every position by `k` and the
excess-return series scales by `k`; `sharpe()` computes `mean/std`, and the `k`
cancels. Verified by running the same synthetic strategy through
`compare_to_benchmark` at three scales:

| `stage x regime` | excess | avg exposure | **IR** |
|---|---|---|---|
| 0.1875 (today) | +0.1266% | 18.8% | **+0.142060** |
| 0.75 | +0.5063% | 75.0% | **+0.142060** |
| 1.00 | +0.6751% | 100.0% | **+0.142060** |

Identical to six decimal places across a 5.3x size change. The t-statistic is a
function of IR and session count alone, so **raising the stage fraction shortens
the road to significance by exactly nothing.** The 1,430-session figure is
correct; what was wrong was the claim that sizing is a lever on it.

**The asymmetry this leaves is one-sided.** Raising the fraction multiplies the
realised loss by the same factor it multiplies the gain, while leaving the
evidence unchanged — and `max_strategy_drawdown` is 0.25, so a strategy that
hits its kill threshold costs the account ~4.7% at 0.1875 and ~25% at 1.0, for
the same knowledge either way.

**Recommendation: leave `STAGE_FRACTIONS["paper"]` at 0.25.** Not as a
compromise — there is no longer a trade-off to split. Revisit it as a *capital
efficiency* question (a book at 18.75% exposure cannot reach 35%/year even with
a good strategy) once a strategy has evidence from somewhere else. That is a
different ruling with different reasoning, and it belongs after the evidence,
not before it.

**What does shorten the clock**, since sizing does not — `N = 252 x (2/IR)^2` to
reach `t = 2`:

| IR | sessions | wall-clock, daily bars |
|---|---|---|
| 0.50 (promote floor) | 4,032 | ~16 years |
| 0.84 (the live reading) | 1,430 | ~5.7 years |
| 1.00 | 1,008 | ~4 years |
| 2.00 | 252 | ~1 year |

Three real levers, all already on the roadmap and none of them sizing:

1. **More observations per unit time.** The intraday cards (timeline 2.8–2.10).
   Six bars a day instead of one is ~6x the evidence per calendar week *if* the
   edge survives at that horizon. This is the only lever that shortens
   wall-clock without improving the strategy.
2. **More uncorrelated strategies.** `n` independent strategies at IR `r` give a
   portfolio IR of `r x sqrt(n)` — the same arithmetic as the 11-strategy figure
   above. The empty third account is one of these.
3. **Higher IR per strategy.** Breadth and signal quality — the research-funnel
   cards. `IR ≈ IC x sqrt(breadth)`, and 50 names is a narrow book.

**A daily-bar strategy at the promote floor cannot be validated on a human
timescale.** That is the finding worth carrying forward, and it is an argument
about what the firm should build, not about how large it should size.

## Measured 2026-08-19/20 (verify before reuse)

| | |
|---|---|
| Equity `PA3HEG2CLXLU` | **$9,990.85** (−0.09%) |
| Equity `PA3KQN57WVXY` | **$10,069.87** (+0.70%) |
| Equity `PA3YPMG9AD4Z` (**third strategy slot, empty** — see below) | **$10,066.19** (+0.66%) |
| Orders | 68 over 10 sessions, **100% filled** |
| Open positions | 12 and 27, for strategies that hold **ten** |
| `risk.decisions` | 155 rows, 84 approvals / 71 vetoes |

**`PA3YPMG9AD4Z` is not a control account and was never meant to be one.** The
three accounts exist so **three strategies can run, be calibrated and be refined
in parallel** without netting against each other (ADR-0017) — that is the whole
reason for the split. This one is the original smoke-test account, and its
+0.66% is Mike's manual AAPL/SPY test purchases closing at a profit: a human's
discretionary trades, which is the single thing a control must not contain. It
holds only cash now, which is exactly what makes it look like a baseline at a
glance.

**It is empty because there is no third strategy to put in it.** The firm has
capacity for three and runs two, and the idle slot is the research-throughput
constraint made concrete — not spare infrastructure. A control account, if one
is wanted, is a *fourth* account, not this one.

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

1. ~~**Compute the benchmark.**~~ **Done** — #203–#206 shipped
   `shrap-live-benchmark`; the reading is in the section above. If a clean
   control account is wanted it still needs a *fourth*, fresh one —
   `PA3YPMG9AD4Z` is the third strategy's slot, and is contaminated by
   hand-placed test trades besides.
1a. **Paste the Langfuse API keys into `infra/.env`.** #208 made every agent
   trace its LLM calls, but **only when keys exist**, and Langfuse issues them
   from its own UI so no card can create them. Until then each agent logs
   `llm.tracing_disabled` at startup and the sample keeps not accumulating —
   which is what KI-018 has described since July. Steps and the verification
   that separates "reachable" from "traced" are in
   `docs/runbooks/dell-bootstrap.md` §3.4a. This is five minutes and it is the
   only item here whose cost is *irrecoverable*: an untraced call cannot be
   traced retroactively, and Month 4's exit criteria need 50 of them per task.
2. **Clear the sub-$1 dust** in the Alpaca dashboard. Ops, not code.
3. **Kill and re-propose `true-autonomy-implementation`.** Its falsifiers are
   inverted (see below) and `amend-criteria` is append-only, so they cannot be
   fixed in place. The kill reason should record that the *falsifiers* were
   inverted, not that the *thesis* was wrong — the graveyard's denominator
   depends on that distinction.
4. **Research throughput — and there is now an empty account measuring it.**
   The constraint, per the 2026-07-28 arithmetic. Three accounts were opened so
   three strategies could be calibrated in parallel; two are running and the
   third has nothing to put in it.

   **It needs new sources, not a filter fix.** Checked 2026-08-23: the
   literature leg reads each item's `summary` and prompts with it as
   `Abstract:` at 4,000 chars, which for arXiv *is* the abstract — the right
   unit for a relevance judgement, and **not** the EDGAR failure where the
   stored item was an index entry. 105 of 111 q-fin papers rejected for "no
   testable effect" is most likely correct rather than broken.
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
