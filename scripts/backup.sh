#!/usr/bin/env bash
#
# Nightly backup of every durable volume the firm has.
#
# The contract from the runbook: named volumes are the only durable state. Back
# them up and you can restore; lose them and the firm's memory is gone. As of
# 2026-08-25 there had never been a backup — no crontab entry existed and
# /mnt/backups did not exist either (KI-034).
#
# This replaces four separate cron lines, each of which had its own way of
# failing quietly:
#
#   * They referenced "$SHRAP_DB_USER", which is set in infra/.env and NOT in
#     cron's environment. Every one would have run `pg_dumpall -U ""`.
#   * They redirected with `>`, which creates the file whether or not the dump
#     succeeded. A failed backup left a truncated .gz that looks like a backup
#     from `ls`, which is worse than leaving nothing.
#   * The Qdrant line POSTed to the snapshots API and wrote the *response* to a
#     .json. The snapshot stayed inside the container. It was never a backup.
#
# What this does instead:
#
#   * Reads credentials from inside each container, so there is no environment
#     to get wrong.
#   * Writes to `<name>.partial`, verifies it, and only then renames. A file in
#     the backup directory has therefore been checked.
#   * Verifies gzip integrity and a plausible minimum size, because a valid
#     empty archive is still not a backup.
#   * Exits non-zero on the first failure and says which stage failed.
#
# Usage:  scripts/backup.sh [destination]   (default /mnt/backups)

set -euo pipefail

DEST="${1:-/mnt/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
STAMP="$(date +%F)"
# A dump smaller than this is not a real one. An empty gzip is ~20 bytes and
# passes `gzip -t` happily, so integrity alone does not prove content.
MIN_BYTES="${BACKUP_MIN_BYTES:-1024}"

# How to invoke Docker. `truenas_admin` is not in the `docker` group on this
# host, so an interactive run needs sudo; the scheduled run is root and does
# not. Read into an array so DOCKER="sudo docker" splits into two words rather
# than being looked up as a single command named "sudo docker".
# `|| true`: a here-string always supplies a terminated line so `read` returns 0,
# but `set -e` would turn any surprise into an exit before the first log line.
read -r -a DOCKER_CMD <<< "${DOCKER:-docker}" || true

