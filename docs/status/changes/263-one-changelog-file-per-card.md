### Cards stopped sharing one changelog file (#263)

`docs/status/recent-changes.md` ended in a `## Security notes` section, so every
card appended at the same anchor. Two cards touching the file in one week
therefore **always** conflicted, and merging one re-broke every other open PR.

On 2026-09-20 that cost four resolution passes across #258, #260, #261 and #262.
Each was a merge commit whose entire content was *keep both sections, in order* —
a mechanical resolution git could not make itself only because both sides had
written to the same place. Mike's words: *"each time i merge one the others
conflict."*

It is the same failure the merge of #249/#250 caused in `CLAUDE.md`, where two
PRs edited one paragraph on one day and left the file asserting that Qdrant both
held zero collections and was live.

**New entries are now one file per card**, `docs/status/changes/<pr>-<slug>.md`.
Separate files cannot conflict; that is the whole mechanism. `recent-changes.md`
is frozen and keeps its history. `make changelog` reads the directory in merge
order.

**`merge=union` was considered and rejected, after testing rather than reading
about it.** In a scratch repo reproducing this exact collision, a `.gitattributes`
line resolved it correctly with no conflict — the driver works. But GitHub's
precomputed `mergeable` field does not apply `.gitattributes` merge drivers, so
the PR would still *display* as conflicting while being perfectly mergeable. One
line of config that turns a real conflict into a false one is a worse trade than
the problem.
