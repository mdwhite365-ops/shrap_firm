# ADR-0009: Research Funnel Boundary Decisions and LLM Registry Pattern

**Status:** Accepted
**Date:** 2026-05-30
**Deciders:** Mike White

## Context

ADR-0007 (Research Funnel) and ADR-0008 (Hypothesis Archetypes and Universe
Construction) closed the large structural questions but left two classes of
open items that were blocking downstream agent specs:

1. **Eight boundary questions** about how the research funnel interacts with
   Risk, Strategy lifecycle, Implementation, and the broader universe — small
   in isolation, but each one would otherwise be re-litigated inside every
   agent spec that touches it.
2. **The LLM-versioning staleness problem.** Agent specs were drifting toward
   hardcoded model names (`claude-3-5-sonnet-...`, `qwen2.5:9b-instruct`).
   Within a quarter, every spec would either be silently stale or would
   require a coordinated global find-replace with no shadow evaluation
   behind it. Neither outcome is acceptable.

This ADR closes both. The eight boundary decisions are spelled out so they
can be cited rather than re-debated. The LLM Registry pattern is introduced
as the general mechanism for handling versioned external dependencies in the
repo.

## Decision — Part A: Eight Boundary Decisions

Each decision is stated with its locked text and the condition under which
it should be revisited.

**1. Regime Classifier debounce.** `M = 5` trading days minimum dwell time
in a regime before a transition is emitted, with `ε = 0.15` (15% margin)
on the classifier's confidence delta required to flip. Tuned to suppress
chop-driven false transitions without blinding the system during real
regime shifts.
*Revisit if:* the calibration ledger shows the classifier missing real
transitions by >10 trading days in retrospect on more than two events, OR
emitting transitions that reverse within `M` more than once per quarter.

**2. Risk Officer regime-tightening semantics.** When the Risk Officer
tightens regime sizing to 0, this BLOCKS new entries but does NOT force
exits. Strategy exits remain signal-driven. The Risk Officer retains its
veto power for HARD breaches only (drawdown floor, position limit,
real-money-block invariant) — soft regime tightening is not a hard veto.
*Revisit if:* we observe a regime change where holding existing positions
through a forced-block window produced a materially worse outcome than a
forced-exit policy would have, on more than one occasion.

**3. Hypothesis Generator behavior on upstream kill events.** On a kill
event from upstream (Tech Watcher, Bottleneck Scout, Infra Mapper), the
Hypothesis Generator AUTO-processes the kill, AUTO-drafts the replacement
hypothesis, and HOLDS emission in `proposed` state pending Mike approval.
Automation handles the mechanical work; the human stays on the merge gate.
*Revisit if:* the proposed-state backlog grows faster than Mike can review,
OR if auto-drafted replacements turn out to require so much rewriting that
the drafting step is net-negative.

**4. Implementation Agent off-hours PRs.** Off-hours PRs are permitted on
non-protected paths at any hour. The Implementation Agent cannot self-merge
(existing rule, unchanged). Protected paths require an explicit Mike
approval token attached to the PR before it can be opened.
*Revisit if:* off-hours PRs produce a higher defect rate than business-hours
PRs once we have enough data to measure, OR if reviewing the off-hours
queue becomes a morning bottleneck.

**5. Risk Officer override mechanism.** Overrides require a signed-token
second factor with 24-hour maximum validity, time-bounded to the specific
policy change being overridden, prompted via CLI through TOTP or hardware
key. One token, one override, expires automatically.
*Revisit if:* the 24-hour window is repeatedly insufficient for legitimate
overrides (suggesting we need a session model), OR if the TOTP/hardware-key
prompt friction causes Mike to defer overrides past the point of usefulness.
**This is the highest reversal-risk decision in this ADR** — see Notes.

**6. Protected-paths list and risk policy split.** Protected paths:
`docs/agents/risk-compliance/`, `docs/agents/trading-floor/decision-maker.md`,
`docs/risk/policy.md` (once created), `docs/decisions/*.md`,
`infra/docker-compose.yml`. Not protected: Health Monitor, Tech Watcher,
Bottleneck Scout, Infra Mapper, Research reference taxonomies, runbooks.
Risk policy uses a HYBRID structure: hard kill switches live as code
constants (max drawdown floor, max position size, real-money-block
invariant); tunable thresholds live in `docs/risk/policy.md` as config
(Kelly fraction, correlation limits, regime sizing bands) and require
Mike approval to change.
*Revisit if:* we discover a protected path that materially slows iteration
on a non-load-bearing surface, OR if a tunable threshold turns out to need
the rigidity of a code constant.

**7. Strategy Evaluator unilateral demote on upstream kills.** YES for
hypothesis-stage and paper-stage strategies — the Evaluator can demote
without ceremony. NO for live-paper — those stay running for measurement
value even if the underlying thesis is killed upstream, because the
realized P&L of an invalidated thesis is itself data.
*Revisit if:* we accumulate live-paper strategies running on dead theses
to the point of resource or attention cost exceeding the measurement value.

**8. Shorts in universe.** Opt-in per hypothesis archetype. Infra-graph
plays are LONG ONLY. Bottleneck-rotation may short the old-layer incumbent
when obsolescence is rapid. This constrains Risk Officer borrow-cost
modeling and Strategy Evaluator friction tests to the bottleneck-rotation
archetype only — other archetypes do not pay that modeling cost.
*Revisit if:* a non-rotation archetype produces a clearly profitable short
thesis that we cannot express under the current rule. **Second-highest
reversal-risk decision** — see Notes.

