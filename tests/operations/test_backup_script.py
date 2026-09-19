"""The backup script must never leave a failed dump looking like a backup.

There had never been a backup when this was written (KI-034): no crontab entry
existed and `/mnt/backups` did not either. The runbook's four cron lines each
redirected with `>`, which creates the destination file whether or not the dump
succeeded — so the first thing to get right is that a file's *presence* in the
backup directory means it was checked.

`scripts/backup.sh` guards that in `publish()`. Everything else in the script
needs Docker and a live stack; this one function does not, and it is the part
whose failure is silent.
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backup.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")


def _publish(partial: Path, final: Path) -> subprocess.CompletedProcess[str]:
    """Source the script and call `publish` on a crafted file."""

    harness = f'set -uo pipefail; source "{SCRIPT}"; publish "{partial}" "{final}"'
    return subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=False)


def test_the_script_is_syntactically_valid() -> None:
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


def test_an_empty_dump_is_rejected_and_removed(tmp_path: Path) -> None:
    partial = tmp_path / "shrap-postgres-2026-08-25.sql.gz.partial"
    final = tmp_path / "shrap-postgres-2026-08-25.sql.gz"
    partial.touch()

    result = _publish(partial, final)

    assert result.returncode != 0
    assert not final.exists(), "an empty dump was published as a backup"
    assert not partial.exists(), "the rejected file was left behind"


def test_a_valid_but_empty_gzip_is_rejected(tmp_path: Path) -> None:
    """`gzip -t` passes on an empty archive, so integrity alone proves nothing.

    This is the case that would have bitten: `pg_dumpall -U ""` fails, the
    pipeline still produces a well-formed ~20-byte gzip, and `ls` shows a file
    that looks exactly like a backup.
    """

    partial = tmp_path / "shrap-postgres-2026-08-25.sql.gz.partial"
    final = tmp_path / "shrap-postgres-2026-08-25.sql.gz"
    partial.write_bytes(gzip.compress(b""))

    result = _publish(partial, final)

    assert result.returncode != 0
    assert not final.exists()
    assert "failed dump" in result.stderr.lower() or "under" in result.stderr.lower()


def test_a_corrupt_archive_of_plausible_size_is_rejected(tmp_path: Path) -> None:
    partial = tmp_path / "shrap-redis-2026-08-25.tar.gz.partial"
    final = tmp_path / "shrap-redis-2026-08-25.tar.gz"
    partial.write_bytes(b"\x00" * 4096)

    result = _publish(partial, final)

    assert result.returncode != 0
    assert not final.exists()
    assert "gzip" in result.stderr.lower()


def test_a_real_dump_is_published(tmp_path: Path) -> None:
    # Incompressible content on purpose. Repeated SQL gzips to a few hundred
    # bytes and trips the size floor, which is the floor working correctly.
    partial = tmp_path / "shrap-postgres-2026-08-25.sql.gz.partial"
    final = tmp_path / "shrap-postgres-2026-08-25.sql.gz"
    partial.write_bytes(gzip.compress(os.urandom(8192)))

    result = _publish(partial, final)

    assert result.returncode == 0, result.stderr
    assert final.exists()
    assert not partial.exists(), "the .partial should be renamed, not copied"


def test_publishing_is_atomic_in_name(tmp_path: Path) -> None:
    """Nothing carries the final name until it has passed both checks.

    A half-written file under the real name is the failure mode that makes a
    restore fail at the worst possible moment, so the name itself is the signal.
    """

    partial = tmp_path / "shrap-postgres-2026-08-25.sql.gz.partial"
    final = tmp_path / "shrap-postgres-2026-08-25.sql.gz"
    partial.write_bytes(gzip.compress(b""))

    _publish(partial, final)

    assert list(tmp_path.iterdir()) == [], "a rejected run left a file behind"


def test_docker_command_is_overridable_for_hosts_without_a_docker_group(
    tmp_path: Path,
) -> None:
    """`DOCKER="sudo docker"` must split into two words, not one command name.

    The Dell does not put the deploying user in the `docker` group, so the
    first real run of this script died on a raw "permission denied ...
    docker.sock" from inside a dump stage. An array, not a bare string.
    """

    harness = f'set -uo pipefail; DOCKER="sudo docker"; source "{SCRIPT}"; '
    harness += 'printf "%s|" "${DOCKER_CMD[@]}"'
    result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "sudo|docker|"


def test_docker_command_defaults_to_plain_docker() -> None:
    harness = f'set -uo pipefail; source "{SCRIPT}"; printf "%s|" "${{DOCKER_CMD[@]}}"'
    result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=False)

    assert result.stdout == "docker|"


def test_an_unknown_verifier_kind_is_refused(tmp_path: Path) -> None:
    """A typo in the `kind` argument must not silently publish unverified.

    The failure this guards is a future edit adding a dump type and misspelling
    its verifier — which would otherwise fall through and rename the file.
    """

    partial = tmp_path / "shrap-postgres-2026-08-25.dump.partial"
    final = tmp_path / "shrap-postgres-2026-08-25.dump"
    partial.write_bytes(gzip.compress(os.urandom(8192)))

    harness = f'set -uo pipefail; source "{SCRIPT}"; publish "{partial}" "{final}" nonsense'
    result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=False)

    assert result.returncode != 0
    assert not final.exists()
    assert "unknown verifier" in result.stderr.lower()


def test_the_size_floor_is_overridable_per_dump(tmp_path: Path) -> None:
    """Globals are a few hundred bytes; the shared floor would reject them."""

    partial = tmp_path / "shrap-postgres-globals-2026-08-25.sql.gz.partial"
    final = tmp_path / "shrap-postgres-globals-2026-08-25.sql.gz"
    # Distinct role names, so it compresses like a real globals dump rather
    # than like eight copies of one line.
    roles = "".join(f"CREATE ROLE shrap_{os.urandom(6).hex()} LOGIN;\n" for _ in range(12))
    partial.write_bytes(gzip.compress(roles.encode()))

    harness = f'set -uo pipefail; source "{SCRIPT}"; publish "{partial}" "{final}" gzip 64'
    result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    assert final.exists()


# --- an empty Qdrant is a skip, not a failure ---------------------------------
#
# This distinction cost eighteen days of backups. Qdrant has held zero
# collections since 2026-07-02 — the architecture specifies "full text to
# Qdrant" and that leg was never wired — so its tarball was 386 bytes,
# `publish`'s floor correctly rejected it, and `fail` exited 1. Every run ended
# non-zero *after* postgres, langfuse and redis had succeeded and *before*
# retention pruning ran. The only signal an operator has said "failed", every
# time, for a reason that did not matter.


def _dump_qdrant(collections: int, dest: Path) -> subprocess.CompletedProcess[str]:
    """Run `dump_qdrant` with Docker and the collection count stubbed out.

    Stubs rather than mocks a daemon: the function's decision depends on two
    things it asks the outside world for, and this pins the decision, not the
    plumbing.
    """

    harness = f"""
        set -uo pipefail
        source "{SCRIPT}"
        DEST="{dest}"
        STAMP="2026-09-19"
        DOCKER_CMD=(true)                       # `docker inspect` succeeds
        volume_for() {{ printf 'infra_qdrant_storage'; }}
        qdrant_collection_count() {{ printf '{collections}'; }}
        publish() {{ printf 'PUBLISH CALLED\\n'; }}
        dump_qdrant
    """
    return subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=False)


def test_an_empty_qdrant_is_skipped_not_failed(tmp_path: Path) -> None:
    """Zero collections must exit 0, so the run reaches retention pruning."""

    result = _dump_qdrant(0, tmp_path)

    assert result.returncode == 0, f"an empty Qdrant failed the run: {result.stderr}"
    assert "holds no collections" in result.stdout
    assert "PUBLISH CALLED" not in result.stdout, "archived a volume with nothing in it"


def test_a_populated_qdrant_is_still_archived_and_verified(tmp_path: Path) -> None:
    """The size floor is unchanged the moment a collection exists.

    The skip must not become a permanent excuse: once Qdrant holds anything,
    a 386-byte tarball is once again a failed dump.
    """

    result = _dump_qdrant(3, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "3 collection(s)" in result.stdout
    assert "PUBLISH CALLED" in result.stdout, "a populated Qdrant skipped verification"


def test_qdrant_absent_entirely_is_also_a_skip(tmp_path: Path) -> None:
    """Pre-existing behaviour, pinned so the new branch does not displace it."""

    harness = f"""
        set -uo pipefail
        source "{SCRIPT}"
        DEST="{tmp_path}"
        DOCKER_CMD=(false)                      # `docker inspect` fails
        dump_qdrant
    """
    result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=False)

    assert result.returncode == 0
    assert "is not running" in result.stdout


def test_retention_prune_is_reachable_after_qdrant(tmp_path: Path) -> None:
    """The prune sits after `dump_qdrant` in `main`, so a failure there hid it.

    Asserted on the ordering in the source rather than by running `main`, which
    needs a live stack. The point is that nothing between the last real dump and
    the prune may exit non-zero on a condition that is not an error.
    """

    body = SCRIPT.read_text()
    qdrant_at = body.index("    dump_qdrant\n")
    prune_at = body.index("pruning backups older than")

    assert qdrant_at < prune_at, "test assumes dump_qdrant precedes the prune"
    assert "skip: $container holds no collections" in body
