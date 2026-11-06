#!/usr/bin/env bash
# Run the read-only firm-health report against the Postgres container.
#
#   scripts/firm-health.sh                      # defaults
#   scripts/firm-health.sh -v window_days=20    # any psql -v override passes through
#
# Exit code: 2 if any ALERT, 1 if any WARN, 0 otherwise, so a scheduler or an
# overseer agent can branch on it without parsing the table.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
container="${SHRAP_POSTGRES_CONTAINER:-shrap_postgres}"
docker_cmd=(docker)
if ! docker info >/dev/null 2>&1; then docker_cmd=(sudo docker); fi

out="$("${docker_cmd[@]}" exec -i "$container" \
  sh -c 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"' psql "$@" \
  < "$here/firm_health.sql")"
printf '%s\n' "$out"

summary="$(printf '%s\n' "$out" | awk -F'|' '$1 ~ /SUMMARY/ {gsub(/ /,""); print $2" "$3}')"
read -r alerts warnings <<<"${summary:-0 0}"
if [[ "${alerts:-0}" -gt 0 ]]; then exit 2; fi
if [[ "${warnings:-0}" -gt 0 ]]; then exit 1; fi
exit 0