## Decision — Part B: LLM Registry Pattern

Single source of truth at `docs/infrastructure/llm-registry.md`. Agent
specs reference **tier aliases**, not model names. The six tier aliases
are fixed vocabulary:

- `cloud-judgment-heavy`
- `cloud-default`
- `cloud-cheap`
- `local-classification`
- `local-heavy`
- `no-llm`

Tier aliases are the contract between agent specs and the model layer.
Which actual model serves each tier is registry data, not spec data.

**Update flow:**

1. A new model is announced upstream (Anthropic release, Ollama tag, etc.).
2. Tech Watcher emits a `research.tech-event-model-release` event (new
   event type added to its output set by this ADR).
3. The **Model Registry Maintainer** (a new agent spec under the Platform
   Department at `docs/agents/platform/model-registry-maintainer.md`, to be
   written in a follow-up commit) drafts a shadow-eval plan.
4. The eval runs against the current tier incumbent on a representative
   prompt set. Results are logged to the calibration ledger.
5. If the candidate passes, the Maintainer opens a PR updating the
   registry row plus the history table.
6. Mike approves. Merge is the change. No agent specs need to be touched —
   they reference the tier alias, which now resolves to the new model.

This makes model upgrades a single-PR operation gated by evidence,
instead of a global find-replace gated by hope.

## Decision — Part C: Generalized Principle — Versioned External Dependencies

The LLM Registry is the first concrete instance of a general pattern:

> Anything in the repo with a versioned external dependency, a date-bound
> assumption, or a profile that drifts as the world changes gets a named
> Maintainer responsibility and a staleness-check protocol.

Other instances exist in the repo today but are not yet acute enough to
need their own Maintainer:

- **Regime profile drift** — the regime label definitions themselves will
  drift as macro conditions change. Currently handled in-line by Research.
- **World-changer LIVE vs HISTORICAL classification drift** — what was
  once a structural break is now baseline.
- **Ticker behavioral profile staleness** — a ticker's volatility and
  correlation behavior changes as its business changes.

Universe Curator's profile-staleness scan and the calibration ledger
already address pieces of this. When any of these becomes acute, it gets
its own Maintainer spec on the same template as the Model Registry
Maintainer. Until then, we do not pre-build the abstraction.

## Alternatives Considered

**Hardcode model names per agent spec, update by global find-replace.**
Rejected. It does not scale past a handful of specs, and more importantly
it skips the shadow-eval step — model upgrades become "did the new name
work in casual testing" instead of "did the new model match or beat the
old one on the representative prompt set." That is exactly the silent
degradation we are trying to prevent.

**External config file outside the repo** (e.g., a service that resolves
tier → model at runtime). Rejected. Violates the "repo is the truth"
norm. The mapping from tier to model is exactly the kind of decision an
investigator should be able to reconstruct from `git log`.

**Skip the registry, accept staleness as a known cost.** Rejected. Every
agent would silently degrade as its hardcoded model name aged out, and
the degradation would surface as "agents got worse" with no causal
attribution.

## Consequences

**Enables:** All twelve existing agent specs can be migrated to tier
aliases in a single mechanical follow-up commit. `llm-routing.md` defers
to the registry instead of carrying its own model list. Model upgrades
become a one-PR change with shadow-eval evidence attached.

**Constrains:** A new Maintainer agent spec is required at
`docs/agents/platform/model-registry-maintainer.md`. The Tech Watcher's
output event set grows by one type (`research.tech-event-model-release`).
The calibration ledger grows a new section (e) for Model Registry Eval
Ledger. Agent specs must be patched to use tier aliases — until that
patch lands, the registry exists but specs are still inconsistent.

**Cost:** One markdown file (the registry). One agent spec (the
Maintainer). One calibration-ledger section. Mechanical patches across
twelve existing specs. Negligible runtime cost; small one-time editing
cost.

## Notes

Five of the eight boundary decisions carry meaningful reversal risk and
should be tracked explicitly in the calibration ledger so we know when
to revisit them rather than discovering the regret late:

- **#5 (override mechanism)** — highest risk. The 24-hour window and the
  TOTP/hardware prompt friction are both guesses. If either turns out to
  be wrong in practice, the override is the wrong shape and we will know
  it within the first few real uses.
- **#8 (shorts opt-in by archetype)** — second highest. The boundary
  ("infra-graph long only, bottleneck-rotation may short incumbent") is
  the kind of rule that produces a clear counterexample at exactly the
  wrong moment. Track for the first archetype-level short thesis that
  the rule blocks.
- **#2 (regime tightening blocks entries but not exits)** — moderate.
  Asymmetric and easy to second-guess after the fact.
- **#3 (auto-process kills, hold replacements proposed)** — moderate.
  Depends on Mike's review throughput.
- **#7 (live-paper preserved through upstream kill)** — moderate.
  Depends on whether the measurement value pays for the carrying cost.

The other three (#1 debounce parameters, #4 off-hours PRs, #6
protected-paths list) are also revisitable but lower-risk in the sense
that their failure modes are visible and incremental, not silent and
load-bearing.
