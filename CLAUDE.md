# Shrap-Firm — Project Context for Claude Code

This is **Shrap**, a self-developing multi-agent trading firm. The repo name is `shrap_firm` and the project is called Shrap throughout the codebase.

**Read `docs/00-vision.md` first.** Everything in this project flows from that document. Do not propose changes that conflict with the vision without flagging the conflict explicitly.

## Current phase
**Phase 0: Documentation — foundational set complete.** No code is being written yet. The foundational doc set is drafted and awaiting Mike's review pass. Do not write code unless explicitly asked.

## Foundational doc set (all v0.1 drafts, awaiting Mike's review)
1. `docs/00-vision.md` — DONE
2. `docs/02-architecture.md` — DONE (three open questions resolved into ADRs 0004–0006; one remaining: ADR-0003 Nautilus-Redis bridge)
3. `docs/03-hardware.md` — DONE
4. `docs/agents/README.md` + `docs/agents/_template.md` — DONE
5. `docs/regimes/README.md` + `_template.md` + seed profiles (wartime, stagflation, crisis-recovery, late-cycle-melt-up) — DONE
6. `docs/universe/README.md` + `_template.md` + seed profiles (SPY, QQQ, TSLA, NVDA, AAPL, LMT) — DONE; **50-name list is a draft proposal requiring Mike's lock-in**
7. `docs/01-roadmap.md` — DONE
8. `docs/infrastructure/llm-routing.md` — DONE
9. Seed agent specs — DONE (regime-classifier, hypothesis-generator, strategy-evaluator, decision-maker, risk-officer, implementation-agent, health-monitor)
10. `docs/post-launch.md` — DONE

## Mike's review queue (before Phase 1 begins)
- Universe lock-in: confirm or revise the proposed 50-name list in `docs/universe/README.md`
- Resolve open agent-boundary questions surfaced during spec drafting (see "Open Questions" sections in each agent spec)
- Resolve ADR-0003 (Nautilus-Redis bridge coverage) during Trading Floor spec deep-dive
- Approve `docs/01-roadmap.md` month-by-month milestones

## How to work with me
- **Read existing docs before proposing changes.** Especially `docs/00-vision.md`.
- **Match the style of existing docs.** Vision doc sets the tone: clear prose, honest probability framing, principled reasoning, no marketing language.
- **Surface uncertainty.** Ask before making architectural decisions. Don't guess on direction.
- **Don't write code yet.** We're in docs phase.
- **Drift requires updating the spec, not the code.** When implementation reveals a spec is wrong, update the spec first.
- **Commit messages:** `docs: ...` for doc work, `chore: ...` for setup, `feat: ...` later for code.

## Key project constraints
- 4-month sprint (May–Aug 2026), classes start after
- Mike has 1-2 hours/day for this project
- Agents do most of the building; Mike is architect/reviewer
- 50-stock universe (locked), regime-conditional strategies, structural analysis department
- Local-first long-term, cloud LLMs as scaffolding
- Hardware: Dell 5820 (TrueNAS, prod), Ryzen 7800X + 4070 Super (heavy inference), MacBook M4 24GB (dev/mobile)

## Tooling stack (planned)
- LangGraph for agent orchestration
- OpenHands SDK for development department
- NautilusTrader for execution
- VectorBT PRO for backtesting
- Mem0 for agent memory
- Qdrant for semantic search
- Langfuse for observability
- Ollama for local LLMs

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
