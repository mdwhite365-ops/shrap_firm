#!/usr/bin/env bash
#
# Report compose services that are DEFINED but NOT RUNNING on this host.
#
# Why this exists (KI-014, found 2026-07-27). The Dell is deployed one service
# at a time — `docker compose up -d --build --force-recreate <svc>` — because a
# bare `up -d --build` was found to leave running containers on stale images
# (2026-07-19). That per-service pattern has a silent failure mode: a service
# added to docker-compose.yml and never explicitly named in a deploy command is
# never created at all. Not stopped. Never created.
#
# It went unnoticed for weeks. The Strategy Librarian (PR #40) and Strategy
# Runner (PR #80) were both in the compose file and had never run in production
# when the firm's first strategy verdict was produced on 2026-07-27. The gap
# surfaced only because someone grepped `docker compose ps` by hand while
# looking for something else.
#
# The general property: `ps` shows what IS running, and nothing in the deploy
# path compares that against what SHOULD be. This script is that comparison.
#
# Tools-profile services (market-data, strategy-evaluator, infra-mapper) are
# run-to-completion and correctly absent from a running stack. `docker compose
# config --services` excludes profiled services unless their profile is active,
# so they are filtered out by construction rather than by a hardcoded list —
# a new tools-profile service needs no change here.
#
# Usage — NOTE the sudo. On the Dell, truenas_admin is not in the docker group,
# so anything touching the daemon needs it. `compose config` does NOT touch the
# daemon and `compose ps` DOES, so without sudo this script gets past the first
# check and fails the second, which is confusing unless the real error is shown.
#
#   sudo ./infra/check-deploy-drift.sh          # report; exit 1 if drift
#   sudo ./infra/check-deploy-drift.sh --quiet  # exit code only, no output
#
# Exit codes: 0 = no drift, 1 = services missing, 2 = could not determine.

set -euo pipefail

cd "$(dirname "$0")"

QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

log() { [[ $QUIET -eq 1 ]] || printf '%s\n' "$*"; }

# Never discard stderr from these probes. The first version of this script sent
# it to /dev/null, so a plain permission-denied surfaced as the unhelpful
# "could not list running services" and the actual cause had to be guessed.
# A diagnostic tool that hides its own diagnostics is worse than no tool.
err_file=$(mktemp)
trap 'rm -f "$err_file"' EXIT

fail() {
  log "ERROR: $1"
  if [[ -s "$err_file" ]]; then
    log ""
    log "underlying error:"
    sed 's/^/  /' "$err_file" | while IFS= read -r line; do log "$line"; done
  fi
  if grep -qiE "permission denied|dial unix|docker daemon" "$err_file"; then
    log ""
    log "This looks like a docker-daemon permission problem. Re-run with sudo:"
    log "  sudo ./infra/check-deploy-drift.sh"
  fi
  exit 2
}

# `config --services` without an active profile lists only always-on services.
# It parses the file and does NOT contact the daemon, so it can succeed while
# the `ps` call below fails — do not treat its success as proof of access.
if ! defined=$(docker compose config --services 2>"$err_file" | sort); then
  fail "could not read compose config (wrong directory, or compose not installed)"
fi

if ! running=$(docker compose ps --services --status running 2>"$err_file" | sort); then
  fail "could not list running services"
fi

missing=$(comm -23 <(printf '%s\n' "$defined") <(printf '%s\n' "$running") || true)

if [[ -z "${missing//[[:space:]]/}" ]]; then
  log "OK — every always-on compose service is running ($(printf '%s\n' "$defined" | grep -c .) services)."
  exit 0
fi

log "DEPLOY DRIFT — defined in docker-compose.yml but NOT running:"
log ""
while IFS= read -r svc; do
  [[ -z "$svc" ]] && continue
  # Distinguish "never created" from "created but stopped" — different causes.
  if docker compose ps -a --services 2>/dev/null | grep -qx "$svc"; then
    log "  $svc  (container exists but is not running — check logs, it may be crash-looping)"
  else
    log "  $svc  (NO CONTAINER — never created; it was never named in a deploy)"
  fi
done <<< "$missing"
log ""
log "To create the missing ones:"
log "  sudo docker compose up -d --build --force-recreate $(printf '%s ' $missing)"
exit 1
