# Shrap-Firm — Project Context for Claude Code

This is **Shrap**, a self-developing multi-agent trading firm. The repo name is `shrap_firm` and the project is called Shrap throughout the codebase.

**Read `docs/00-vision.md` first.** Everything in this project flows from that document. Do not propose changes that conflict with the vision without flagging the conflict explicitly.

## Current phase
**Phase 1: implementation — Research unlock.** The paper-trading spine is deployed on the Dell and **closed** (market-hours smoke 9/9 on 2026-07-15; first fully autonomous trade, signal through fill, 2026-07-16). The Research funnel went live 2026-07-17; the Tech Watcher ingests EDGAR, arXiv, arXiv q-fin, USASpending, the Federal Register and DOE newsroom, and filters on **`qwen3.5:397b` via Ollama Cloud** (promoted 2026-07-31 by the first shadow eval — routing has been box-wide cloud since #169, and nothing runs on the local 9B).

**The funnel closed end to end on 2026-08-02** (#189, KI-009). It had admitted nothing for two months because the EDGAR leg stored the Atom *index entry* — a filed date, an accession number and a file size — rather than the filing. With document bodies it admitted **46 items** and synthesized its first pipeline candidate.

**The order path closed on 2026-08-04: six orders, six fills** on `PA3KQN57WVXY`, signal through fill with no human in the path. **Two strategies sit at `paper`** as forward tests, not promotions — neither cleared the promote gate. Both are daily; day trading still needs the intraday bar-*reading* card, since `BarSample.session_date` is a `date` all the way through the strategy layer. **Ten sessions to 2026-08-19: 68 orders, 100% filled**, best account +0.70% — **against no benchmark**. The firm has no control account — the three accounts are three *strategy slots* (ADR-0017), and `PA3YPMG9AD4Z` is the empty third, holding only the profit from hand-placed smoke-test buys, so the comparison must be computed from `market_data.daily_bars`. Both strategies scored IR 0.306 against a benchmark at 0.876, so a weak result is predicted rather than surprising. **The binding constraint is research throughput, not execution** — 35%/year needs ~11 uncorrelated strategies at the promote floor and the firm has zero above it.

The trading path was fixed **five times in ten days** (#192, #193, #195, #196, #198, #199), every defect silent and none raising an error. Three were introduced by the sessions that fixed the others. The recurring shape: **a component reconstructed a fact that was already recorded, and the reconstruction disagreed** — intent standing in for position, `market_value / price` standing in for a share count, an `INTEGER` column standing in for a fractional quantity. When a card changes a type or inserts a stage, ask what downstream already declared about what reaches it.

**Always-on services (34 containers, verified 2026-07-31):** Health Monitor, Audit Logger, Pre-Trade Checker, Execution Agent ×3 (one per paper account), Paper Order Store, Reconciliation Agent ×3, Decision Maker, Strategy Fixture (disarmed), Strategy Librarian, Strategy Runner, Regime Classifier, Market Phase Scheduler, Tech Watcher, News Analyzer, Filing Processor, Universe Curator, Strategy Evaluator Trigger, Hypothesis Generator Trigger. **On-demand (`--profile tools`):** Strategy Evaluator, Hypothesis Generator, Market Data backfill, Infrastructure Mapper. The **Risk Officer is a library**, not a service — it is enforced inside the Pre-Trade Checker.

Work proceeds as one-card-per-PR (`phase1/<card-name>` branches off `main`; Mike reviews and merges; never stack PRs — see KI-001).

**Ground truth for what's next, in reading order:** **`docs/status/session-handoff.md`** (latest rulings + what to pick up), then **`docs/roadmap/implementation-timeline.md`** (the ordered plan). `docs/status/current-sprint.md` is longer-form history. `docs/roadmap/paper-spine-tree.md` is history — its last card shipped weeks ago.

> **Check the status docs before trusting them.** They have now fallen behind
> `main` three times — #72–#80, #92–#101, and #129–#175, the last being
> forty-six PRs of finished work still described as pending. Run
> **`make doc-drift`** first (last reconciled at **#200**). When it fails, trust
> `git log`, `docker compose ps` and the database over any document.
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
- 4-month sprint (May–Aug 2026), classes start after
- Mike has 1-2 hours/day for this project
- Agents do most of the building; Mike is architect/reviewer
- 50-stock universe (locked), regime-conditional strategies, structural analysis department
- Local-first long-term, cloud LLMs as scaffolding
- Hardware: Dell 5820 (TrueNAS, prod), Ryzen 7800X + 4070 Super (heavy inference), MacBook M4 24GB (dev/mobile)

## Tooling stack
**In production now:** Redis Streams (ADR-0001/0006 event bus), PostgreSQL + TimescaleDB, Prometheus + Grafana (ADR-0004), Langfuse, Qdrant, Ollama, Docker Compose on TrueNAS SCALE, direct Alpaca paper client (ADR-0003 — paper phase). Agents are plain asyncio service loops, not LangGraph, so far.

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
