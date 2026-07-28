# Runbook — deploying and running services after a code change

**Last updated:** 2026-07-28

Covers both service kinds, because the same mistake has now been made in both:

- **Tools-profile** (`market-data`, `strategy-evaluator`, `infra-mapper`) —
  run-to-completion, invoked on demand, exit when done.
- **Always-on** (everything else) — long-running agents.

Two failure modes recur here, both found the hard way on 2026-07-27/28 while
running the first strategy evaluations. Neither produces a useful error message
on its own, and both present as "the code is merged, so it must be running."


## 1. Build before running, after any code change

`docker compose run` **does not rebuild the image.** It reuses whatever was
built last, so a tools service silently runs stale code indefinitely.

The symptom is misleading: after adding two CLI subcommands and pulling
successfully, the container still reported

```
shrap-strategy-seed: error: argument action: invalid choice: 'load-probe'
                     (choose from load-first, list)
```

The code was on disk. The image was from the night before.

**Always:**

```bash
cd /mnt/Archive/shrap/shrap_firm/infra
sudo docker compose --profile tools build strategy-evaluator
sudo docker compose --profile tools run --rm strategy-evaluator <command>
```

This is the run-to-completion analogue of the always-on `--force-recreate`
lesson (2026-07-19), where `up -d --build` built a new image but left the
container on the old one. Same class of problem, different command.

**`check-deploy-drift.sh` cannot catch this.** It compares *which services are
running* against which are defined. A tools service is correctly not running,
and image staleness is invisible to it. Different check; not currently built.

### The always-on twin of the same mistake

The identical trap exists for always-on services, and it was hit the same day.

PR #100 changed `librarian_service.py` and `strategy_registry.py`. The
`strategy-evaluator` image was rebuilt (because the probe commands needed it);
`strategy-librarian` was not, because nobody thought to name it. It kept running
the previous image and kept emitting the exact ERROR-plus-traceback the PR had
just fixed — while `docker compose ps` showed it healthy and `git log` showed
the fix merged.

**After merging anything, rebuild every service whose source it touched**, not
only the one you are about to invoke:

```bash
sudo docker compose up -d --build --force-recreate <service>
```

Confirm by the container's own startup log line, not by the compose summary
(`Running 0.0s` means untouched). A service's `config_loaded` timestamp older
than the merge is the tell.

**Verification caveat.** Restarting a stream consumer does **not** replay
already-acked events — `start_id` applies only when the consumer group is
first created. A logging or handling fix therefore cannot be observed against
old events; it needs a new one. Do not record such a fix as verified live until
a fresh event has exercised it.

### One image can back more than one service

`strategy-evaluator` (tools profile, on demand) and
`strategy-evaluator-trigger` (always-on sweep) are built from the **same**
`infra/strategy-evaluator.Dockerfile`. A change to
`src/shrap/research/strategy_evaluator/` affects both, and rebuilding one does
not recreate the other:

```bash
sudo docker compose --profile tools build strategy-evaluator
sudo docker compose up -d --build --force-recreate strategy-evaluator-trigger
```

This is the librarian mistake generalised: the question is never "which service
did I run" but "which containers are running code this change touched."
`docker compose config --services` lists every service; grep the compose file
for the Dockerfile you changed to find all of its consumers.

## 2. Do not override the container user

Tools containers run as `USER shrap` (uid **10001**), and bind-mounted output
directories are owned by 10001 to match — see the one-time host setup documented
in the `strategy-evaluator` block of `docker-compose.yml`.

Passing `--user "$(id -u):$(id -g)"` **breaks this.** On the Dell,
`truenas_admin` is uid 950; the mounted directory is owned by 10001 with mode
`755`, so a 950 process falls through to `other` (`r-x`) and cannot create the
per-strategy subdirectory:

```
PermissionError: [Errno 13] Permission denied: '/cards/01KY...'
```

Ownership looks right in `ls -ld` — that is what makes it confusing. The
directory *is* owned correctly; the override is what created the mismatch.

**Run tools services with their default user.** No `--user` flag.

Cards land owned by 10001 with mode 644, which is world-readable, so `git add`
works without any chown. You only lack permission to *create* files there as
950, which is not something you need to do.

## Diagnosing a permission failure on a bind mount

Check ownership and the container's uid before changing anything:

```bash
id -u; id -g                                    # host user (950 on the Dell)
ls -ld docs/strategies/evaluations              # mount source owner + mode
getfacl docs/strategies/evaluations | head      # ZFS ACLs, if any
```

A trailing `+` on the permission bits in `ls -ld` means ACLs are present and
POSIX ownership alone will not explain access. On the Dell as of 2026-07-28
there are none — plain `user::rwx group::r-x other::r-x`.

The order that matters: **read the actual ownership before proposing a fix.**
This problem was "solved" three times on 2026-07-27/28 — `mkdir`, then
`chown 10001`, then `--user` — each reasoned from the previous error rather
than from the filesystem. The third made it worse by breaking a configuration
that was already correct.

## Evaluator specifics

- Cards are written **first** in `commit()`, before the registry transition,
  the `research.evaluations` row, and the event publish. A card-write failure
  therefore persists nothing and a retry is clean — verified twice.
- Every strategy is single-use. `killed` is terminal
  (`ALLOWED_TRANSITIONS[killed]` is empty) and re-evaluating refuses with
  *"is 'killed'; this card evaluates only 'hypothesis'-stage strategies"*.
  A new experiment needs a new seed with a distinct `spec_hash`.
- Always `--dry-run` first and read the trade count and verdict before
  committing. A promoted strategy now reaches the deployed Strategy Runner and
  begins emitting paper signals at the next market-phase `open`.
