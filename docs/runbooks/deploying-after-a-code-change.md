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

**`--profile tools` is required on the *build* line too, not just the run line.**
A plain `docker compose build` skips profiled services silently — it prints a
normal-looking success for everything else and leaves the tools images
untouched. Hit again on 2026-07-29: a full-stack rebuild had just run, yet

```
shrap-market-data-backfill: error: unrecognized arguments: --launch-list
shrap-strategy-seed: error: argument action: invalid choice: 'load-technical'
```

Both flags had been merged for days. Both images were from before that merge.
An argument-parser error is the tell — it reads like a typo in the command,
which is why this keeps costing time.

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

### 1c. Two services can share a Dockerfile and still be separate build targets

`strategy-evaluator` (tools profile, on demand) and `strategy-evaluator-trigger`
(always-on) are built from the **same Dockerfile** and run the **same pipeline**.
They are two compose services, so building one leaves the other untouched.

This is §1b's lesson inverted. That one says *build the profile too*; this one
says **the profiled build does not cover the always-on service that shares its
image.**

It bit on 2026-07-30 and was invisible for hours, because a stale evaluator does
not error — it produces confident, well-formed, **wrong** verdicts:

- the long/short momentum strategy evaluated to a Sharpe of `0.782248971076797`,
  identical to the long-only original to fifteen decimal places. The image
  predated PR #145, so `long_short` was parsed out of the spec and ignored, and
  a two-sided rule was measured as a one-sided one.
- the standdown revision returned `hold-for-data` where PR #144 requires
  `kill (worse-than-parent)`. That gate was not in the image either.

Both rows persisted. `latest_information_ratio` reads the newest row for the
parent comparison, so a wrong verdict propagates into the *next* strategy's.

**After any research-code change, rebuild all three.** `hypothesis-generator`
joined the same Dockerfile on 2026-07-30 — it needs numpy for the capability
check, so it cannot live in the tech-watcher image (see §1d):

```bash
cd /mnt/Archive/shrap/shrap_firm/infra
sudo docker compose --profile tools build strategy-evaluator hypothesis-generator
sudo docker compose up -d --build --force-recreate strategy-evaluator-trigger
```

**Verify the trigger specifically.** The compose summary is not evidence — a
container left on the old image prints `Running 0.0s` rather than `Started`.
Read its startup log, or re-run one known strategy through the tools CLI with
`--dry-run` and check the numbers moved.

**The general rule:** an always-on service that shares an image with a tools
service is the one that will be forgotten, because the tools service is the one
you type commands at. If a verdict looks *identical* to another strategy's,
suspect the image before suspecting the data.

## 1d. An agent image only has the dependencies its agent needs

The two failures above are "the new code is not running." This one is the
opposite and reads nothing like them: the new code **is** running, and the
container crash-loops before its first log line.

Agent images install a subset of the runtime dependencies.
`tech-watcher.Dockerfile` installs redis, httpx, structlog, pydantic,
pydantic-settings and asyncpg. `strategy-evaluator.Dockerfile` adds numpy and
pandas, because the stats core needs them and nothing else does. That boundary
is invisible in the source, absolute at runtime, and **cannot be caught by
`pytest` or `mypy --strict`** — the dev environment has everything.

Hit on 2026-07-30 by PR #157, which gave Tech Watcher one line:

```python
from shrap.research.hypothesis_generator.literature import PostgresLiteratureStore
```

`literature.py` imports stdlib only. But the package's `__init__.py`
re-exported `HypothesisGenerator` for convenience, and **a submodule import
runs the package `__init__` first** — so that line pulled in the strategy
evaluator, then numpy, and `tech-watcher` died on `ModuleNotFoundError`. 368
modules imported where 39 were needed.

The traceback names the innocent module. `grep`ping the logs for the feature
shows the import line repeating on every restart, which reads like the feature
failing rather than the container never starting.

**The rule:** a convenience re-export in an `__init__` is an invisible
dependency edge from every consumer of every submodule to every module the
package touches. Import the submodule you want, and keep package roots empty
where two different images consume different parts of one package.

