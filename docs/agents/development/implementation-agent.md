# Implementation Agent

**Department:** Development
**LLM tier:** `cloud-default` as the driver model behind OpenHands SDK for routine
non-protected-path work. **Escalates to `cloud-judgment-heavy` for any change touching a
protected path** (Risk Officer core, order-router, execution adapters, kill-switches,
secrets handling, anything in `docs/development/protected-paths.md`) — protected-path
diffs get a judgment-heavy code-review pass before the PR is opened. Migration target
post-sprint: `local-heavy` for routine tasks once shadow evaluation passes; protected-path
work stays on `cloud-judgment-heavy` indefinitely. See `docs/infrastructure/llm-routing.md`
and `docs/infrastructure/llm-registry.md`.
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** Draft
**Date:** 2026-05-29
**Author:** Mike White
**Version:** 0.1 (draft)

## Purpose

The Implementation Agent is how Shrap's 1–2 hours/day of Mike's time compounds into a
serious operation. It reads approved specifications, writes code that implements them,
adds tests, runs the test suite, and opens pull requests for review. It does not merge
its own work. Mike or the Code Reviewer Agent must approve before anything reaches `main`.

The agent exists because manually-written code at Mike's pace would take years. The agent
is deliberately constrained — it is not given freedom to redesign, refactor across
boundaries, or modify specifications. It is given a spec, a task, and tests; it produces
an implementation and a PR.

**Hard constraint — trust progression.** Per `docs/00-vision.md` §The role of AI agents,
the Implementation Agent **cannot touch trading-policy or risk-policy code paths without
Mike's explicit per-PR approval**. Specifically forbidden, even with Code Reviewer
approval alone:

- The real-money invariant in the Risk Officer (`mode == paper` enforcement)
- Risk limit constants in `docs/risk/policy.md` and the code that enforces them
- The Strategy Evaluator's promotion-gate thresholds (trade-count gate, PBO, DSR
  thresholds, the promotion-stage state machine)
- The kill-switch mechanism
- The Decision Maker's confluence-policy file
- Anything inside `trading-floor/` or `risk-compliance/` package roots in production

PRs touching these paths are auto-labeled `MIKE_APPROVAL_REQUIRED` and the merge gate
blocks until Mike's signed approval is present. The CI rule that enforces this lives in
the repo and is itself in the protected set. The Implementation Agent reads this list at
the start of every task and refuses tasks that violate it.

What this agent cannot do:
- It cannot decide what to build. Specs come from Mike or the Spec Writer.
- It cannot evaluate strategy code for correctness against the trading thesis — that is
  the Strategy Evaluator's job.
- It cannot reliably reason about subtle concurrency or live-market timing issues; PRs
  touching those areas require Code Reviewer + Mike sign-off even if not in the
  protected set.
- It cannot self-assess when it is overreaching its competence. The Code Reviewer is the
  external check.

## Trigger

- **Schedule:** Polls the implementation queue every 5 minutes during configured working
  hours (default 08:00–22:00 ET; off-hours work is permitted if Mike enables it
  explicitly to avoid surprise PRs).
- **Event:** `development.task.ready` event posted by the Spec Writer or by Mike.
- **On-demand:** Mike-initiated `development.task.assign` with a task_id.

## Cross-references

**Depends on:** Spec Writer (input specs), test infrastructure, OpenHands SDK runtime,
Code Reviewer Agent (downstream gate), Deployment Agent (post-merge).
**Depended on by:** Every agent in the system eventually — this is how they get built.
**Related ADRs:** Forthcoming ADR on OpenHands integration; ADR-0006 (envelope).
**Related architecture sections:** `docs/02-architecture.md` §Development Department.

## Inputs

| Source | Type | Description |
|---|---|---|
| Redis: `development.task.queue` | Job pull | Tasks with priority, spec ref, acceptance criteria |
| Repo: `docs/agents/**/*.md` | File read | Spec(s) the task references |
| Repo: `docs/02-architecture.md`, `docs/00-vision.md` | File read | Context |
| Repo: `docs/development/protected-paths.md` | File read | Authoritative list of protected paths; refuses task if it conflicts |
| Repo: code | Git read | Working tree |
| Qdrant: `code_corpus` | Semantic search | Prior implementations for style/pattern consistency |

## Processing

1. **Pull task.** Pop highest-priority task whose dependencies are satisfied.
2. **Read context.** Load the referenced spec, the architecture sections it cites, and
   the dependency specs. Hard cap: if context exceeds the configured budget, the agent
   declines the task and asks the Spec Writer to break it down.
3. **Protected-path check.** Parse the task's intended file paths against
   `docs/development/protected-paths.md`. If any match without an attached Mike-approval
   token, refuse the task with reason `PROTECTED_PATH_REQUIRES_MIKE_APPROVAL` and emit
   an event that surfaces in Mike's daily briefing.
