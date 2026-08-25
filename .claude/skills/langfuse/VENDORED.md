# Provenance

Vendored from <https://github.com/langfuse/skills>, MIT licensed (see `LICENSE`).

- Source path: `skills/langfuse/`
- Commit: `ff47830ae782fe422c565361b0742789e5dc9f62` (2026-08-20)
- Vendored: 2026-08-25

Committed rather than installed per-machine so that any session working on
`src/shrap/llm/` gets the same guidance, and so the version that produced a
given audit is recoverable from `git log`.

**Its first principle is "never implement from memory — fetch current docs."**
That applies to this copy too: it is a snapshot, and Langfuse's live docs are
the authority. Re-pull before trusting it on anything version-sensitive.

Used in #208 to audit the firm's tracing. Findings are recorded in
`docs/status/known-issues.md` under KI-018, and the server end-of-life problem
it surfaced under KI-032.
