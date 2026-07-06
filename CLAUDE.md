# Shrap-Firm — Project Context for Claude Code

This is **Shrap**, a self-developing multi-agent trading firm. The repo name is `shrap_firm` and the project is called Shrap throughout the codebase.

**Read `docs/00-vision.md` first.** Everything in this project flows from that document. Do not propose changes that conflict with the vision without flagging the conflict explicitly.

## Current phase
**Phase 1: implementation.** The paper-trading spine is deployed on the Dell and verified end to end via `shrap-spine-smoke` (Card 15 passed 2026-07-06). Deployed services: Health Monitor, Audit Logger, Pre-Trade Checker, Execution Agent, Paper Order Store, Reconciliation Agent, and the Regime Classifier (first Research-unlock agent, statistical layer only). Work proceeds as one-card-per-PR (`phase1/<card-name>` branches off `main`; Mike reviews and merges; never stack PRs — see KI-001). Ground truth for what's next: `docs/status/current-sprint.md` and `docs/roadmap/paper-spine-tree.md`.

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
