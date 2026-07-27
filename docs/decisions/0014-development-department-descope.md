# ADR-0014: Development Department Descope, and the Three-Tier Compute Boundary

**Status:** Proposed
**Date:** 2026-07-27
**Deciders:** Mike White

## Context

The Development Department has four agents on the roster — Spec Writer,
Implementation Agent (OpenHands SDK), Code Reviewer, Deployment Agent — all
scheduled for Month 1. On 2026-07-27, three months into a four-month sprint,
**none exist**. There is no `docs/agents/development/` content beyond a single
`implementation-agent.md` spec, and no container.

The roadmap's stated goal for the department was:

> "Outer loop comes online first because everything else gets built through it.
> By end of month 1, Mike should be reading PRs, not writing code."

**That outcome was achieved.** Mike reads PRs and does not write code. Every
card from #7 through #91 arrived as a reviewed pull request. The 2026-07-22
cost policy formalized it further: cards are built by delegated subagents while
an orchestrator reviews, gates, and opens PRs.

It was achieved by a mechanism the spec does not describe. The department was
specified as OpenHands SDK containers running on the Dell. What actually
happened is Claude Code sessions on Mike's MacBook. The goal is met; the
architecture on paper was never built. That divergence has sat unrecorded,
which is operating principle 7 running in reverse — spec and reality drifted
and nothing surfaced it.

### The constraint that actually governs this

Mike's ruling, 2026-07-27:

> "For now cloud based agents is perfect, the Ollama ones are free and you use
> my Pro subscription. The Ollamas work on the Dell 'cause I can't put you on
> the Dell."

That sentence names a hard architectural boundary the repo has never written
down. The firm runs on **three distinct compute tiers**, and they are not
interchangeable:

| Tier | Where | Cost | Who initiates | ADR-0009 aliases |
|---|---|---|---|---|
| **1. Local inference** | Ollama on the Dell | Free | The agent's own loop | `local-classification`, `local-heavy` |
| **2. Cloud inference** | API calls from Dell agents | Metered, needs billing | The agent's own loop | `cloud-judgment-heavy`, `cloud-default`, `cloud-cheap` |
| **3. Development** | Claude Code on Mike's MacBook | Mike's Pro subscription | **A human opening a session** | *none — not addressable* |

Tiers 1 and 2 are what ADR-0009's registry describes: models a deployed agent
can call at runtime. **Tier 3 is not in that vocabulary and cannot be added to
it.** Claude Code is an interactive development environment on Mike's machine,
not a model endpoint an agent can invoke. No amount of registry work makes it
callable from a container on the Dell.

This is why the Implementation Agent was never built and why building it now
would not help: the implementer is not a deployable agent. It is a session.

## Decision

### 1. Descope Spec Writer, Implementation Agent, and Code Reviewer

These three are removed from the active roster for Phase 1. Claude Code, driven
by Mike, **is** the Development Department. This is a formal recognition of the
operating reality, not a new plan.

`docs/agents/development/implementation-agent.md` is retained as a historical
spec and marked descoped. It is not deleted — if Phase 2 pursues local
autonomy, it is the starting point.

### 2. Retain the Deployment Agent, reclassified

The Deployment Agent is **not** descoped, on a distinction that matters: it
needs no model at all. Deployment is deterministic Docker orchestration —
`no-llm` in ADR-0009's vocabulary. The "Claude cannot run on the Dell"
constraint therefore does not apply to it. It is ordinary automation that
happens to have been filed under a department whose other members were
model-dependent.

It is also the member with the clearest evidence of need. Two production
incidents trace directly to manual deploys:

- `docker compose up -d --build <svc>` reported success while leaving the
  container on the old image; `--force-recreate` is now required and was
  learned the hard way (2026-07-19).
