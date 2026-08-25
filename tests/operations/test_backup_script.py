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
