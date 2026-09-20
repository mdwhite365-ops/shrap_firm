# Changes — one file per card

**Write your card's entry as a new file in this directory. Never append to
`../recent-changes.md`.**

```
docs/status/changes/<pr-number>-<short-slug>.md
```

`261-session-quota.md`, `262-langfuse-client.md`. No frontmatter, no index to
update, no shared anchor — the filename carries the PR number and the directory
listing carries the order.

## Why this exists

`recent-changes.md` had a trailing `## Security notes` section, so every card
appended at the same anchor. Two cards touching the file in one week therefore
*always* conflicted, and merging one re-broke every other open PR. On
2026-09-20 that cost four resolution passes across #258, #260, #261 and #262 —
each one a merge whose only content was keeping both sections, in order.

It is the same failure the merge of #249/#250 caused in `CLAUDE.md`, where two
PRs edited one paragraph on one day and left the file asserting that Qdrant both
held zero collections and was live. **Two cards touching one doc paragraph is a
conflict git resolves by keeping both** — so stop making them touch it.

**Separate files cannot conflict.** That is the whole mechanism.

`merge=union` in `.gitattributes` was considered and rejected: it resolves
correctly on a local `git merge` (verified), but GitHub's precomputed
`mergeable` field does not apply the driver, so a PR would still *display* as
conflicting. A false conflict is worse than a real one.

## Shape of an entry

Match the prose style of the existing `recent-changes.md` sections — a heading
that states what changed, then what was actually measured. The vision doc sets
the tone: clear prose, honest framing, no marketing language. Say what was
verified and how, and say what is still unknown.

```markdown
### What changed, stated as an outcome (#261)

What was wrong, with the evidence. What the fix does. What was measured rather
than assumed, and what remains open.
```

## Reading them together

```bash
make changelog     # concatenates this directory, newest last
```

`../recent-changes.md` keeps everything written before 2026-09-20 and is
**frozen** — history, not a destination.
