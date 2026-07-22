"""Failure-behaviour coverage for the backup/restore/verify shell scripts.

These orchestration scripts (``deploy/backup.sh``, ``restore.sh``,
``verify-restore.sh``) exist to prevent silent data loss: a failed ``pg_dump``
masked by a successful ``gzip``, a restore from an unverifiable or truncated
dump, and a scratch database leaked when verification fails. Each is exercised
here with fake ``pg_dump``/``psql``/``createdb``/``dropdb``/``python`` binaries
on ``PATH``, so no real Postgres is needed and the checks stay fast and
deterministic.

Requires a POSIX ``sh``; skipped on non-POSIX hosts (the scripts ship inside the
``postgres:16-alpine`` image and run in CI on Linux).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_SH = shutil.which("sh") or shutil.which("bash")
pytestmark = pytest.mark.skipif(
    os.name != "posix" or _SH is None, reason="requires a POSIX sh"
)

_DEPLOY = Path(__file__).resolve().parents[4] / "deploy"


def _sidecar(dump: Path) -> Path:
    """The ``.sha256`` checksum file that sits next to a dump."""
    return dump.with_name(dump.name + ".sha256")


class Sandbox:
    """A throwaway backup dir with fake command-line tools on ``PATH``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.bin = root / "bin"
        self.backups = root / "backups"
        self.bin.mkdir()
        self.backups.mkdir()
        # Default fakes; individual tests override the ones they care about.
        self.fake("pg_dump", 'echo "CREATE TABLE t(x integer);"\n')
        self.fake("psql", 'echo "psql $*" >> "$SANDBOX/psql.log"\n')
        self.fake("createdb", ":\n")
        self.fake("dropdb", 'echo "dropdb $*" >> "$SANDBOX/dropdb.log"\n')

    def fake(self, name: str, body: str) -> None:
        """(Re)write an executable fake ``name`` running ``body`` under sh."""
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    def run(
        self, script: str, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run a deploy script with the fakes on ``PATH`` and a scratch env."""
        full_env = dict(os.environ)
        full_env["PATH"] = str(self.bin) + os.pathsep + full_env.get("PATH", "")
        full_env["SANDBOX"] = str(self.root)
        full_env["BACKUP_DIR"] = str(self.backups)
        full_env["PGDATABASE"] = "testdb"
        if env:
            full_env.update(env)
        assert _SH is not None  # guaranteed by pytestmark
        return subprocess.run(
            [_SH, str(_DEPLOY / script), *args],
            env=full_env,
            capture_output=True,
            text=True,
        )

    def dumps(self) -> list[Path]:
        return sorted(self.backups.glob("tokemetry-*.sql.gz"))

    def log(self, name: str) -> str:
        path = self.root / f"{name}.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path)


def test_backup_success_publishes_dump_and_checksum(sandbox: Sandbox) -> None:
    result = sandbox.run("backup.sh")
    assert result.returncode == 0, result.stderr
    dumps = sandbox.dumps()
    assert len(dumps) == 1
    assert _sidecar(dumps[0]).exists()


def test_backup_aborts_when_pg_dump_fails(sandbox: Sandbox) -> None:
    # pg_dump fails after emitting partial output that still gzips cleanly.
    sandbox.fake("pg_dump", 'echo "-- partial"; exit 2\n')
    result = sandbox.run("backup.sh")
    assert result.returncode != 0
    # Nothing is published and no scratch/temp file is left behind.
    assert list(sandbox.backups.iterdir()) == []


def test_restore_refuses_missing_checksum(sandbox: Sandbox) -> None:
    sandbox.run("backup.sh")
    dump = sandbox.dumps()[0]
    _sidecar(dump).unlink()
    result = sandbox.run("restore.sh", str(dump), env={"FORCE": "1"})
    assert result.returncode != 0
    assert "refusing" in (result.stdout + result.stderr).lower()
    assert sandbox.log("psql") == ""  # psql must never run


def test_restore_aborts_on_checksum_mismatch(sandbox: Sandbox) -> None:
    sandbox.run("backup.sh")
    dump = sandbox.dumps()[0]
    with dump.open("ab") as handle:
        handle.write(b"tampered")  # archive no longer matches the sidecar
    result = sandbox.run("restore.sh", str(dump), env={"FORCE": "1"})
    assert result.returncode != 0
    assert sandbox.log("psql") == ""


def test_restore_aborts_on_undecompressable_archive(sandbox: Sandbox) -> None:
    # Checksum matches (so that gate passes) but the archive is not valid gzip;
    # staged decompression must catch it before psql is invoked.
    sandbox.run("backup.sh")
    dump = sandbox.dumps()[0]
    dump.write_bytes(b"not a gzip stream")
    digest = hashlib.sha256(dump.read_bytes()).hexdigest()
    _sidecar(dump).write_text(f"{digest}  {dump.name}\n", encoding="utf-8")
    result = sandbox.run("restore.sh", str(dump), env={"FORCE": "1"})
    assert result.returncode != 0
    assert sandbox.log("psql") == ""


def test_restore_round_trip_runs_psql(sandbox: Sandbox) -> None:
    sandbox.run("backup.sh")
    dump = sandbox.dumps()[0]
    result = sandbox.run("restore.sh", str(dump), env={"FORCE": "1"})
    assert result.returncode == 0, result.stderr
    assert "psql" in sandbox.log("psql")


def test_verify_restore_refuses_missing_checksum(sandbox: Sandbox) -> None:
    sandbox.run("backup.sh")
    _sidecar(sandbox.dumps()[0]).unlink()
    result = sandbox.run("verify-restore.sh", env={"PGHOST": "h", "PGUSER": "u"})
    assert result.returncode != 0
    assert "refusing" in (result.stdout + result.stderr).lower()


def test_verify_restore_drops_scratch_db_when_verifier_fails(sandbox: Sandbox) -> None:
    sandbox.run("backup.sh")
    sandbox.fake("python", 'echo "verifier failed" >&2; exit 1\n')
    result = sandbox.run(
        "verify-restore.sh",
        env={"PGHOST": "h", "PGUSER": "u", "SCRATCH_DB": "scratch_x"},
    )
    assert result.returncode != 0  # the verifier's failure propagates
    assert "scratch_x" in sandbox.log("dropdb")  # cleanup trap still ran


def test_verify_restore_success_drops_scratch_db(sandbox: Sandbox) -> None:
    sandbox.run("backup.sh")
    sandbox.fake("python", ":\n")  # verifier passes
    result = sandbox.run(
        "verify-restore.sh",
        env={"PGHOST": "h", "PGUSER": "u", "SCRATCH_DB": "scratch_ok"},
    )
    assert result.returncode == 0, result.stderr
    assert "scratch_ok" in sandbox.log("dropdb")
