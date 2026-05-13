# ADR-0002: Rename "Infrastructure and Growth" to "Platform"

**Status:** Accepted
**Date:** 2026-05-06
**Deciders:** Mike White

## Context

The vision document (`docs/00-vision.md`) names the ninth department
"Infrastructure and Growth Department." On drafting the architecture
document, the name proved misleading: "Growth" naturally reads as
revenue or capital growth, which is the implicit responsibility of
Research and the Trading Floor, not this department.

## Decision

Rename the department to "Platform Department" in the architecture
document and going forward. The department's role — managing LLM
migration, tracking cost, planning infrastructure changes — is the
platform's evolution, not the firm's growth.

## Alternatives Considered

**Keep "Infrastructure and Growth."** Continuity with the vision doc.
Eliminated: the naming confusion compounds across future agent specs
and reports.

**"Infrastructure Department."** Accurate but understates scope —
LLM migration is broader than infrastructure.

## Consequences

The vision document (`docs/00-vision.md`) will be updated in a follow-up
commit to match. All future agent specs and reports use "Platform
Department." No code changes required (no code exists yet).

## Notes

Naming inconsistency between vision and architecture lasts until the
vision update commit. Acceptable for a short window; the architecture
doc is the more frequently referenced of the two during the build.