log() { printf '[backup %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
fail() { printf '[backup %s] FAILED: %s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }

# Resolve a volume name from the running container rather than guessing it.
# Compose derives the prefix from the project directory, so the name depends on
# where the stack was brought up — `shrap_firm_redis_data` and
# `infra_redis_data` are both plausible and only one is real.
volume_for() {
    local container="$1" mountpoint="$2" name
    name="$("${DOCKER_CMD[@]}" inspect -f \
        "{{range .Mounts}}{{if eq .Destination \"$mountpoint\"}}{{.Name}}{{end}}{{end}}" \
        "$container" 2>/dev/null)" || fail "cannot inspect $container"
    [ -n "$name" ] || fail "no volume mounted at $mountpoint in $container"
    printf '%s' "$name"
}

# Verify then publish. Nothing lands in $DEST under its real name until it has
# passed both checks, so the presence of a file is itself the assertion.
publish() {
    local partial="$1" final="$2" size
    [ -s "$partial" ] || { rm -f "$partial"; fail "$(basename "$final") is empty"; }
    size="$(wc -c < "$partial")"
    if [ "$size" -lt "$MIN_BYTES" ]; then
        rm -f "$partial"
        fail "$(basename "$final") is only ${size}B (under ${MIN_BYTES}B) — treating as a failed dump"
    fi
    if ! gzip -t "$partial" 2>/dev/null; then
        rm -f "$partial"
        fail "$(basename "$final") is not a valid gzip"
    fi
    mv "$partial" "$final"
    log "ok: $(basename "$final") (${size}B)"
}

# --- Postgres: the one that matters -------------------------------------------
# Every order, fill, risk decision, verdict, equity point and strategy record.
# Credentials come from the container's own environment, which is the same
# pattern every ad-hoc psql query in this project uses.
dump_postgres() {
    local container="$1" label="$2"
    local final="$DEST/shrap-${label}-${STAMP}.sql.gz"
    local partial="${final}.partial"
    log "dumping $label from $container"
    if ! "${DOCKER_CMD[@]}" exec -i "$container" sh -c 'pg_dumpall -U "$POSTGRES_USER"' \
        | gzip > "$partial"; then
        rm -f "$partial"
        fail "pg_dumpall failed for $container"
    fi
    publish "$partial" "$final"
}

# --- Redis: flush to disk first, then copy the volume -------------------------
# BGSAVE is asynchronous. Waiting on rdb_bgsave_in_progress is the difference
# between copying a current snapshot and copying whatever was on disk from the
# last one, which could be hours stale.
dump_redis() {
    local container="shrap_redis"
    local final="$DEST/shrap-redis-${STAMP}.tar.gz"
    local partial="${final}.partial"
    local volume waited=0
    log "BGSAVE on $container"
    "${DOCKER_CMD[@]}" exec -i "$container" redis-cli BGSAVE >/dev/null || fail "redis BGSAVE failed"
    while "${DOCKER_CMD[@]}" exec -i "$container" redis-cli INFO persistence 2>/dev/null \
        | grep -q 'rdb_bgsave_in_progress:1'; do
        waited=$((waited + 2))
        [ "$waited" -gt 120 ] && fail "redis BGSAVE still running after 120s"
        sleep 2
    done
    volume="$(volume_for "$container" /data)"
    log "archiving volume $volume"
    "${DOCKER_CMD[@]}" run --rm -v "$volume":/data:ro -v "$DEST":/backup alpine \
        tar czf "/backup/$(basename "$partial")" -C /data . \
        || { rm -f "$partial"; fail "redis volume archive failed"; }
    publish "$partial" "$final"
}

# --- Qdrant: filesystem copy, and honest about what that means ----------------
# This is a copy of the volume, not a coordinated snapshot. Qdrant may be
# mid-write. Acceptable because nothing in the firm currently treats Qdrant as a
# system of record — if that changes, this needs the snapshots API and a
# download, not a tar.
dump_qdrant() {
    local container="shrap_qdrant"
    local final="$DEST/shrap-qdrant-${STAMP}.tar.gz"
    local partial="${final}.partial"
    local volume
    if ! "${DOCKER_CMD[@]}" inspect "$container" >/dev/null 2>&1; then
        log "skip: $container is not running"
        return 0
    fi
    volume="$(volume_for "$container" /qdrant/storage)"
    log "archiving volume $volume"
    "${DOCKER_CMD[@]}" run --rm -v "$volume":/data:ro -v "$DEST":/backup alpine \
        tar czf "/backup/$(basename "$partial")" -C /data . \
        || { rm -f "$partial"; fail "qdrant volume archive failed"; }
    publish "$partial" "$final"
}

# Check daemon access once, up front, and say what to do about it. The first
# real run of this script failed on a raw "permission denied ... docker.sock"
# buried inside a stage failure, which is an accurate message that does not tell
# you the fix.
preflight() {
    "${DOCKER_CMD[@]}" info >/dev/null 2>&1 && return 0
    fail "cannot reach the Docker daemon as $(id -un). This host does not put
    the deploying user in the 'docker' group, so an interactive run needs:

        sudo /mnt/Archive/shrap/shrap_firm/scripts/backup.sh

    or, to keep the destination writable by your own user:

        DOCKER='sudo docker' $0 $DEST

    A scheduled run under root needs neither."
}

main() {
    log "destination $DEST"
    mkdir -p "$DEST" || fail "cannot create $DEST"
    preflight
    dump_postgres shrap_postgres postgres

    # Langfuse moved to Cloud on 2026-08-25 (KI-032). The local instance still
    # runs, so it is still backed up — skipped rather than failed if it is
    # retired, so decommissioning it does not break the nightly job.
    if "${DOCKER_CMD[@]}" inspect shrap_langfuse_db >/dev/null 2>&1; then
        dump_postgres shrap_langfuse_db langfuse
    else
        log "skip: shrap_langfuse_db is not running"
    fi

    dump_redis
    dump_qdrant

    log "pruning backups older than ${RETENTION_DAYS} days"
    find "$DEST" -maxdepth 1 -name 'shrap-*' -type f -mtime "+${RETENTION_DAYS}" -delete

    # Leave any .partial behind visible rather than tidying it away — its
    # presence is the record that a run died part-way.
    log "done. $(find "$DEST" -maxdepth 1 -name 'shrap-*.gz' -type f | wc -l) archives present"
}

# Only run when executed, not when sourced. `tests/operations/test_backup_script.py`
# sources this file to exercise `publish` directly — the guard that decides
# whether a failed dump is allowed to look like a backup is the one part of this
# worth testing, and it needs no Docker to test.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