- A one-shot seed loader was run before the card that fixed what it writes,
  baking bad data in permanently — the loader is idempotent-by-skip, so the
  later merge could not reach it (PRs #83/#84).

It moves to the **Operations Department** as a `no-llm` service and is
rescheduled from Month 2 to the post-loop-closure queue.

### 3. Record the three-tier boundary as a firm-level constraint

The table above is adopted as architecture, not as an incidental fact about
Mike's hardware. Its binding consequence:

> **No autonomous capability may depend on Tier 3.** Anything that requires
> Claude-grade reasoning is human-initiated by construction, and therefore
> bounded by Mike's attention rather than by the clock.

Any future spec proposing an always-on agent must state which of Tier 1 or
Tier 2 serves it. "It will use Claude" is not an available answer for a
deployed agent.

### 4. Amend the vision honestly

`docs/00-vision.md:13` currently opens: *"Shrap is a self-developing,
self-improving, self-trading firm."* Under this ADR the firm is not
self-developing — it develops when Mike opens a session, using a subscription
tied to his person and machine.

The vision is amended to say so plainly, and to name local development autonomy
as a Phase 2 goal rather than a current property. Principle 5 ("cloud is
scaffolding — use it freely during the build, plan to retire it") already
anticipates exactly this arrangement; what was missing was the admission that
development is currently *on* the scaffolding.

## Alternatives Considered

### (a) Build the four agents as specified

Rejected. It duplicates a working mechanism at the direct expense of the
firm's scarcest resource (principle 10, Mike's time), during the final month
of a four-month sprint, to reach an outcome already achieved. OpenHands SDK
remains in CLAUDE.md's gated list and can be revisited in Phase 2.

### (b) Descope all four, including the Deployment Agent

Rejected. It conflates two unlike things. Three of the four are descoped
because a human session replaced them. The Deployment Agent was never replaced
by anything — deploys are still manual, and they have already caused two
incidents. Descoping it would mean deciding that manual deployment is fine,
which is a different decision and not one the evidence supports.

### (c) Leave the roster as-is and treat it as backlog

Rejected. This is the status quo, and the status quo is what produced a roster
reading "Planned" for 35 agents including several that shipped months ago. A
gap that is neither built nor honestly marked is worse than either — it
silently overstates the plan and understates what was actually achieved.

### (d) Add Claude Code to the ADR-0009 LLM registry as a tier

Rejected as technically incoherent. The registry maps tier aliases to model
endpoints an agent calls at runtime. Claude Code is an interactive session on a
different machine with no callable interface. Registering it would create a
tier no agent could resolve and would invite specs to depend on it.

## Consequences

### The autonomous ceiling is now explicit

This is the significant consequence, and it constrains work already decided.

The firm's unattended capability is bounded by Tiers 1 and 2. ADR-0013's
cross-lens synthesis surface — the component that most directly serves the
firm's stated objective of finding outliers that "take a team to put the pieces
together" — is also the component that most wants strong reasoning. Under this
ADR it must either run on Tier 1/2 with the quality ceiling that implies, or be
human-initiated and bounded by Mike's attention. It cannot be both good and
free of Mike.

That tension is real and is recorded rather than resolved here.

One piece of evidence bears on it and cuts favorably: the 2026-07-27 KI-009
diagnostic re-ran the Tech Watcher filter on a cloud model against the same 16
items and produced **zero verdict flips**. For classification-shaped work, the
local tier appears to be at parity, and the funnel's problem was taxonomy
rather than model quality. That evidence does **not** transfer to synthesis —
joining weak signals across sources is a different task shape than scoring one
item against a rubric — but it argues against assuming the local ceiling is
low before measuring it.

### Immediate

- Roster: three agents marked Descoped; Deployment Agent moved to Operations.
- `docs/00-vision.md:13` amended per §4.
- `implementation-agent.md` marked descoped, retained.
- No change to how work is done. This ADR describes current practice; nothing
  about the PR flow changes on merge.

### Not changed

- Paper-only scope.
- ADR-0009's six tier aliases, which remain correct for what they describe.
- The 2026-07-22 cost policy governing subagent delegation.
- Phase 2 optionality on OpenHands SDK and local development autonomy.

## Notes

The honest framing of this ADR is that the firm traded self-development for
speed and got a good deal. Three months of cards shipped through a mechanism
that cost a subscription instead of an engineering effort.

The debt is real, though, and worth naming precisely rather than dramatizing:
the firm cannot currently improve itself while Mike sleeps, and the research
loop — which *was* built to run while Mike sleeps — has no equivalent
dependency. That asymmetry is the thing to watch. A firm whose research runs
unattended but whose development does not will accumulate findings faster than
it can act on them, which is approximately the condition described in KI-008,
KI-011, and KI-012.
