#!/usr/bin/env bash
# Report status documents that have fallen behind `main`.
#
# This exists because the same failure has now happened three times, each
# larger than the last: #72-80 (nine PRs), #92-101 (ten), #129-175 (forty-six).
# In the third case every status doc was stamped 2026-07-28 while `main` was at
# #175, and CLAUDE.md still named session-handoff.md as ground truth for what
# to pick up next. A roadmap in that state describes finished work as pending,
# which is how a card gets written twice.
#
# Three lapses of diligence is a missing mechanism. This is the mechanism: it
# compares the highest PR number recorded in each status doc against the newest
# merge commit on `main`, and fails if any doc trails by more than the
# threshold. It reads git, not GitHub, so it needs no network and no auth.
#
# Usage:  make doc-drift            # default tolerance
#         DOC_DRIFT_MAX=0 make doc-drift
set -uo pipefail

MAX_LAG="${DOC_DRIFT_MAX:-5}"

DOCS=(
  "docs/status/recent-changes.md"
  "docs/status/session-handoff.md"
  "docs/roadmap/implementation-timeline.md"
  "CLAUDE.md"
)

head_pr="$(git log --grep='Merge pull request' -1 --format=%s 2>/dev/null |
  grep -oE '#[0-9]+' | head -1 | tr -d '#')"

if [[ -z "${head_pr}" ]]; then
  echo "doc-drift: no merge commits found on this branch — nothing to compare."
  exit 0
fi

echo "main is at PR #${head_pr} (tolerance: ${MAX_LAG})"
echo

status=0
for doc in "${DOCS[@]}"; do
  [[ -f "${doc}" ]] || { printf '  %-46s MISSING\n' "${doc}"; status=1; continue; }

  # Highest PR number mentioned anywhere in the document.
  doc_pr="$(grep -oE '#[0-9]{1,4}' "${doc}" | tr -d '#' | sort -n | tail -1)"
  [[ -z "${doc_pr}" ]] && doc_pr=0

  lag=$(( head_pr - doc_pr ))
  if (( lag > MAX_LAG )); then
    printf '  %-46s newest #%-4s  BEHIND BY %s\n' "${doc}" "${doc_pr}" "${lag}"
    status=1
  else
    printf '  %-46s newest #%-4s  ok\n' "${doc}" "${doc_pr}"
  fi
done

if (( status != 0 )); then
  cat <<'EOF'

A status document is behind main. Before planning anything from it:

    git log --oneline --grep="Merge pull request" -20

Trust git, `docker compose ps` and the database over any document that fails
this check. Update the doc, or raise DOC_DRIFT_MAX if the lag is deliberate.
EOF
fi

exit "${status}"
