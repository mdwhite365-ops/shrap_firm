# ADR-0008: Research Funnel Open-Items Resolutions

**Status:** Accepted
**Date:** 2026-05-30
**Deciders:** Mike White

## Context

ADR-0007 introduced the three-step Research funnel (World-Changers + Infrastructure Graphs + Bottleneck Scouting) and the supporting reference taxonomies in `docs/research/`. The subagents writing the agent specs and taxonomies surfaced five open items that needed Mike's decision before the funnel could be considered locked. This ADR records the decisions in one place so the affected specs can reference a single source of truth.

## Decisions

### 1. Stream naming convention

**Decision:** Use hyphens, per ADR-0006 (`<department>.<event-type>`).

**Specific event-type names locked:**

- `research.world-changer-proposed`, `research.world-changer-promoted`, `research.world-changer-killed`, `research.world-changer-updated`
- `research.bottleneck-detected`, `research.bottleneck-validated`, `research.bottleneck-binding`, `research.bottleneck-killed`
- `research.infra-graph-added`, `research.infra-graph-updated`, `research.infra-graph-removed`
- `research.universe-proposed-add`, `research.universe-proposed-remove`
- `intel.regime-state`, `intel.regime-sizing-modifier`

ADR-0007 prose used dot notation in places. That was informal — ADR-0006 governs. No ADR-0006 amendment needed.

### 2. World-Changer LIVE / HISTORICAL split

**Decision:** Confirm the proposed split as written in `docs/research/world-changer-archetypes.md`:

- **LIVE (May 2026):**
  - (a) compute-substrate revolutions
  - (b) biological-mechanism unlocks
  - (c) cost-curve crossings
  - (e) platform shifts
- **HISTORICAL only (no Mike-promoted candidates at investment-grade probability):**
  - (d) physical-realization breakthroughs (fusion, room-temp superconductors)

**Reasoning:** Physical-realization breakthroughs have no public-company investment vehicle at credible probability in May 2026. Helion, Commonwealth Fusion, and the room-temp-superconductor noise of 2023 are all either private or fully discredited. If a public path emerges (e.g. a fusion company files S-1 with binding milestones), Tech Watcher promotes (d) to LIVE via a new world-changer candidate.

### 3. AI agents as own archetype

**Decision:** No. AI agents stay folded into compute-substrate + platform-shift.

**Reasoning:** AI agents are an *application* of (a) compute-substrate (the NVIDIA-AI thesis already in place) crossed with (e) platform-shift (the new interaction surface). Giving agents their own archetype double-counts the dependency: the infra graph for "AI agents as a world-changer" is a strict subgraph of the NVIDIA compute graph plus model-weights/orchestration layers. Tech Watcher tracks AI agents as one of the active platform-shift candidates instead.

**Action:** Add an explicit cross-reference note in `docs/research/world-changer-archetypes.md` under (a) and (e) flagging the overlap, so the Tech Watcher doesn't separately propose the same thing under two archetypes.

### 4. Strategy Evaluator 150-trade gate vs multi-quarter infra-graph plays

**Decision:** Two promotion paths — either passes. Specific to hypothesis archetype.

**For `bottleneck-rotation` hypotheses** (weeks-to-quarters horizon, rotation-frequency higher):

- Standard ADR-0007 / strategy-evaluator gate: walk-forward + PBO + Deflated Sharpe + purged CV + minimum 150 distinct trades.

**For `infra-graph-play` hypotheses** (quarters-to-years horizon, low-frequency by design):

- Alternative gate: walk-forward window covers ≥3 years AND ≥6 distinct infra-graph plays evaluated across ≥2 regime states (regime as classified by `intel.regime-state`) AND aggregated portfolio-level Sharpe + PBO + CPCV pass at the standard thresholds. Trade count is not the gating metric; cross-play and cross-regime coverage is.

**Action:** Patch `docs/agents/research/strategy-evaluator.md` to spell both gates out, with explicit rule that the gate selected is determined by the hypothesis archetype declared in the originating `research.hypothesis-proposed` event. No hypothesis can switch archetype mid-evaluation to escape the harder gate.

**Honest read:** the infra-graph gate is more vulnerable to optimistic backtesting than the trade-count gate — fewer independent samples, more correlation across plays. The PBO and CPCV checks have to be applied especially strictly. The calibration ledger (`docs/research/calibration.md`) should track infra-graph promotion vs kill rates separately from bottleneck-rotation promotion vs kill rates, because the base rates will diverge.

### 5. LITE backwards-test rubric

**Decision:** Resolved by separate deliverable. See `docs/research/lite-backwards-test-rubric.md` (written 2026-05-30). The rubric flags four sub-items still requiring Mike's call: red-herring slate sourcing, evaluator identity (Mike personally vs two Sonnet subagents), time-to-bind band width, and whether fabricated citations hard-fail the run or only void affected promotions. These are deferred to actual test-run time, not gate items for spec lock.

## Consequences

**Enables:**

- Hypothesis Generator and Strategy Evaluator specs now have a single ADR to cite when their hypothesis-archetype-dependent behavior is questioned
- Stream naming is locked, so the Redis envelope registry can be written without ambiguity
- World-Changer archetype taxonomy is closed for the sprint (Tech Watcher can be implemented without waiting on archetype churn)
- Backwards-test methodology is sufficiently specified to be executed once Bottleneck Scout code exists

**Defers:**

- Infra-graph promotion gate may prove too lax in practice. The calibration ledger will surface this if the promotion rate exceeds ~20% on infra-graph plays (cross-reference: bottleneck-rotation promotion rate is targeted at <10% per ADR-0007).
- Whether AI agents earn their own archetype is revisited if the cross-reference rule starts collapsing meaningful distinctions (i.e. Tech Watcher keeps proposing the same agent-platform candidate twice).

## Alternatives Considered

- **Single trade-count gate for both hypothesis archetypes.** Rejected: makes infra-graph plays effectively unpromotable, killing the load-bearing edge claim from ADR-0007.
- **Separate ADRs per open item.** Rejected: five small ADRs is administrative overhead with no compounding value. One ADR is enough.
- **Defer all five items to forward-test time.** Rejected: items 1, 4 block spec lock today.

## Notes

The Bottleneck Scout backwards-test rubric is **separate** from this ADR (it is a methodology doc, not a binding decision). When the backwards-test runs, the verdict appends to `docs/research/calibration.md` per the calibration ledger's append-only rules, and any rubric refinements driven by the test become ADR-0009 or later.