4. **Branch.** Create a feature branch `impl/<task_id>-<slug>`. Never works on `main` or
   on another agent's branch.
5. **Implement.** Run the OpenHands SDK loop: write code, run tests, iterate. The agent
   is configured with project conventions (formatter, linter, type checker). Each
   iteration's diff and test output is recorded.
6. **Add tests.** Tests must cover the spec's documented behavior including failure
   modes. PRs without tests are not opened.
7. **Run the full suite.** Local CI must pass before opening a PR. If the suite cannot
   pass within a documented retry budget, the agent stops, posts a status, and waits for
   human input rather than disabling tests.
8. **Open PR.** PR body includes: task_id, spec link, summary of changes, list of files
   touched (highlighting any near-protected paths), test results, known limitations, and
   a "why this might be wrong" section. The last item is required — it makes Mike's
   review faster.
9. **Wait for review.** The agent does not merge. It responds to review comments by
   pushing additional commits. It does not request re-review more than 2 times before
   asking Mike for intervention.
10. **Close out.** On merge (by Mike or Code Reviewer), the agent marks the task
    complete and emits `development.task.completed`. On close-without-merge, emits
    `development.task.abandoned` with reason captured.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Git remote: PR branch | Push | Implementation + tests |
| Git remote: PR | Open | Pull request with structured body |
| Redis stream: `development.task.status` | Event | Per-step status updates |
| Redis stream: `development.task.completed` / `abandoned` | Event | Terminal state |
| PostgreSQL: `development.task_runs` | Append-only insert | Full task record: spec ref, iterations, tools used, LLM costs, outcome |
| Qdrant: `code_corpus` | Upsert | Embeddings of the resulting code for future style retrieval |

Every event carries the ADR-0006 envelope. The forensic record for any change in the
firm — who proposed it, who wrote it, who reviewed it, which model wrote which lines — is
reconstructible from this table plus git history.

## LangGraph structure

The agent uses OpenHands SDK as the inner loop, which has its own control flow. The
outer LangGraph layer manages: task selection → context loading → protected-path check →
OpenHands run → PR open → review-response loop → terminal state.

**Nodes:**
- `select-task`, `load-context`, `protected-path-gate`, `openhands-run`, `open-pr`,
  `await-review`, `respond-to-review`, `terminate`

**Key edges:**
- `protected-path-gate` → `terminate` (refusal path) or `openhands-run` (proceed)
- `await-review` → `respond-to-review` → `await-review` (loop, bounded retry count)
- `await-review` → `terminate` (merge or close)

## State

| What | Store | Notes |
|---|---|---|
| Active task assignment | Redis hash | Single-task-at-a-time per agent instance |
| Task history | PostgreSQL `development.task_runs` | Append-only |
| LLM call ledger per task | PostgreSQL | For cost monitor |

## Failure behavior

1. **Containment.** The agent operates entirely off `main`. Bad code lives in a PR
   branch and is caught by tests + Code Reviewer + Mike. Worst case: a bad PR is merged
   by mistake; the Deployment Agent's smoke tests should catch it before promotion to
   production. The trading floor running paper-only during the sprint provides further
   containment.
2. **Replay safety.** Task work itself is not idempotent (the agent will produce
   different code on different runs). However, the *record* of each task run is
   complete, and a failed task can be reassigned cleanly because the branch is
   discardable.
3. **Degraded operation.** The firm runs fine for weeks without the Implementation Agent
   — no new features are built, but nothing in production breaks. If the OpenHands SDK
   is unavailable, Mike can implement directly. This is degraded but viable.

## Sprint scope

- Month 1: Basic task → PR flow on simple, scoped specs. Protected-path enforcement
  working from day one.
- Month 2: Multi-iteration review-response loop. Cost ledger.
- Month 3: Larger refactors with explicit Mike sign-off per refactor.
- Month 4: Shadow-evaluate the `local-heavy` driver against `cloud-default` on the routine task subset.

## Deferred

- Cross-repo work (Shrap is one repo).
- Autonomous merging — explicitly never in scope.
- Self-modification of the protected-paths list — explicitly never in scope.
- Refactors that span more than 5 files without Mike-approved plan.

## Open questions

- **Off-hours operation:** Should the agent open PRs while Mike is asleep? Default no.
  Blocks: throughput. Owner: Mike.
- **OpenHands sandboxing:** Local Docker vs the Ryzen tier? Blocks: throughput and
  cost. Owner: Mike + Infrastructure Planner.
- **Cost cap per task:** Default $5 of cloud LLM. Blocks: budget control. Owner: Mike.
- **What happens when Code Reviewer Agent and Implementation Agent disagree?** Currently
  escalates to Mike after 2 rounds. Blocks: workflow for novel disagreements. Owner: Mike.
