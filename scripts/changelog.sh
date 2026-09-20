#!/usr/bin/env bash
# Concatenate the per-card change entries, oldest first.
#
# Entries are named <pr-number>-<slug>.md and sorted numerically on that prefix,
# so the reading order is merge order. README.md is the directory's own
# instructions, not an entry, and is skipped.
set -euo pipefail

dir="$(cd "$(dirname "$0")/.." && pwd)/docs/status/changes"
[ -d "$dir" ] || { echo "no $dir" >&2; exit 1; }

found=0
while IFS= read -r f; do
  found=1
  cat "$f"
  echo
done < <(find "$dir" -maxdepth 1 -name '*.md' ! -name 'README.md' -print | sort -t/ -k99 -V)

if [ "$found" = 0 ]; then
  echo "No entries yet. Frozen history: docs/status/recent-changes.md"
fi
