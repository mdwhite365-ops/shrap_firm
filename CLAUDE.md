# Strap-Firm — Project Context for Claude Code

This is **Shrap**, a self-developing multi-agent trading firm. The repo name is `strap-firm` but the project is called Shrap throughout the codebase.

**Read `docs/00-vision.md` first.** Everything in this project flows from that document. Do not propose changes that conflict with the vision without flagging the conflict explicitly.

## Current phase
**Phase 0: Documentation.** No code is being written yet. We are drafting the foundational doc set before any implementation begins. Do not write code unless explicitly asked.

## What I'm working on now
Drafting the foundational doc set:
1. `docs/00-vision.md` — DONE
2. `docs/02-architecture.md` — NEXT
3. `docs/03-hardware.md`
4. `docs/agents/README.md` + `docs/agents/_template.md`
5. `docs/regimes/README.md` + `docs/regimes/_template.md`
6. `docs/universe/README.md` + `docs/universe/_template.md`
7. `docs/01-roadmap.md`
8. `docs/infrastructure/llm-routing.md`
9. Seed agent specs (5-8 of them)
10. `docs/post-launch.md`

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
