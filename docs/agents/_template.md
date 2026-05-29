# [Agent Name]

**Department:** [Department Name]
**LLM tier:** [Cloud (Claude Sonnet 4.6) | Local (Qwen 9B) | No LLM — deterministic]
**Status:** Draft
**Date:** [YYYY-MM-DD]
**Author:** Mike White

## Purpose

[1–3 paragraphs. What problem does this agent solve? Why does it exist? What would break
or be missing without it? Be specific about the failure mode the agent prevents, not just
the function it performs.]

## Trigger

[What initiates a run? List all that apply.]

- **Schedule:** [cron-like cadence, e.g., daily at 07:00 ET, every 5 minutes during market hours]
- **Event:** [Redis Stream subscription, e.g., `regime.updated`]
- **On-demand:** [invoked by another agent or by Mike]

## Cross-references

**Depends on:** [agents whose correctness this agent requires — if they fail, this agent produces wrong output]
**Depended on by:** [agents that consume this agent's outputs]
**Related ADRs:** [ADR numbers from `docs/decisions/`]
**Related architecture sections:** [section numbers in `docs/02-architecture.md`]

## Inputs

| Source | Type | Description |
|---|---|---|
| Redis: `<stream>` | Event | [description] |
| PostgreSQL: `<table>` | Query | [description] |
| Qdrant: `<collection>` | Semantic search | [description] |
| External API: [name] | HTTP | [description] |
| Repo: `<path>` | File read | [description] |

[Remove rows that don't apply. Add rows as needed.]

## Processing

[Numbered steps from trigger to output. Specific enough that an implementer can build
without further design decisions. If there is a meaningful conditional branch, describe
both paths.]

1. [Step 1]
2. [Step 2]
3. [Step N]

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis: `<stream>` | Event | [description] |
| PostgreSQL: `<table>` | Write | [description] |
| Qdrant: `<collection>` | Upsert | [description] |
| Repo: `<path>` | File write | [description] |

[Remove rows that don't apply. Add rows as needed.]

## LangGraph structure

**Nodes:**
- `<node-name>` — [what this node does]

**Key edges:**
- `<node-a>` → `<node-b>` [condition, if conditional]

[If the agent is purely deterministic and does not use LangGraph, say so and omit the
node/edge detail.]

## State

[What does this agent persist across runs, and where?]

| What | Store | Notes |
|---|---|---|
| [description] | PostgreSQL / Redis / Qdrant / Repo | [TTL, append-only, keyed by X, etc.] |

[If the agent is stateless — each run is fully independent — say so explicitly.]

## Failure behavior

Each spec must answer these three questions explicitly:

1. **Containment:** If this agent crashes or returns wrong output, what is the blast
   radius? Contained to this agent's output only, or does it propagate to other
   departments?

2. **Replay safety:** Is it safe to restart this agent and reprocess events from the last
   checkpoint? If not, what manual recovery is needed before restart?

3. **Degraded operation:** Can the system run usefully without this agent for some period?
   If yes, for how long and under what constraints? If no, what halts?

[Prose answers to all three, following the numbered structure above.]

## Sprint scope

- Month [N]: [capability]
- Month [N]: [capability]

## Deferred

- [Item explicitly out of scope for the sprint]

## Open questions

[Design questions that remain unresolved when this spec is written. Each should name what
it blocks and who resolves it. Remove this section entirely if there are none.]

- **[Question]:** [description]. Blocks: [what]. Owner: [Mike | agent X | pending].
