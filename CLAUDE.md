# Shrap-Firm — Project Context for Claude Code

This is **Shrap**, a self-developing multi-agent trading firm. The repo name is `shrap_firm` and the project is called Shrap throughout the codebase.

**Read `docs/00-vision.md` first.** Everything in this project flows from that document. Do not propose changes that conflict with the vision without flagging the conflict explicitly.

## Current phase
**Phase 1: implementation — Research unlock.** The paper-trading spine is deployed on the Dell and **closed** (market-hours smoke 9/9 on 2026-07-15; first fully autonomous trade, signal through fill, 2026-07-16). The Research funnel went live 2026-07-17; the Tech Watcher ingests EDGAR, arXiv, arXiv q-fin, USASpending, the Federal Register and DOE newsroom, and filters on **`qwen3.5:397b` via Ollama Cloud** (promoted 2026-07-31 by the first shadow eval — routing has been box-wide cloud since #169, and nothing runs on the local 9B).

**The funnel closed end to end on 2026-08-02** (#189, KI-009). It had admitted nothing for two months because the EDGAR leg stored the Atom *index entry* — a filed date, an accession number and a file size — rather than the filing. With document bodies it admitted **46 items** and synthesized its first pipeline candidate.

**The order path closed on 2026-08-04: six orders, six fills** on `PA3KQN57WVXY`, signal through fill with no human in the path. **Two strategies sit at `paper`** as forward tests, not promotions — neither cleared the promote gate. Both are daily; day trading still needs the intraday bar-*reading* card, since `BarSample.session_date` is a `date` all the way through the strategy layer. **Ten sessions to 2026-08-19: 68 orders, 100% filled**, best account +0.70% — **against no benchmark**. The firm has no control account — the three accounts are three *strategy slots* (ADR-0017), and `PA3YPMG9AD4Z` is the empty third, holding only the profit from hand-placed smoke-test buys, so the comparison must be computed from `market_data.daily_bars`. Both strategies scored IR 0.306 against a benchmark at 0.876, so a weak result is predicted rather than surprising. **The binding constraint is research throughput, not execution** — 35%/year needs ~11 uncorrelated strategies at the promote floor and the firm has zero above it.

The trading path was fixed **five times in ten days** (#192, #193, #195, #196, #198, #199), every defect silent and none raising an error. Three were introduced by the sessions that fixed the others. The recurring shape: **a component reconstructed a fact that was already recorded, and the reconstruction disagreed** — intent standing in for position, `market_value / price` standing in for a share count, an `INTEGER` column standing in for a fractional quantity. When a card changes a type or inserts a stage, ask what downstream already declared about what reaches it.

**The same shape, again, in the backups (#212–#214).** Three PRs to get one
backup that works: the crons were never installed, then the script could not
reach Docker (`truenas_admin` is not in the `docker` group — this is why every
interactive command here is `sudo docker`), then its dump would not have
*restored*, because `shrap` is TimescaleDB being dumped as plain Postgres. **Each
failure was only visible on the next real run; none were findable by reading the
script.** Assume a component is wrong about what it talks to until it has run
against the real thing.

**#215–#231 (2026-09-16/18)** shipped the Regime Router, the intraday panel path,
the Kelly posterior, exit rules and a forced filter re-promotion — and **two of
those PRs fixed regressions the others introduced**, each found only by running on
the Dell rather than by a passing test suite (#220 an import that broke the
evaluator's daily path, #221 a schema read that left the Runner one rebuild from
silently not trading). Tests pass in the gap between "the code is correct" and
"the container has the dependency, the database has the column."

**And a third shape of that same gap, found on deploy 2026-09-18 (KI-039):
`docker compose up -d --build` built a correct image and left the container
running the old one.** `docker inspect` showed the container on a different SHA
with the new code absent, while the build log said `Built`. Only
`--force-recreate` fixed it. **Verify a deploy by image ID, never by the build
log** — and confirm the behaviour in the database, not in the container.

**#232–#247 (2026-09-18)** closed the intraday path end to end (#234, #236,
#237, #241) and then found **three things that had been silently wrong for
weeks, none of which raised anything**:

- **node-exporter had never once been scraped** (#244).
  `max_over_time(up{job="node-exporter"}[45d])` was **0**. Two independent
  faults, each fatal alone: the exporter bound `127.0.0.1` under
  `network_mode: host`, unreachable from any container at any address; and
  Prometheus targeted `172.17.0.1`, the *default* bridge, when this project's
  gateway is `172.16.0.1`. The config's comment said "typically 172.17.0.1 on
  Linux … adjust if your bridge gateway differs" — a guess plus an instruction
  nobody ran. The firm had no host CPU, memory or disk metrics, ever.
- **The filing roster covered 4 of the 50 names** (#246). EDGAR ingest was
  healthy the whole time (~1,000 items/week); everything else was dropped at
  `roster.ticker_for(cik)`. Fifteen days with no filing recorded, which reads
  exactly like a quiet market. Now 42 of 50, and 113 → 169 filings.
- **`source` was not in the bar tables' primary key** (#245), while the
  module's own docstring claimed it was "part of the primary key intent … if a
  future card ever backfills SIP". That backfill would have overwritten IEX row
  by row and made every recorded IR irreproducible. **Not one of the eight
  bar-reading queries filtered on `source`** — all were correct only by
  accident, because the table held one feed.

**#248–#251 (2026-09-19) found two more silent faults, both of which had been
true for days and neither of which raised anything.**

- **A dead ingest source looks exactly like a quiet market (#251).** Both arXiv
  feeds returned HTTP 406 on **46 consecutive hourly passes** from 2026-09-17;
  the firm's only quant-literature source was off for two days. There *was* a
  six-hour freshness check on `research.raw_source_items` — it reads
  `max(fetched_at)` over the whole table, and EDGAR kept inserting ~1,000 items
  a week, so it stayed green throughout. **A table-level maximum is an AND
  across every producer writing to that table.** Now checked per source on
  `research.ingest_cursors`, which advances on every *successful* pass rather
  than only when new items appear. Two faults behind one symptom.
  ~~arXiv refuses `cond-mat` and `q-bio.NC` outright~~ — **retracted
  2026-09-23 (#272):** from another IP every one of those categories returns
  200; the 406s were aimed at the Dell, and the per-category fan-out built on
  that reading multiplied requests at a throttled host. One combined query per
  source now, and a 406 starts a 2–12h cooldown. Separately, the firm was **violating arXiv's
  published rate limit on every pass** (one request per three seconds; the pass
  fetched two `ArxivSource` instances back to back), and a throttled host gets
  406 on every cache *miss* while cache *hits* keep returning 200 — which is why
  it looked random. Fixed with a shared per-host throttle. **The throttle fix is
  unverified** — it could not be tested against a throttled host, and my own
  probing kept re-tripping it. **Do not diagnose an external API by hammering it
  from the production IP.**
- **The backup cron had never once fired (#249).** Zero `cronjob.run` entries in
  an unrotated `/var/log/cron.log` going back to 2026-07-17. Both backups that
  existed were stamped 14:24 and 16:54 — hand-run. **Check `cron.log`, not the
  destination directory:** files in `/mnt/backups` were never evidence that the
  schedule worked.

**The Ollama quota has two windows and this project budgeted against the wrong
one for months (#261, 2026-09-20).** Ollama Cloud enforces a **session** limit as
well as a weekly one, and the session limit is what stops runs. A 599-item
experiment was refused after 192 items with `weekly.usage` at **0.277** and
`session.usage` at **1.0**; #255's "10.6 weekly allowances" and the earlier 2.7x
estimate are both priced in the window that does not bind.
**`https://ollama.com/api/usage` returns both directly** to the firm's own key —
read it (`src/shrap/llm/ollama_usage.py`) before committing to any batch of
completions. **The quota is account-wide**, so a batch job starves the always-on
agents: twenty minutes after that run, the Tech Watcher's hourly literature pass
aborted with `scored: 0`. Batch CLIs hold a 10% reserve back for them.

**A pipe is part of the query (#260).** `select * from research.bar_experiment_runs
limit 5 | head -14` returned four rows and showed one, because `report_markdown`
is a multi-line `TEXT` column and psql's aligned output spans dozens of lines per
row. The truncation was read as a finding — "bars B and C have never been run" —
and the first correction then invented a root cause the data did not support.
`SELECT *` on a table with a wide text column is not a listing; name the columns,
or ask for `count(*)` when a count is the claim.

**And the merge of #249/#250 left `CLAUDE.md` asserting both that Qdrant held
zero collections and that it was live**, two lines apart, because both PRs
edited the same paragraph on the same day. Fixed in #251. Two cards touching one
doc paragraph is a conflict git resolves by keeping both.

**Two measured negatives, both worth not repeating.** Intraday cadence does not
multiply a daily edge — IR **0.003** at 15 minutes against **0.415** daily,
where `IR = IC x sqrt(breadth)` predicted ~8.8x (#242). And the IEX-vs-SIP feed
difference does not survive the folds: the sign flips on aggregate
(−0.1315 → +0.2275) but SIP wins only **3 of 6 folds** and dropping the single
dominant fold leaves a mean delta of **+0.0011** (#247,
`docs/research/feed-comparison-experiment.md`). The naive comparison of those
feeds *clears the promote floor* and is confounded by window — a live example
of the error KI-036 exists to refuse.

**Changelog entries are one file per card (#263).** Write yours as
`docs/status/changes/<pr>-<slug>.md`; **never append to
`docs/status/recent-changes.md`, which is frozen.** That file ended in a
`## Security notes` section, so every card landed at the same anchor and any two
open PRs conflicted — four resolution passes across #258, #260, #261 and #262 in
one evening, each a merge whose only content was keeping both sections. Separate
files cannot conflict. `make changelog` reads them in order.

**Write the docs with the card, not at session end (Mike's ruling,
2026-09-18).** This set went 16 PRs stale again during that session — the
fourth occurrence after #72–80, #92–101 and #129–175.

**The promote gate has never been failed on evidence (KI-036, 2026-09-17).** The
standard error of an annualised IR over 5.1 years on this firm's panel is
**±0.47**; the best IR ever recorded is **0.448**. That is **0.11 standard
errors** from the floor it failed, and separating 0.45 from 0.50 at two sigma
would take **~1,800 years** of daily history. A backtest cannot validate the
0.50 floor, ever — so the backtest now feeds the Kelly posterior rather than a
threshold. Do not go looking for a strategy variant that clears the gate; that
is a search for a favourable measurement error. Two mechanisms were already
measured and eliminated: **zero-cost momentum still scores 0.487** (so turnover
reduction cannot work) and the active-return streams are genuinely uncorrelated
but only one has positive IR on a common window (so breadth has nothing to
multiply).

**The firm could take neither a profit nor a loss until #230 (KI-037).** `grep`
for `stop_loss`/`take_profit`/`profit_target`/`trailing_stop` returned **zero
matches**. Every fill in the firm's history is stamped **09:30:03–09:30:07 ET**.
Exit rules are now **armed**: stop −10%, intraday take-profit +7%. The
since-entry take-profit is deliberately unarmed — it would sell a position the
126-day momentum signal is actively long.

**The accounts are 84% cash, and that alone costs 1.15 of IR.** Stage 0.25 x
regime 0.75 = 0.1875. Holding selection perfectly constant, **IR is
`-Sharpe(benchmark)` at every exposure below 1.0**, independent of the level.
The deadlock: exposure is low because edge is unproven, and a $70 return cannot
prove edge. Raising it is Mike's ruling. **The third account is idle because
nothing has reached `paper` in 46 days** — 15 strategies, 13 killed, 2 promoted.

**The Tech Watcher's filter is `kimi-k3` as of #231**, promoted on a shadow eval
because Ollama retires `qwen3.5:397b` on 2026-09-25. The eval also found that
**the incumbent no longer reproduces its own verdicts** — it called 0% of a
sample relevant, half of which it had previously scored relevant. Same shape as
KI-009: taxonomy, not model.

**The binding constraint, measured 2026-09-17 (KI-035):** the autonomous research
loop has produced **one** strategy ever (IR −0.006) against 14 Mike-seeded textbook
factors. The Hypothesis Generator logs `sweep_empty` hourly because
`research.literature_items` holds nine rows in total. Best IR the firm has ever
recorded is 0.415 against a 0.50 floor — the gate is not too tight, the strategies
lack edge. The seven `capability-gap` rows are a prioritised build list; market
capitalisation is the cheapest and is absent from every table. **Prefer feeding the
funnel over building another thing that measures it.**

**The archetype bar experiment is answered (#270): Bar B.** On 425 `sec-edgar`
filings under `kimi-k3`, `B-evidence-contribution` admits **14** against
`A-incumbent`'s 6 and `C-signal-tagging`'s 3 — and the disagreements nest
perfectly: B-only 8, A-only **0** (McNemar p = 0.008), B-only 11 against C
(p = 0.001). **C is worse than the unmodified production prompt.** B catches the
AI build-out supply chain A misses — Alliant Energy, EMCOR, MasTec, SPX, 3M, Air
Products, Ford. Evidence in `docs/research/archetype-bar-ruling.md`; the ruling
is Mike's. **But B's advantage is concentrated in filings.** Over the full 599
items it is A 9, B 17, C 5 — B vs A 9-vs-1, p = 0.022 — while on the 174
non-EDGAR items the bars are indistinguishable (3 each, one item each way) and
**the item B loses is DQ-006's named exemplar.** 29 non-EDGAR hard-leg items is
too few to detect an 8-in-425 effect, so this is underpowered rather than a
refutation, but **the EDGAR result is not evidence about the bar everywhere.**
The other limit: 14 admits is a small base, so direction yes, magnitude no. **#264 claimed the corpus
was the constraint — that claim is retracted (#267).** It rested on `sec-edgar`
admitting 0 of 425, which was an artifact of the experiment's own query: it never
selected `document_text`, so for 72% of the corpus the model was shown the Atom
index entry (a filed date, an accession number, a file size) rather than the
filing (#266). Read properly, EDGAR admits **6 of 425 — 1.41%**, Fisher exact
p = 0.031, and every admit is energy or compute build-out surfacing in corporate
disclosure. **Before concluding a source has nothing to say, confirm something
asked it.**

**Always-on services (verified 2026-09-18, 39 containers):** Health Monitor, Audit Logger, Pre-Trade Checker, Execution Agent ×3 (one per paper account), Paper Order Store, Reconciliation Agent ×3, Decision Maker, Strategy Fixture (disarmed), Strategy Librarian, Strategy Runner, Regime Classifier, Market Phase Scheduler, Tech Watcher, News Analyzer, Filing Processor, Universe Curator, Strategy Evaluator Trigger, Hypothesis Generator Trigger, Market Data Trigger, **Market Data Intraday Trigger** (#236), plus the substrate: Postgres/TimescaleDB, Redis, Qdrant, Ollama, Prometheus, Grafana, cAdvisor, node-exporter, postgres-exporter, redis-exporter and **docker-state-exporter** (#239). **On-demand (`--profile tools`):** Strategy Evaluator, Hypothesis Generator, Market Data backfill, Infrastructure Mapper. The **Risk Officer is a library**, not a service — it is enforced inside the Pre-Trade Checker. **`ib-gateway` was stopped and removed 2026-09-18**; its compose project lives at `infra/ibgateway/` (#243) and needs a gitignored `.env` to revive — ADR-0003 gates IBKR on live capital.

Work proceeds as one-card-per-PR (`phase1/<card-name>` branches off `main`; Mike reviews and merges; never stack PRs — see KI-001).

**Ground truth for what's next, in reading order:** **`docs/status/session-handoff.md`** (latest rulings + what to pick up), then **`docs/roadmap/implementation-timeline.md`** (the ordered plan). `docs/status/current-sprint.md` is longer-form history. `docs/roadmap/paper-spine-tree.md` is history — its last card shipped weeks ago.

> **Check the status docs before trusting them.** They have now fallen behind
> `main` four times — #72–#80, #92–#101, #129–#175 (forty-six PRs of finished
> work still described as pending), and #232–#247. Run **`make doc-drift`**
> first (last reconciled at **#247**). When it fails, trust `git log`,
> `docker compose ps` and the database over any document.
>
> **Mike's ruling, 2026-09-18: update the docs and the session memory with the
> card that caused the change, not in a cleanup pass at session end.** Batching
> the write-up means it competes with running out of context, which is exactly
> when it gets dropped — four times now.
>
> **`make doc-drift` compares PR numbers, not claims.** On 2026-08-04 it
> reported every status doc `ok` while the handoff said *"Orders: none yet"* on a
> day the firm had filled six. Green means recent, not true — the database is
> still the arbiter.

### Python project conventions
Standard PEP 621 / hatchling layout, single `src/shrap/` package. Tooling: **ruff** (lint + format, line length 100, py312 target), **pytest** + **pytest-asyncio** (auto mode), **mypy --strict** scoped to `src/shrap/`, **pre-commit** wiring all three plus YAML/whitespace hygiene. Runtime deps: `redis`, `httpx`, `structlog`, `pydantic`, `python-ulid`. Boring beats clever — no exotic tooling. See `pyproject.toml` and `Makefile` (`make all` = install + lint + typecheck + test).

## Foundational doc set (v0.1, complete)
All ten foundational docs are drafted: vision, architecture (all open questions resolved into ADRs 0001–0006; ADR-0003 decided 2026-07-06), hardware, agents catalog + seed specs, regimes, universe, roadmap, LLM routing, post-launch. Living status lives in `docs/status/`; decisions in `docs/decisions/`.

## Mike's review queue (still open)
- Universe lock-in: confirm or revise the proposed 50-name list in `docs/universe/README.md`
- Regime Classifier calibration ownership: thresholds/sizing bands in `src/shrap/intelligence/regime/profiles.py` are v0.1 single-day calibrations; the spec's open questions (debounce M, epsilon, band derivation) are implemented as defaults pending Mike's ruling
- Open agent-boundary questions in the remaining unimplemented agent specs

## How to work with me
- **Read existing docs before proposing changes.** Especially `docs/00-vision.md` and `docs/status/current-sprint.md`.
- **Match the style of existing docs.** Vision doc sets the tone: clear prose, honest probability framing, principled reasoning, no marketing language.
- **Surface uncertainty.** Ask before making architectural decisions. Don't guess on direction.
- **One card per PR.** Branch `phase1/<card-name>` off `main`; Mike merges. Decision-carrying PRs (ADRs, calibrations) must say "merging this = accepting X" in the body. Never stack PRs (KI-001).
- **Drift requires updating the spec, not the code.** When implementation reveals a spec is wrong, update the spec first.
- **Paper only. No real-money execution.** Broker credentials live only in `infra/.env` (gitignored) and only in broker-facing agent containers (ADR-0003). Never print, commit, or paste them.
- **Commit messages:** `docs: ...` for doc work, `chore: ...` for setup, `feat: ...`/`fix: ...`/`test: ...` for code.

## Key project constraints
- **The 4-month sprint (May–Aug 2026) is over.** Classes have started; this is post-sprint work at whatever pace they leave. Don't plan as though a deadline is still running — and don't let "the sprint ends soon" justify skipping a step.
- Mike has 1-2 hours/day for this project
- Agents do most of the building; Mike is architect/reviewer
- 50-stock universe (locked), regime-conditional strategies, structural analysis department
- Local-first long-term, cloud LLMs as scaffolding
- Hardware: Dell 5820 (TrueNAS, prod), Ryzen 7800X + 4070 Super (heavy inference), MacBook M4 24GB (dev/mobile)

## Tooling stack
**In production now:** Redis Streams (ADR-0001/0006 event bus), PostgreSQL + TimescaleDB, Prometheus + Grafana (ADR-0004), **Langfuse Cloud**, Qdrant, Ollama, Docker Compose on TrueNAS SCALE, direct Alpaca paper client (ADR-0003 — paper phase). Agents are plain asyncio service loops, not LangGraph, so far.

**Qdrant became true on 2026-09-19, having been listed here since July.** It was deployed 2026-07-02 and held **zero collections for two and a half months** while 164 MB of filing and paper text sat in Postgres, searchable only by exact string match. `docs/02-architecture.md` specified "full text to Qdrant" for Intelligence and Structural Analysis; nothing implemented it. The corpus index now does: `shrap-corpus-index` chunks, embeds with **local `nomic-embed-text`** (768-dim, on the Dell's own Ollama — not the cloud host, per vision principle 5) and writes ~82,000 points with full provenance. See `docs/runbooks/corpus-index.md`. **This is the firm's only vector search, and no agent consumes it yet** — the CLI queries it; wiring the Hypothesis Generator to retrieve prior work is a separate card. It does **not** address the binding constraint: the funnel is starved because the filter admits nothing (KI-009/KI-035), and making the rejected corpus searchable is a different capability from fixing the taxonomy.

**The local Langfuse was retired 2026-09-19 (#254), and it had never held a
trace.** All 24 agents carry `LANGFUSE_HOST=https://us.cloud.langfuse.com`;
Cloud held **4,424 traces** when the container was removed, and the local
`langfuse/langfuse:2` (2.95.11, OSS v2, **end of life**) held **zero** — while
costing an 821 MB image, a 68 MB volume, two healthchecks and a nightly backup
leg. Its compose lives at `infra/langfuse-local/` and can be revived, same
treatment as `infra/ibgateway/` (#243). **`DEFAULT_HOST` is now empty on
purpose**: keys set with no host disables tracing and says so, because
defaulting to a container that no longer exists would reproduce KI-018 exactly.
One knock-on, now resolved (#262): `src/shrap/llm/tracing.py` was hand-rolled
against the legacy `/api/public/ingestion` endpoint **because** OSS v2 could not
talk to Python SDK v3/v4 or OTel. Cloud supports all three — verified 2026-09-20,
OTel at `/api/public/otel` over HTTP/JSON or protobuf with Basic auth — **so the
constraint is gone and the module keeps the hand-rolled client on its own
merits**: it records inline, while the SDK and every OTel exporter batch in a
background processor, and spans queued when a container restarts are precisely
the unrecoverable sample the card exists to capture. Measured cost of recording
inline: 48-165 ms per call. Reopen it if a second OTel backend is ever wanted.

**The strategy engine can read market cap as of 2026-09-20 (#258).**
`AVAILABLE_SERIES` was `{close, volume}` for the firm's whole life and is now
`{close, volume, market cap}` — the first addition ever made to it. That moves
`volatility-rank-forecast`, the cheapest of KI-035's seven capability gaps, from
`missing-data` ("an ingestion pipeline, often a paid feed") to `missing-scorer`
("cost: an afternoon"). Market cap is `close x shares` computed **in the panel
from the panel's own closes**, never from `SELECT_MARKET_CAP_SQL`, which would
re-read `daily_bars` with its own `adjustment`/`source` and could silently
describe a different feed than the strategy sees. Coverage is 36 of 50 names;
the rest are ETFs and four multi-class issuers, carrying `nan` rather than zero.

**Planned / gated:** NautilusTrader (gate: live capital or execution needs beyond market/day orders, per ADR-0003), LangGraph (when an agent actually needs multi-node orchestration), OpenHands SDK (Development Department), VectorBT PRO (Strategy Evaluator), Mem0 (agent memory).

## Operating principles (from vision)
1. Honest accounting first, optimization later
2. Kill more aggressively than you promote
3. Boring beats clever
4. The repo is the truth
5. Cloud is scaffolding
6. Mike is the architect, not the implementer
7. Drift requires updating the spec, not the code
8. Audit everything
9. Optimize for compounding learning
10. Mike's time is the constraint