**Checking it costs one command**, and it is worth running before deploying any
cross-package import into an agent container:

```bash
python -c "
import sys; before=set(sys.modules)
import shrap.research.hypothesis_generator.literature
new=set(sys.modules)-before
print(sorted({m.split('.')[0] for m in new} & {'numpy','pandas'}), len(new))"
```

`tests/research/test_import_weight.py` pins the boundary for the modules that
cross it. Adding a new cross-image import means adding a case there.

## 1e. Which container to run a CLI in follows its imports, not its subject

1d is about a container that crash-loops after a deploy. This is the same
dependency boundary hit from the other side, and it looks nothing like a deploy
problem: **you pick the wrong container for a one-shot CLI**, and it dies at
import before doing anything.

Hit on 2026-08-01 running `shrap-strategy-stage` in `tech-watcher`:

```
File ".../shrap/research/strategy_stage_cli.py", line 46, in <module>
    from shrap.research.strategy_evaluator.pipeline import DEFERRED_RULES, _rule_name
  ...
ModuleNotFoundError: No module named 'numpy'
```

The reasoning that produced it was subject-matter reasoning: every other
`shrap-*` CLI Mike runs by hand lives in `tech-watcher`, and staging a strategy
is a research action, so `tech-watcher` looked right. It is the wrong question.
`strategy_stage_cli` imports the Evaluator pipeline for its deferred-rules
check, so it needs numpy, so it needs an image that has numpy — **regardless of
what the command is about**.

**There is a second half to the trap.** `strategy-runner` also carries numpy, so
it survives the import — and then reads the wrong DSN. `_default_dsn()` looks
for `STRATEGY_SEED_POSTGRES_DSN` or `STRATEGY_EVALUATOR_POSTGRES_DSN` and falls
back to a hardcoded `shrap:shrap`. `strategy-runner` sets neither, so it lands
on the fallback and connects with credentials that may not be this deployment's.
A container has to satisfy **both** constraints: the imports and the env.

Measured 2026-08-01 with the 1d check:

| CLI | heavy deps | run it in |
|---|---|---|
| `shrap-strategy-stage` | numpy | `--profile tools run --rm strategy-evaluator` |
| `shrap-hypothesis-generate` | numpy | `--profile tools run --rm hypothesis-generator` |
| `shrap-strategy-evaluate` | numpy | `--profile tools run --rm strategy-evaluator` |
| `shrap-tech-watcher-promote` | pure | `exec tech-watcher` |
| `shrap-world-changer-observe` | pure | `exec tech-watcher` |
| `shrap-tech-watcher-refilter` | pure | `exec tech-watcher` |
| `shrap-literature-refilter` | pure | `exec tech-watcher` |
| `shrap-market-data-*backfill` | pure | `--profile tools run --rm market-data` |

Regenerate that column rather than trusting it — **one fresh interpreter per
module**:

```bash
for m in shrap.research.strategy_stage_cli shrap.research.tech_watcher.promotion; do
  uv run python -c "
import sys, importlib
importlib.import_module('$m')
print('$m', sorted({k.split('.')[0] for k in sys.modules} & {'numpy','pandas'}) or 'pure')"
done
```

**Do not loop inside one interpreter.** The obvious version of this check —
import each module in turn and diff `sys.modules` — is wrong, and wrong in the
direction that hides the problem. The first module to pull numpy leaves it
loaded, so every module measured after it reports `pure` no matter what it
imports. Written that way on 2026-08-01 it cleared `hypothesis_generator.cli`,
which the compose file has documented as numpy-dependent since 2026-07-30. The
result was an artifact of the loop order, not a property of the code.

**The rule:** before running a CLI in a container, ask what it *imports* and
what env vars its defaults *read* — never what it is about. A `pure` CLI runs
anywhere; a numpy one runs only in an image built from
`strategy-evaluator.Dockerfile` or `strategy-runner.Dockerfile`, and only where
the DSN it looks for is actually set.

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
